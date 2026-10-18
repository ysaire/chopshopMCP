"""mcp server: claude is the editor, these tools are its eyes, ears and hands in premiere."""

import functools
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:  # mcp 2.x
    from mcp.server.mcpserver import Image, MCPServer as Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server, Image
    from mcp.server.fastmcp.exceptions import ToolError

import captions
import cutList
import edl
import eyes
import grade
import graphics
import library
import maskGraft
import masks
import media
import mogrt
import perceive
import pipeline
import presets
import reference
import review
import sheets
import shotLog
import sketch
import storyboard
import style
import tracking
from bridgeClient import BridgeError, callPremiere
from config import cacheDir, loadConfig, mediaCacheDir, pluginVersion, promptsDir

startedAt = time.time()

playbookNames = {"core": "core", "craft": "craft", "unboxing": "unboxingAsmr", "asmr": "unboxingAsmr",
                 "gaming": "gaming", "podcast": "podcast", "interview": "podcast", "meme": "memeFast",
                 "fast": "memeFast", "premium": "premiumTech", "tech": "premiumTech", "review": "premiumTech",
                 "references": "references", "style": "references"}


def readPlaybook(name: str) -> str:
    path = promptsDir() / "playbooks" / f"{playbookNames.get(name, name)}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


server = Server("chopshopMCP", instructions=readPlaybook("core"))


def tool(fn):
    """mcp 2 hides the text of unexpected errors from claude: ours are messages meant for it."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as err:
            raise ToolError(str(err) or err.__class__.__name__) from err
    return server.tool()(wrapper)


jobs = {}
jobsLock = threading.Lock()


def startJob(key: tuple, work):
    with jobsLock:
        job = jobs.get(key)
        if job and job["status"] in ("running", "done"):
            return job
        job = {"status": "running", "progress": 0.0, "stage": "", "error": None, "result": None, "key": key}
        jobs[key] = job

    def onProgress(stage, pct=None, message=None):
        job["stage"] = message or stage
        if pct is not None:
            job["progress"] = round(pct, 3)

    def run():
        try:
            job["result"] = work(onProgress)
            job["status"] = "done"
        except Exception as err:
            job["error"] = str(err)
            job["status"] = "error"

    threading.Thread(target=run, daemon=True).start()
    return job


def waitJob(job: dict, waitSeconds: float):
    deadline = time.time() + max(0.0, waitSeconds)
    while job["status"] == "running" and time.time() < deadline:
        time.sleep(0.5)
    if job["status"] == "error":
        with jobsLock:
            jobs.pop(job["key"], None)
        raise RuntimeError(job["error"])
    return job


def transcriptJob(mediaPath: str, voiceTrack: int):
    def work(onProgress):
        cfg = loadConfig()
        cfg["voiceTrack"] = int(voiceTrack)
        return pipeline.getTranscript(mediaPath, cfg, onProgress)
    return startJob(("transcript", mediaPath, int(voiceTrack)), work)


def perceptionJob(mediaPath: str, voiceTrack: int, force: bool = False):
    def work(onProgress):
        cfg = loadConfig()
        cfg["voiceTrack"] = int(voiceTrack)
        return pipeline.getPerception(mediaPath, cfg, onProgress, force=force)
    key = ("perception", mediaPath, int(voiceTrack))
    if force:
        with jobsLock:
            if jobs.get(key, {}).get("status") == "done":
                jobs.pop(key)
    return startJob(key, work)


segmenters = {}
segmentersLock = threading.Lock()


def sharedSegmenter(quality: str | None = None):
    """matting models stay loaded between previews and builds."""
    quality = quality or loadConfig().get("cutoutQuality") or "auto"
    with segmentersLock:
        if quality not in segmenters:
            segmenters[quality] = masks.outlineSegmenter() if quality == "outline" else masks.Segmenter(quality)
        return segmenters[quality]


def checkMedia(mediaPath: str):
    if not os.path.isfile(mediaPath):
        raise ValueError(f"file not found: {mediaPath}")


def samePath(a: str, b: str) -> bool:
    norm = lambda p: os.path.normcase(os.path.normpath(p.replace("\\", "/")))
    return norm(a) == norm(b)


def contextFor(mediaPath: str) -> tuple:
    """(active sequence settings if the rush sits in it, project item name)."""
    try:
        ctx = callPremiere("getContext")
    except BridgeError:
        return {}, None
    seq = (ctx or {}).get("sequence")
    for item in (ctx or {}).get("media", []):
        if samePath(item.get("path", ""), mediaPath):
            inherited = {}
            if seq and (item.get("inSequence") or item.get("onTimeline")):
                inherited = {"ticksPerFrame": seq.get("ticksPerFrame"), "width": seq.get("width"), "height": seq.get("height")}
            return inherited, item.get("name")
    return {}, None


def buildsFile() -> Path:
    return cacheDir() / "builds.json"


def rememberBuild(sequenceId: str, record: dict):
    """what each built sequence came from, so later tools (voice over slots) can find it."""
    try:
        builds = json.loads(buildsFile().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        builds = {}
    builds[sequenceId] = dict(record, savedAt=time.time())
    keep = sorted(builds.items(), key=lambda kv: kv[1].get("savedAt", 0))[-60:]
    buildsFile().write_text(json.dumps(dict(keep)), encoding="utf-8")


def recallBuild(sequenceId: str) -> dict:
    try:
        return json.loads(buildsFile().read_text(encoding="utf-8"))[sequenceId]
    except (OSError, ValueError, KeyError):
        raise ValueError(f"no chopshopMCP build known for sequence {sequenceId}, build it with buildEdit first")


def requirePerception(mediaPath: str) -> dict:
    cached = perceive.loadPerception(mediaCacheDir(mediaPath))
    if not cached:
        raise RuntimeError("this rush is not analysed yet, call analyzeRush first")
    return cached


def pluginState() -> dict:
    """which plugin answers, and whether its files changed after this server started (claude desktop keeps the
    old code running until it is restarted)."""
    sidecar = Path(__file__).resolve().parent
    files = list(sidecar.glob("*.py")) + list((sidecar / "prompts").rglob("*.md"))
    newest = max((f.stat().st_mtime for f in files), default=0.0)
    out = {"version": pluginVersion, "sidecar": str(sidecar)}
    if newest > startedAt + 1.0:
        out["warning"] = "the plugin's files changed after this server started: restart Claude Desktop to load the latest tools"
    return out


@tool
def premiereContext() -> dict:
    """Active sequence (name, ticks per frame, size) and candidate rushes: selected timeline clips, project panel selection, then everything on the timeline; a selected bin lists what it holds (bin), truncated when more than 60 files were selected. Each media says its kind (video, audio, image, missing), its duration and size, and if it sits directly on the timeline (inSequence) or inside a nest. plugin says which chopshopMCP answers; when it carries a warning, tell the user before editing."""
    ctx = callPremiere("getContext")
    for entry in ctx.get("media") or []:
        entry.update(media.kindOf(entry["path"]))
    return dict(ctx, plugin=pluginState())


@tool
def getPlaybook(format: str = "core") -> str:
    """Editing playbook. core (tools and workflow), craft (how a good editor thinks, vocabulary, self review), references (learning from reference edits, style memory), and per genre: unboxing (also asmr, desk setups), gaming, podcast, meme (fast meme edits, JamFam / Finzar / Phyz style), premium (premium tech and unboxing, optimum style). Read craft once per session and the matching genre before planning an edit."""
    text = readPlaybook(format)
    if not text:
        raise ValueError("unknown playbook, use core, craft, references, unboxing, gaming, podcast, meme or premium")
    return text


@tool
def analyzeRush(mediaPath: str, voiceTrack: int = 1, waitSeconds: float = 40.0, force: bool = False) -> dict:
    """Local analysis of a rush, done once and cached: motion, hard cuts, per-track sound levels and transients, speech detection, beats, thumbnails. Works on voiceless footage. If status is running, call again. voiceTrack is the audio track to check for speech (1 = A1)."""
    checkMedia(mediaPath)
    job = waitJob(perceptionJob(mediaPath, voiceTrack, force), waitSeconds)
    if job["status"] == "running":
        return {"status": "running", "stage": job["stage"], "progress": job["progress"], "hint": "call analyzeRush again"}
    result = job["result"]
    probe = pipeline.getProbe(mediaPath)
    return {
        "status": "done",
        "duration": result["duration"],
        "fps": probe.get("fps"),
        "size": [probe.get("width"), probe.get("height")],
        "audioTracks": len(probe.get("audioStreams") or []),
        "stats": result["stats"],
        "next": "rushOverview, then viewSheet on active or speech stretches",
    }


@tool
def rushOverview(mediaPath: str, fromBeat: int = 0, limit: int = 300) -> str:
    """Beat table of an analysed rush (idle stretches folded), annotated with your shot log. Use it to decide what to watch."""
    perception = requirePerception(mediaPath)
    return perceive.formatOverview(perception, shotLog.load(mediaCacheDir(mediaPath)), fromBeat, limit)


@tool
def viewSheet(mediaPath: str, start: float, end: float, every: float | None = None) -> list:
    """Contact sheet of the rush between start and end (source seconds): up to 20 timestamped thumbnails labelled with beat ids. every sets the spacing in seconds (default spreads 20 cells over the range)."""
    perception = requirePerception(mediaPath)
    sheet = sheets.contactSheet(perception, mediaCacheDir(mediaPath), float(start), float(end), every)
    cells = ", ".join(f"{c['time']:.1f}" for c in sheet["cells"])
    text = f"sheet {start:.1f}-{end:.1f}s, cells at {cells}"
    asked = sheet.get("asked") or []
    step = float(every) if every else (asked[1] - asked[0] if len(asked) > 1 else 0.0)
    if asked and step and asked[-1] + step < float(end) - 1e-6:
        text += f"\nstops at {asked[-1]:.1f} s of {float(end):.1f} ({len(asked)} cells max): call again from {asked[-1] + step:.1f}"
    thumbEvery = float(perception.get("thumbEvery") or 1.0)
    if every and float(every) < thumbEvery - 1e-6:
        text += f"\nthumbnails exist every {thumbEvery:.1f} s: lookAt for a precise frame"
    return [text, Image(path=sheet["path"])]


@tool
def lookAt(mediaPath: str, times: list[float], grid: bool = False) -> list:
    """Full-quality stills at the given source times (max 6), for details: logos, scores, faces, text on screen. grid=True overlays a 0.1 grid so you can read normalized coordinates for blur zones and subject boxes."""
    checkMedia(mediaPath)
    frames = eyes.grabFrames(mediaPath, times[:6], str(mediaCacheDir(mediaPath) / "frames"))
    out = []
    for f in frames:
        path = f["path"]
        if grid:
            from PIL import Image as PilImage
            path = path.replace(".jpg", "_grid.jpg")
            masks.drawGrid(PilImage.open(f["path"])).save(path, quality=85)
        out.append(Image(path=path))
    return out


@tool
def previewFrame(mediaPath: str, time: float, blur: list[dict] | None = None, title: dict | None = None,
                 format: str = "source", zoom: dict | None = None) -> list:
    """Exactly what the viewer will see at one source instant: blur zones applied, title (behind the subject if title.behind with title.subject.box), cropped to the format and zoom. Check every blur zone and every title here before buildEdit."""
    checkMedia(mediaPath)
    info = pipeline.getProbe(mediaPath)
    inherited, _ = contextFor(mediaPath)
    seqSize = edl.resolveFormat(format, info, inherited if format in (None, "source") else None)
    zones = [masks.normalizeZone(z, info["duration"]) for z in (blur or [])]
    normTitle = masks.normalizeTitle(title, 1e9) if title else None
    view = edl.normalizeView(zoom) if zoom else None
    outDir = mediaCacheDir(mediaPath) / "previews"
    outDir.mkdir(parents=True, exist_ok=True)
    segmenter = sharedSegmenter() if normTitle and normTitle["behind"] and normTitle["subjectBox"] else None
    path = masks.previewFrame(mediaPath, float(time), zones, normTitle, str(outDir / f"preview_{time:09.3f}.jpg"),
                              seqSize=seqSize, view=view, segmenter=segmenter)
    active = [z["label"] or f"zone {i}" for i, z in enumerate(zones) if masks.zoneActive(z, float(time))]
    return [f"preview at {time:.2f}s, format {seqSize[0]}x{seqSize[1]}, active blur zones: {', '.join(active) or 'none'}",
            Image(path=path)]


@tool
def checkTracking(mediaPath: str, start: float, end: float, box: list[float] | None = None,
                  quad: list[list[float]] | None = None) -> list:
    """Track before building: an object from `box` [x0, y0, x1, y1], or a flat surface (a screen) from `quad`
[[x, y] upper left, upper right, lower right, lower left], both normalized and true at `start` (source seconds).
Returns how many frames were lost and a sheet of 8 frames with the track drawn in red: check it sticks."""
    checkMedia(mediaPath)
    if quad:
        result, kind = tracking.trackQuad(mediaPath, float(start), float(end), quad), "quad"
    elif box:
        result, kind = tracking.trackBox(mediaPath, float(start), float(end), box), "box"
    else:
        raise ValueError("give box or quad")
    outDir = mediaCacheDir(mediaPath) / "previews"
    outDir.mkdir(parents=True, exist_ok=True)
    path = tracking.trackSheet(mediaPath, result["frames"], kind, str(outDir / f"track_{kind}_{start:09.3f}.jpg"))
    lost = len(result["lost"])
    return [f"{kind} tracked over {len(result['frames'])} frames, lost on {lost}"
            + (f" ({result.get('method')})" if result.get("method") else ""), Image(path=path)]


@tool
def transcribeMedia(mediaPath: str, fromId: int = 0, limit: int = 400, waitSeconds: float = 40.0,
                    voiceTrack: int = 1) -> dict:
    """Word-timed transcript split into numbered utterances (local whisper, background job: call again while running). Use on a rush with speech or on a voice over file."""
    checkMedia(mediaPath)
    job = waitJob(transcriptJob(mediaPath, voiceTrack), waitSeconds)
    if job["status"] == "running":
        return {"status": "running", "progress": job["progress"], "hint": "call transcribeMedia again"}
    transcript = job["result"]
    utterances = transcript["utterances"]
    page = [u for u in utterances if u["id"] >= fromId][: max(1, limit)]
    lines = [f"[{u['id']}] {u['start']:.2f}-{u['end']:.2f} | {u['text']}" for u in page]
    nextId = page[-1]["id"] + 1 if page and page[-1]["id"] + 1 < len(utterances) else None
    return {"status": "done", "language": transcript.get("language"), "utteranceCount": len(utterances),
            "transcript": "\n".join(lines), "nextFromId": nextId}


@tool
def layoutVoiceOver(path: str, dropIds: list[int] | None = None, pieces: list[dict] | None = None, at: float = 0.0,
                    gapMs: int = 150, removeFillers: bool = True, waitSeconds: float = 40.0) -> dict:
    """Where a voice over lands on the edit, piece by piece (output seconds) with what is said.
Voice first: transcribe it (transcribeMedia on the file), drop retakes by utterance id, and the rest is laid back to back
from `at`. Plan the shots so each piece is covered, then pass the same values as plan.voiceOver = {path, dropIds, at, gapMs, duck}.
Edit first: pieces = [{ids: [utterance ids], at: slot start}] from voiceOverSlots. duck (dB, default -14) lowers the rush under the voice."""
    checkMedia(path)
    job = waitJob(transcriptJob(path, 1), waitSeconds)
    if job["status"] == "running":
        return {"status": "running", "progress": job["progress"], "hint": "call layoutVoiceOver again"}
    spec = {"dropIds": dropIds or [], "pieces": pieces, "at": at, "gapMs": gapMs, "removeFillers": removeFillers}
    layout = pipeline.voiceOverLayout(path, spec, loadConfig())
    return {"status": "done", "length": layout["length"], "pieces": layout["pieces"], "words": len(layout["words"]),
            "planEntry": {"path": path, **{k: v for k, v in spec.items() if v not in (None, [], "")}}}


@tool
def voiceOverSlots(sequenceId: str, wordsPerSecond: float = 2.5) -> dict:
    """Edit first: stretches of a built sequence where nobody talks on the rush, with their length, a word budget
and what is on screen, to write a voice over script that fits. Read the script slot by slot to the user."""
    record = recallBuild(sequenceId)
    perception = perceive.loadPerception(mediaCacheDir(record["mediaPath"]))
    slots = pipeline.voiceOverSlots(record["timeline"], perception, wordsPerSecond)
    note = "" if perception else "rush not analysed, every stretch counted as free"
    return {"sequence": record.get("sequenceName"), "slots": slots, "note": note}


@tool
def timelineClips(track: str, sequenceId: str | None = None) -> dict:
    """Every clip of one track ("A3", "V2") with its media file and position, for instance a voice over recorded
straight into Premiere: pass its file to transcribeMedia and layoutVoiceOver."""
    return callPremiere("getClips", {"sequenceId": sequenceId, "targets": [{"track": track}]})


@tool
def detectSilences(mediaPath: str, thresholdDb: float | None = None, minSilenceMs: int = 350,
                   voiceTrack: int = 1) -> dict:
    """Dead-air ranges (source seconds) on one audio track, auto threshold from the room tone. Mostly useful for talk."""
    checkMedia(mediaPath)
    return pipeline.getSilences(mediaPath, thresholdDb, minSilenceMs, track=voiceTrack)


@tool
def logShots(mediaPath: str, entries: list[dict]) -> dict:
    """Save what you saw: entries = [{start, end, description, interest 0-10, tags[]}] in source seconds. Same range overwrites. Every later edit of this rush can reuse the log."""
    merged = shotLog.save(mediaCacheDir(mediaPath), entries)
    return {"saved": len(entries), "total": len(merged)}


@tool
def getShotLog(mediaPath: str) -> str:
    """Your shot log for this rush, in time order."""
    entries = shotLog.load(mediaCacheDir(mediaPath))
    return shotLog.formatLog(entries) if entries else "empty, watch with viewSheet then logShots"


def importBuilt(built: dict) -> dict:
    nestNames = list((built.get("nestNames") or {}).values()) or [e["name"] for e in built.get("matteEffects") or []]
    return callPremiere("importSequenceXml", {"xmlPath": built["xmlPath"], "sequenceName": built["sequenceName"],
                                              "nestNames": nestNames})


def runEffectJobs(built: dict, imported: dict, warnings: list, onProgress) -> list:
    """stabilization, corner pins: effects computed in python, set on the imported sequence or its nests."""
    nestIds = {n["name"]: n["id"] for n in imported.get("nests") or []}
    names = built.get("nestNames") or {}
    report = []
    for job in built.get("effectJobs") or []:
        sequenceId = imported.get("sequenceId") if job["sequence"] == "main" else nestIds.get(names.get(job["sequence"]))
        what = ", ".join(e["effect"] for e in job["effects"])
        if not sequenceId:
            warnings.append(f"{what}: sequence {job['sequence']} not found after import")
            continue
        onProgress("render", 1.0, f"{what} on {job['sequence']} V{job['track'] + 1} #{job['index']}")
        target = {"audio": False, "track": job["track"], "index": job["index"], "label": f"V{job['track'] + 1} #{job['index']}"}
        try:
            done = callPremiere("applyEffects", {"sequenceId": sequenceId, "targets": [target], "plans": [job["effects"]],
                                                 "mode": "add"}, timeout=600.0)
            report.append({"sequence": job["sequence"], "clip": target["label"], "effects": what, "ok": done["ok"],
                           "issues": done["clips"][0]["issues"] if done.get("clips") else []})
        except BridgeError as err:
            warnings.append(f"{what} failed: {err}")
    return report


def keyMattes(built: dict, imported: dict) -> dict:
    """track matte keys (and mosaic) inside the nests, through premiere's qe dom."""
    nestIds = {n["name"]: n["id"] for n in imported.get("nests") or []}
    nests = [dict(e, id=nestIds.get(e["name"])) for e in built["matteEffects"]]
    try:
        return callPremiere("applyMatteEffects", {"sequenceId": imported.get("sequenceId"), "nests": nests,
                                                  "matteOffset": loadConfig().get("matteOffset")}, timeout=300.0)
    except BridgeError as err:
        return {"ok": False, "error": str(err)}


def finishBuild(built: dict, imported: dict, warnings: list, onProgress) -> dict:
    """what the host does on an imported sequence once the keys are set: motion, sound, effects, presets."""
    motion = None
    if built["motions"] and imported.get("sequenceId"):
        motion = callPremiere("applyMotion", {"sequenceId": imported["sequenceId"], "clips": built["motions"]})
    audioReports = []
    for audioJob in (built.get("audioJobs") or []) if imported.get("sequenceId") else []:
        try:
            if audioJob["kind"] == "duck":
                report = callPremiere("duckAudio", {"sequenceId": imported["sequenceId"], "db": audioJob["db"],
                                                    "ranges": audioJob["ranges"], "trackList": audioJob["trackList"]})
            else:
                report = callPremiere("setVolume", {"sequenceId": imported["sequenceId"], "clips": audioJob["clips"]})
            audioReports.append(dict(report, kind=audioJob["kind"]))
        except BridgeError as err:
            warnings.append(f"{audioJob['kind']} on the sound failed: {err}")
    effectsDone = runEffectJobs(built, imported, warnings, onProgress)
    animated = []
    for job in built.get("presetJobs") or []:
        onProgress("render", 1.0, f"replaying {job['preset']} on {len(job['indexes'])} clips")
        try:
            targets = [{"track": f"V{job['track'] + 1}", "index": i} for i in job["indexes"]]
            animated.append(replayPreset(job["preset"], targets, imported.get("sequenceId")))
        except Exception as err:
            warnings.append(f"caption preset {job['preset']!r} not applied: {err}")
    return {"motion": motion, "audio": audioReports, "effects": effectsDone, "animated": animated}


def graftNativeMasks(mediaPath: str, built: dict, imported: dict, onProgress) -> dict:
    """native masks: save the project, graft the keyframed masks into a copy of it, import the final sequence
    from that copy under the plan's name, drop the stage. neither the dom nor qe can write a mask path."""
    stage, final = built["sequenceName"], built["finalName"]
    nestIds = {n["name"]: n["id"] for n in imported.get("nests") or []}
    names = built.get("nestNames") or {}
    grafts = []
    for g in built["maskGrafts"]:
        sequenceId = imported["sequenceId"] if g["sequence"] == "main" else nestIds.get(names.get(g["sequence"]))
        if not sequenceId:
            raise ValueError(f"sequence {g['sequence']} not found after import")
        grafts.append(dict(g, sequence=sequenceId))
    renames = {imported["sequenceId"]: final}
    for n in imported.get("nests") or []:
        renames[n["id"]] = n["name"].replace(stage, final, 1)
    onProgress("render", 1.0, "saving the project")
    saved = callPremiere("saveProject", {}, timeout=600.0)
    path = saved.get("path") or ""
    if not os.path.isfile(path):
        raise ValueError(f"premiere did not save the project at {path!r} (save it once by hand, then build again)")
    folder = mediaCacheDir(mediaPath) / "projects"
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / f"{pipeline.safeName(final)}_{int(time.time())}.prproj"
    onProgress("render", 1.0, f"grafting {len(grafts)} native masks into a copy of the project")
    report = maskGraft.graftProjectFile(path, str(copy), grafts, renames)
    newId = report["ids"][imported["sequenceId"]]
    onProgress("render", 1.0, "importing the masked sequence")
    stageIds = [imported["sequenceId"]] + [n["id"] for n in imported.get("nests") or []]
    done = callPremiere("importProjectSequences", {"path": str(copy), "sequenceIds": [newId], "open": newId, "drop": stageIds},
                        timeout=600.0)
    added = done.get("sequences") or []
    if newId not in {s["id"] for s in added}:
        named = [s for s in added if s["name"] == final]
        if not named:
            raise ValueError(f"premiere imported {len(added)} sequences from the copy but not \"{final}\": the stage is left as it was, the copy is {copy}")
        newId = named[0]["id"]
    nests = [{"name": s["name"], "id": s["id"]} for s in added if s["id"] != newId]
    dropped = done.get("dropped") or {"deleted": [], "left": [{"id": i} for i in stageIds]}
    return {"ok": True, "masks": report["masks"], "projectCopy": str(copy),
            "imported": {"sequenceId": newId, "sequenceName": final, "nests": nests},
            "stageDeleted": [s.get("name") or s.get("id") for s in dropped.get("deleted") or []],
            "stageLeft": [s.get("name") or s.get("id") for s in dropped.get("left") or []]}


@tool
def previewPlan(mediaPath: str, plan: dict) -> list:
    """Storyboard of a plan before building: one still per segment, framed for the format with zooms, blur and
titles applied, numbered with its output time, speed and note. Use it in propose mode to show an angle, and to
check pacing and framing before buildEdit."""
    checkMedia(mediaPath)
    info = pipeline.getProbe(mediaPath)
    inherited, _ = contextFor(mediaPath)
    needsSegmenter = any((seg.get("title") or {}).get("behind") for seg in plan.get("segments") or [])
    board = storyboard.storyboard(mediaPath, plan, info, mediaCacheDir(mediaPath) / "previews", inherited,
                                  segmenter=sharedSegmenter() if needsSegmenter else None)
    text = (f"{board['segments']} segments, {board['duration']:.1f} s, {board['format'][0]}x{board['format'][1]}"
            + (f", first {board['shown']} shown" if board["shown"] < board["segments"] else "")
            + ("; " + "; ".join(board["warnings"]) if board["warnings"] else ""))
    return [text, Image(path=board["path"])]


@tool
def buildEdit(mediaPath: str, plan: dict, waitSeconds: float = 40.0) -> dict:
    """Build a new Premiere sequence from an edit plan and open it. The source timeline is untouched. For a first cut
or a new version only: notes on a built sequence go through readEdit and editSequence, which keep the user's changes.
plan = {name, format: source|vertical|square|portrait, masks?: native|matte|baked,
        blur: [{box: [x0, y0, x1, y1], from, to, track?, shape?: outline|rect|ellipse (outline when tracked), style?: blur|pixelate, strength?, label}]  (source seconds, normalized source coords),
        segments: [{start, end, speed?, audio?, zoom?, note?,
                    title?: {text, from?, to? (seconds into the segment), x?, y?, size?, color?, stroke?, font?,
                             behind?: true, subject: {box: [x0, y0, x1, y1], track?: true}}}],
        captions?: {style: pop|karaoke|line|a configured style, preset?: animation preset id, cues?: [{start, end, text}]},
        screens?: [{quad: [[x, y] upper left, upper right, lower right, lower left] at `from`, from, to (source seconds), media: image or video path}],
        voiceOver?: (see layoutVoiceOver)}
segment extras: stabilize?: true | {smoothness 0-1, method: transform|warp},
                attach?: [{text | image, box: [x0, y0, x1, y1] (the object at `from`), from?, to? (seconds into the segment),
                           anchor?: above|below|left|right|center, size?, color?, preset?}]  (a label riding on a tracked object)
several files: sources?: {alias: path} at plan level. segment media?: alias or path (default the rush): the segment
  cuts that file instead (a second camera, a meme clip as a smash cut); a picture takes duration instead of start/end.
  jcut?: seconds the segment's sound starts before its picture, lcut?: seconds its sound runs on over the next shot.
  cover?: [{media, start?, end? | duration? (in that file), from? (seconds into the segment), speed?,
            audio?: mute (default for video: b-roll under the voice) | mix (both heard) | duck (the cut's sound dips,
            duck dB, default -12) | replace (the cut's sound is cut while it plays), volume? (dB on the cover's sound),
            box?: [x0, y0, x1, y1] picture in picture in the output frame, fit?: fill|fit, zoom? (full frame only), note?}]
  laid over the segment on its own track, may run past the segment's end. audio-only files are sound effects or music.
  blur zones, screens and titles behind the subject follow the main rush only.
captions without cues follow the rush's speech mapped onto the edit (listCaptionStyles shows styles and pop-in presets).
speed > 1 is muted. masks = native (default): each blur or pixelate zone is a duplicate of the rush inside a "(chop comp)"
nest with its effect limited by a keyframed Premiere mask (editable with the pen), grafted into a saved copy of the
project and imported back under the plan's name; maskReport says what happened, outlines which model segmented
the tracked zones (video = SAM 2 with memory, fast = MobileSAM frame by frame). matte: only mattes are rendered,
Premiere blurs and keys the original footage inside the "(chop comp)" nest. Titles behind the subject always use a
"(chop subject)" nest keyed by a rendered matte. If Premiere refuses the keys or the graft, the edit is rebuilt in
baked or matte mode automatically. Renders run in the background: if status is rendering, call again with the same
plan. Returns the sequence id and where each segment landed (output seconds) for captions."""
    checkMedia(mediaPath)
    inherited, itemName = contextFor(mediaPath)
    key = ("edit", mediaPath, json.dumps(plan, sort_keys=True, default=str))

    def work(onProgress):
        def rebuild(name: str, mode: str) -> tuple:
            again = pipeline.buildEdit(mediaPath, dict(plan, name=name), inherited, sourceName=itemName,
                                       onProgress=onProgress, maskMode=mode, segmenter=sharedSegmenter)
            return again, importBuilt(again)

        built = pipeline.buildEdit(mediaPath, plan, inherited, sourceName=itemName, onProgress=onProgress,
                                   segmenter=sharedSegmenter)
        warnings = list(built["warnings"])
        imported = importBuilt(built)
        matte = None
        if built.get("matteEffects"):
            onProgress("render", 1.0, "setting effects and track matte keys in premiere")
            matte = keyMattes(built, imported)
            if not matte.get("ok"):
                failed = imported.get("sequenceName") or built["sequenceName"]
                onProgress("render", 0.0, "track matte keys refused, rebuilding with baked masks")
                built, imported = rebuild(f"{built['finalName']} (baked)", "baked")
                warnings.append(f"premiere refused the track matte keys in \"{failed}\", so masks were baked into "
                                f"\"{built['sequenceName']}\" instead. matteReport says why, diagnoseEffects shows what premiere has")
        done = finishBuild(built, imported, warnings, onProgress)
        graft = None
        if built.get("maskGrafts") and imported.get("sequenceId"):
            try:
                graft = graftNativeMasks(mediaPath, built, imported, onProgress)
                imported = graft.pop("imported")
            except (BridgeError, ValueError, OSError) as err:
                stage = imported.get("sequenceName") or built["sequenceName"]
                onProgress("render", 0.0, "native masks refused, rebuilding with rendered mattes")
                built, imported = rebuild(f"{built['finalName']} (matte)", "matte")
                if built.get("matteEffects"):
                    matte = keyMattes(built, imported)
                done = finishBuild(built, imported, warnings, onProgress)
                warnings.append(f"native masks could not be grafted into the project ({err}): \"{stage}\" keeps the effects "
                                f"without masks, the edit was rebuilt with rendered mattes as \"{built['sequenceName']}\"")
                graft = {"ok": False, "error": str(err)}
        duck = next((r for r in done["audio"] if r["kind"] == "duck"), None)
        audioReports, effectsDone, animated = done["audio"], done["effects"], done["animated"]
        result = {
            "status": "done",
            "sequenceId": imported.get("sequenceId"),
            "sequenceName": imported.get("sequenceName") or built["sequenceName"],
            "maskMode": built["maskMode"],
            "format": built["format"],
            "duration": built["duration"],
            "timeline": built["timeline"],
            "layers": built["layers"],
            "motion": done["motion"],
            "warnings": warnings,
        }
        if matte is not None:
            result["matteReport"] = matte
        if graft is not None:
            result["maskReport"] = graft
        if animated:
            result["presetReport"] = [{"preset": a["preset"], "clips": len(a["clips"]), "ok": a["report"]["ok"]} for a in animated]
        if effectsDone:
            result["effectReport"] = effectsDone
        if built.get("outlines"):
            result["outlines"] = built["outlines"]
        if built.get("voiceOver"):
            result["voiceOver"] = built["voiceOver"]
            result["duckReport"] = duck
        if audioReports:
            result["audioReport"] = audioReports
        if len(built.get("media") or {}) > 1:
            result["media"] = built["media"]
        if len(built.get("audioTracks") or []) > 1:
            result["audioTracks"] = built["audioTracks"]
        if imported.get("sequenceId"):
            rememberBuild(imported["sequenceId"], {"mediaPath": mediaPath, "plan": plan, "timeline": built["timeline"],
                                                   "voiceOver": built.get("voiceOver"), "sequenceName": result["sequenceName"],
                                                   "media": built.get("media")})
        if built.get("matting"):
            result["matting"] = built["matting"]
        return result

    job = waitJob(startJob(key, work), waitSeconds)
    if job["status"] == "running":
        return {"status": "rendering", "stage": job["stage"], "progress": job["progress"],
                "hint": "call buildEdit again with the same plan"}
    with jobsLock:
        jobs.pop(key, None)  # the same plan can be rebuilt later
    return job["result"]


@tool
def refineMask(sequenceId: str | None = None, track: int | None = None, index: int | None = None, inPoint: float | None = None,
               outPoint: float | None = None, effect: str | None = None, waitSeconds: float = 40.0) -> dict:
    """Refine a mask the user sketched by hand. They draw a rough pen mask on the Mosaic or Gaussian Blur of a clip
of the rush (on the frame where the object is whole), set the sequence's In and Out points on the stretch to hide,
select the clip, and this replaces the sketch with the object's silhouette keyed on every source frame of that
stretch: the sketch prompts SAM 2 on its first frame (every later key of the sketch re-registers the model on its
own frame), the project is saved, the mask's keys are replaced in a copy of it and the sequence comes back under
the same name (its id changes). The active sequence, its selected clip and its In/Out points by default; track
(0-based) and index name a clip instead, inPoint/outPoint (sequence seconds) stand in for the marks; without
marks the stretch is the span of the sketch's own keys. effect picks the effect when both carry a mask. Outside
the stretch the sketch's own keys stay and the mask is parked off screen where it said nothing. Renders in the
background: if status is rendering, call again with the same arguments. Check with sequenceFrame afterwards."""
    key = ("refine", sequenceId, track, index, inPoint, outPoint, effect)

    def work(onProgress):
        return sketch.refineSketch(callPremiere, sharedSegmenter("outline"), sequenceId=sequenceId, track=track, index=index,
                                   inPoint=inPoint, outPoint=outPoint, effect=effect, onProgress=onProgress)

    job = waitJob(startJob(key, work), waitSeconds)
    if job["status"] == "running":
        return {"status": "rendering", "stage": job["stage"], "progress": job["progress"],
                "hint": "call refineMask again with the same arguments"}
    with jobsLock:
        jobs.pop(key, None)
    return dict(job["result"], status="done")


def exportFramePng(sequenceId: str | None, seconds: float, pngPath: Path) -> tuple:
    """(premiere's answer, the frame as an RGB image): premiere writes the png after it answers."""
    from PIL import Image as PilImage
    pngPath.unlink(missing_ok=True)
    done = callPremiere("exportFrame", {"sequenceId": sequenceId, "seconds": float(seconds), "path": str(pngPath)}, timeout=120.0)
    deadline = time.time() + 15.0
    while time.time() < deadline:
        if pngPath.is_file():
            try:
                with PilImage.open(pngPath) as img:
                    return done, img.convert("RGB")
            except OSError:
                pass
        time.sleep(0.2)
    raise ValueError(f"premiere reported {done.get('timecode')} but wrote no frame at {pngPath}")


@tool
def sequenceFrame(time: float, sequenceId: str | None = None) -> list:
    """One frame of a built sequence exactly as Premiere renders it (effects, mattes, nests, titles included), at
time in sequence seconds. The active sequence by default. Use it after buildEdit to check a blur zone, a title
behind the subject or a transition before reporting."""
    outDir = cacheDir() / "frames"
    outDir.mkdir(parents=True, exist_ok=True)
    stem = f"seq_{(sequenceId or 'active')[:8]}_{float(time):09.3f}"
    pngPath = outDir / f"{stem}.png"
    done, image = exportFramePng(sequenceId, float(time), pngPath)
    # vision needs a small picture, the full png stays next to it
    image.thumbnail((960, 960))
    jpgPath = outDir / f"{stem}.jpg"
    image.save(jpgPath, quality=85)
    return [f"{done.get('sequence')} at {done.get('timecode')} ({time:.2f} s), full frame {pngPath}", Image(path=str(jpgPath))]


def sequenceNamed(sequenceId: str | None) -> tuple:
    """(id, name) of a sequence, the active one by default."""
    found = callPremiere("getClips", {"sequenceId": sequenceId, "targets": [{"track": "V1"}]})
    return found.get("sequenceId") or sequenceId, found.get("sequence") or "sequence"


def renderSequence(kind: str, sequenceId: str, path: str, inOut: bool, onProgress) -> dict:
    version = callPremiere("getContext").get("premiereVersion") or "25"
    preset = review.presetFor(kind, version)
    onProgress("render", 0.02, f"premiere renders the sequence with {Path(preset).name}")
    done = callPremiere("exportSequence", {"sequenceId": sequenceId, "path": path, "presetPath": preset,
                                           "range": "inout" if inOut else "entire"}, timeout=3600.0)
    if not done.get("written"):
        raise ValueError(f"premiere answered {done.get('answer')!r} but wrote nothing at {path}")
    return dict(done, preset=preset)


def dropRender(mp4: Path):
    for leftover in (mp4, mp4.with_suffix(".jpg")):
        leftover.unlink(missing_ok=True)
    shutil.rmtree(mp4.with_suffix(""), ignore_errors=True)


def freeName(target: Path) -> Path:
    n = 2
    candidate = target
    while candidate.exists():
        candidate = target.with_name(f"{target.stem} {n}{target.suffix}")
        n += 1
    return candidate


def watched(job: dict, key: tuple, first: str) -> list:
    if job["status"] == "running":
        return [f"rendering: {job['stage']} ({job['progress']:.0%}). Call again with the same arguments."]
    with jobsLock:
        jobs.pop(key, None)
    r = job["result"]
    text = f"{first} {r['path']}\nsequence {r['sequence']}\npreset {r['preset']}\n{review.describe(r['report'])}"
    return [text, Image(path=r["sheet"])]


@tool
def watchEdit(sequenceId: str | None = None, inOut: bool = False, waitSeconds: float = 40.0) -> list:
    """Watch the whole edit the way a viewer gets it. Premiere renders the sequence (the active one by default,
only its In/Out range with inOut) through its own exporter with a low bitrate H.264 preset, then every frame and
sample of the render is read: the shots and their lengths (rhythm), black, frozen and silent stretches, loudness
against YouTube's -14 LUFS, and one sheet with a frame of every shot. Use it after every build or change, before
reporting. The render's file is in the answer: viewSheet, lookAt and transcribeMedia work on it for detail (a word
cut short, a caption, a flash frame). Renders in the background: while status is rendering, call again with the
same arguments."""
    seqId, seqName = sequenceNamed(sequenceId)
    key = ("watch", seqId, bool(inOut))

    def work(onProgress):
        folder = cacheDir() / "renders"
        folder.mkdir(parents=True, exist_ok=True)
        stem = (seqId or "active")[:8]
        old = sorted(folder.glob(f"{stem}_*.mp4"))
        mp4 = folder / f"{stem}_{int(time.time() * 1000)}.mp4"
        done = renderSequence("review", seqId, str(mp4), inOut, onProgress)
        for previous in old:
            dropRender(previous)
        report = review.scanMedia(str(mp4), onProgress)
        sheet = review.shotSheet(str(mp4), report["shots"], str(mp4.with_suffix(".jpg")))
        return {"path": str(mp4), "sequence": done.get("sequence") or seqName, "preset": done["preset"],
                "report": report, "sheet": sheet}

    return watched(waitJob(startJob(key, work), waitSeconds), key, "file")


@tool
def exportEdit(sequenceId: str | None = None, path: str | None = None, inOut: bool = False, waitSeconds: float = 40.0) -> list:
    """Export the finished edit for upload. Premiere renders the sequence (the active one by default, only its
In/Out range with inOut) with its high bitrate H.264 preset at the sequence's own size and frame rate (a vertical
edit stays vertical), by default into an "exports" folder next to the project, and never over an existing file
(a number is added). The master is then checked like watchEdit: shots, black, frozen and silent stretches,
loudness. Renders in the background: while status is rendering, call again with the same arguments."""
    seqId, seqName = sequenceNamed(sequenceId)
    key = ("export", seqId, path, bool(inOut))

    def work(onProgress):
        if path:
            target = Path(path)
        else:
            project = (callPremiere("getContext").get("project") or {}).get("path")
            if not project:
                raise ValueError("the project has never been saved: save it once, or give a path")
            target = Path(project).parent / "exports" / f"{pipeline.safeName(seqName)}.mp4"
        target = freeName(target)
        done = renderSequence("master", seqId, str(target), inOut, onProgress)
        report = review.scanMedia(str(target), onProgress)
        folder = cacheDir() / "renders"
        folder.mkdir(parents=True, exist_ok=True)
        sheet = review.shotSheet(str(target), report["shots"], str(folder / f"master_{int(time.time() * 1000)}.jpg"))
        return {"path": str(target), "sequence": done.get("sequence") or seqName, "preset": done["preset"],
                "report": report, "sheet": sheet}

    return watched(waitJob(startJob(key, work), waitSeconds), key, "master")


def trackLines(state: dict) -> list:
    """one line per track that holds something: #index start-end [source in-out] file, +linked clips, then the
    gaps and the transitions."""
    lines = []
    for track in (state.get("video") or []) + (state.get("audio") or []):
        if not track.get("clips"):
            continue
        parts = []
        for c in track["clips"]:
            text = (f"#{c['index']} {c['start']:.2f}-{c['end']:.2f} [{c['inPoint']:.2f}-{c['outPoint']:.2f}] "
                    f"{Path(c.get('mediaPath') or '').name or c.get('name')}")
            if (c.get("speed") or 1) != 1:
                text += f" x{c['speed']:g}"
            if c.get("disabled"):
                text += " (off)"
            if c.get("linked"):
                text += " +" + " ".join(c["linked"])
            parts.append(text)
        line = f"{track['name']}: " + "; ".join(parts)
        if track.get("gaps"):
            line += " | gaps " + ", ".join(f"{a:.2f}-{b:.2f}" for a, b in track["gaps"])
        if track.get("transitions"):
            line += " | transitions " + ", ".join(f"{t['name']} {t['start']:.2f}-{t['end']:.2f}" for t in track["transitions"])
        lines.append(line)
    return lines


def editState(state: dict) -> dict:
    return {"sequenceId": state.get("sequenceId"), "name": state.get("name"), "duration": state.get("duration"),
            "marks": state.get("marks"), "tracks": trackLines(state)}


@tool
def readEdit(sequenceId: str | None = None) -> dict:
    """What a sequence really holds now, the user's own changes included (the active sequence by default): one
line per track, every clip as "#index start-end [source in-out] file", in sequence and source seconds, with the
clips linked to it (a shot's picture and its sound) and the gaps. Read it before changing a sequence the user may
have touched: the labels ("V1 #2") are what editSequence takes."""
    return editState(callPremiere("readSequence", {"sequenceId": sequenceId}))


@tool
def editSequence(operations: list[dict], sequenceId: str | None = None, copyAs: str | None = None) -> dict:
    """Change a sequence in place and keep everything the user did by hand: use it for every note after the
first build instead of building again. Each step moves a clip together with its linked sound. Steps run in order;
labels name the clips as readEdit shows them before the call:
  {op: "trim", clip: "V1 #2", in?, out? (source seconds, never moved by earlier steps), start?, end? (sequence
   seconds, read when the step runs), ripple?: true}: shorter or longer at the head or the tail; ripple (default)
   pulls or pushes everything after it on every track, false leaves a gap or needs the room
  {op: "remove", clip: "V1 #2", ripple?: true}: the shot and its sound, the gap closed
  {op: "insert", media: file path, or sameAs: "V1 #0" for the file a clip shows, in, out (source seconds),
   at (sequence seconds), track?: "V1"}: pushes every track from `at`
  {op: "overwrite", same fields}: covers what is there
  {op: "move", clip: "V1 #2", to: sequence seconds}: only onto free space
  {op: "transition", clip: "V1 #2", side?: "start" | "end", name?: "Cross Dissolve", duration?: 0.5,
   align?: "center" | "start" (begins at the cut) | "end" (ends at the cut), audio?: true, audioName?:
   "Constant Power"}: on the cut at that side of the clip, its linked sound gets the audio one unless audio is
   false; replaces what sat on that cut. Dip to Black / Dip to White for a change of section, Film Dissolve,
   Push, Slide... an unknown name lists them all
  {op: "removeTransition", at: sequence seconds, tracks?: ["V1", "A1"]}: every transition over that point
copyAs: work on a copy under that name, the user's version stays as it was. The first failing step stops the rest.
results say what each step did; spanning lists clips on other tracks that cross an edit point (music, a title)
and were left as they are. Check the result with watchEdit."""
    args = {"sequenceId": sequenceId, "ops": list(operations or [])}
    if copyAs:
        args["copyAs"] = copyAs
    done = callPremiere("editSequence", args, timeout=600.0)
    return dict(editState(done["state"]), results=done["results"])


@tool
def diagnoseEffects(sequenceId: str | None = None) -> dict:
    """Effects, parameter values and track names of every clip in a sequence (active one by default). Open a
hand-made track matte setup in Premiere and run this to see what the Matte popup value really is."""
    return callPremiere("diagnoseEffects", {"sequenceId": sequenceId} if sequenceId else {})


def presetLibrary() -> list:
    files = presets.libraryFiles(loadConfig().get("presetPaths") or [])
    if not files:
        raise RuntimeError("no preset file found: premiere keeps them in Documents/Adobe/Premiere Pro/<version>/"
                           "Profile-<name>/Effect Presets and Custom Items.prfpset, or add .prfpset files or folders "
                           "to presetPaths in config.json")
    return presets.loadLibrary(files, cacheDir())


@tool
def listPresets(query: str = "", limit: int = 150) -> str:
    """The user's Premiere effect presets (their profile plus presetPaths in config.json), one per line:
id | video or audio | duration and anchor (from the in point, from the out point, or stretched) | effects (animated params) | what can't be replayed.
query filters on folder and name (case-insensitive, all words must match). Use it to pick zooms, shakes, slides, flashes, caption pop-ins."""
    library = presetLibrary()
    words = [w for w in query.lower().split() if w]
    hits = [p for p in library if all(w in p["id"].lower() for w in words)]
    lines = [presets.summary(p) for p in hits[: max(1, limit)]]
    more = f"\n... {len(hits) - limit} more, narrow the query" if len(hits) > limit else ""
    return f"{len(hits)} of {len(library)} presets\n" + "\n".join(lines) + more


@tool
def describePreset(preset: str) -> dict:
    """Everything a preset does: each effect, static values, animated params with their key values and timing."""
    p = presets.find(presetLibrary(), preset)
    effects = []
    for e in p["effects"]:
        params = []
        for prm in e["params"]:
            if prm["kind"] == "arb":
                continue
            entry = {"name": prm["name"] or f"#{prm['index']}", "kind": prm["kind"]}
            if prm.get("keys"):
                entry["keys"] = [[round(k["t"] - (e["anchorOut"] if e["anchor"] == "out" else e["anchorIn"]), 3), k["v"]]
                                 for k in prm["keys"][:12]]
                if len(prm["keys"]) > 12:
                    entry["moreKeys"] = len(prm["keys"]) - 12
            else:
                entry["value"] = prm.get("value")
            params.append(entry)
        effects.append({"effect": e["displayName"], "anchor": e["anchor"], "params": params})
    return {"id": p["id"], "duration": p["duration"], "anchor": p["anchor"], "effects": effects, "issues": p["issues"],
            "note": "key times are seconds from the anchor (negative = before the out point)"}


def targetsArg(targets):
    return "selection" if targets in (None, "", "selection") else targets


@tool
def applyPreset(preset: str, targets: list[dict] | str = "selection", sequenceId: str | None = None) -> dict:
    """Rebuild one of the user's presets on clips, exactly: effects added by name, values set, eased curves
written as one keyframe per frame (Premiere's API can't set bezier handles).
targets = "selection" (clips selected in the timeline) or [{track: "V2", index: 0}] or [{track: "V1", at: 12.5}] (sequence seconds).
Anchored like the preset: from the clip's in point, from its out point, or stretched over the clip."""
    return replayPreset(preset, targetsArg(targets), sequenceId)


def replayPreset(preset: str, targets, sequenceId: str | None) -> dict:
    p = presets.find(presetLibrary(), preset)
    if p["audio"]:
        raise ValueError("audio presets can't be replayed by script yet: use setClipEffect with a named audio effect")
    info = callPremiere("getClips", {"sequenceId": sequenceId, "targets": targets})
    clips = [c for c in info["clips"] if not c["audio"]]
    if not clips:
        raise ValueError("no video clip among the targets")
    plans = [presets.replayPlan(p, c["inPoint"], c["outPoint"], info["fps"], c["speed"]) for c in clips]
    report = callPremiere("applyEffects", {"sequenceId": info["sequenceId"], "targets": clips, "plans": plans, "mode": "add"},
                          timeout=600.0)
    notes = sorted({n for plan in plans for spec in plan for n in spec.get("notes", [])})
    return {"preset": p["id"], "clips": [c["label"] for c in clips], "report": report, "presetIssues": p["issues"] + notes}


@tool
def clipEffects(targets: list[dict] | str = "selection", sequenceId: str | None = None) -> dict:
    """Every effect on clips with each parameter's value, whether it is animated and its key count.
targets as in applyPreset."""
    return callPremiere("clipEffects", {"sequenceId": sequenceId, "targets": targetsArg(targets)})


@tool
def setClipEffect(effect: str, values: dict | None = None, keys: dict | None = None,
                  targets: list[dict] | str = "selection", add: bool = False, sequenceId: str | None = None) -> dict:
    """Change an effect on clips, by its Effect Controls names (English UI): "Motion", "Opacity", "Transform", "Gaussian Blur", "Lumetri Color"...
values = {"Scale": 120, "Position": [0.5, 0.4], "Blurriness": 30}  (positions normalized 0-1, colors [a, r, g, b] 0-255)
keys = {"Scale": [[0, 100], [0.4, 115]]}  times in seconds from the clip's start on the timeline, linear between keys.
add=True adds the effect first (a new instance), otherwise the last instance on the clip is edited."""
    info = callPremiere("getClips", {"sequenceId": sequenceId, "targets": targetsArg(targets)})
    plans = []
    for c in info["clips"]:
        params = []
        for name, value in (values or {}).items():
            kind = "color" if isinstance(value, list) and len(value) == 4 else "value"
            params.append({"name": name, "kind": kind, "value": value})
        for name, pairs in (keys or {}).items():
            # timeline seconds from the clip start -> the clip's media time
            mapped = [[round(c["inPoint"] + float(t) * c["speed"], 6), v] for t, v in pairs]
            params.append({"name": name, "kind": "value", "keys": mapped})
        plans.append([{"effect": effect, "matchName": None, "intrinsic": effect.lower() in ("motion", "opacity"),
                       "audio": c["audio"], "params": params}])
    report = callPremiere("applyEffects", {"sequenceId": info["sequenceId"], "targets": info["clips"], "plans": plans,
                                           "mode": "add" if add else "update"}, timeout=300.0)
    return {"clips": [c["label"] for c in info["clips"]], "report": report}


def lumetriValues(sequenceId: str, index: int) -> dict | None:
    """exposure, temperature and tint of the clip's last Lumetri Color, None without one. The first parameter of
    each name is Basic Correction's: HSL Secondary has a Temperature and a Tint of its own further down."""
    info = callPremiere("clipEffects", {"sequenceId": sequenceId, "targets": [{"track": "V1", "index": index}]})
    found = [c for c in info["clips"][0]["components"] if "Lumetri" in (c.get("matchName") or "")]
    if not found:
        return None
    values = {}
    for name in grade.slopes:
        prop = next((p for p in found[-1]["properties"] if p.get("name") == name), None)
        values[name] = float(prop["value"]) if prop and isinstance(prop.get("value"), (int, float)) else 0.0
    return values


def groupFrames(sequenceId: str, group: list, tag: str) -> list:
    """two frames inside the shot, away from its edges and any transition, as floats 0-1."""
    import numpy as np
    folder = cacheDir() / "grade"
    folder.mkdir(parents=True, exist_ok=True)
    start, end = group[0]["start"], group[-1]["end"]
    frames = []
    for k, share in enumerate((0.3, 0.7)):
        _, image = exportFramePng(sequenceId, start + (end - start) * share, folder / f"{tag}_{k}.png")
        frames.append(np.asarray(image, dtype=np.float64) / 255)
    return frames


def differenceText(stats: dict, target: dict) -> str:
    gap = grade.gaps(stats, target)
    return f"{-gap['Exposure']:+.2f} stop, warm {-gap['Temperature']:+.3f}, green {-gap['Tint']:+.3f}"


@tool
def matchShots(reference: str | None = None, clips: list[str] | None = None, sequenceId: str | None = None,
               passes: int = 3, waitSeconds: float = 40.0) -> list:
    """Make the shots of a sequence look like one video: each V1 shot's brightness and colour cast is measured on
frames Premiere renders (its effects included) and its Lumetri Color's Exposure, Temperature and Tint are moved,
render after render, until it sits with the reference shot (the longest by default, or reference: "V1 #4").
clips: only those shots. Pieces cut with the razor from one continuous take count as one shot and get the same
values. The clip's last Lumetri Color is adjusted (one is added if it has none), so a look already on it stays;
a look for the whole video (Contrast, Saturation, Vibrance, Faded Film) goes on afterwards with setClipEffect.
Answers a line per shot (stops and cast against the reference, before and after, the values set) and a sheet,
each shot before and after. Runs in the background: while status is grading, call again with the same arguments."""
    seqId, _ = sequenceNamed(sequenceId)
    key = ("grade", seqId, reference, tuple(clips or ()), int(passes))

    def work(onProgress):
        state = callPremiere("readSequence", {"sequenceId": seqId})
        shots = ((state.get("video") or [{}])[0].get("clips")) or []
        if not shots:
            raise ValueError("no clip on V1 to grade")
        groups = grade.throughEdits(shots)
        labels = [grade.groupLabel(g) for g in groups]

        def holds(group, label):
            return any(f"V1 #{c['index']}" == label for c in group)

        ref = next((g for g in groups if reference and holds(g, reference)), None)
        if reference and ref is None:
            raise ValueError(f"no clip {reference} on V1: {', '.join(labels)}")
        if ref is None:
            ref = max(groups, key=lambda g: g[-1]["end"] - g[0]["start"])
        todo = [g for g in groups if g is not ref and (not clips or any(holds(g, c) for c in clips))]
        onProgress("grade", 0.02, f"measuring the reference {grade.groupLabel(ref)}")
        refFrames = groupFrames(seqId, ref, "ref")
        target = grade.mergeStats([grade.frameStats(f) for f in refFrames])
        lines = [f"reference {grade.groupLabel(ref)}: mean luma {target['luma']:.3f}, warm {target['warm']:+.3f}, "
                 f"green {target['green']:+.3f}"]
        rows = [(f"{grade.groupLabel(ref)} reference", refFrames[0], None)]
        for n, group in enumerate(todo):
            label = grade.groupLabel(group)
            onProgress("grade", 0.05 + 0.95 * n / len(todo), f"grading {label} ({n + 1}/{len(todo)})")
            have = {c["index"]: lumetriValues(seqId, c["index"]) for c in group}
            seen = []

            def render(values):
                if values is not None:
                    for c in group:
                        r = setClipEffect("Lumetri Color", values=values, targets=[{"track": "V1", "index": c["index"]}],
                                          add=have[c["index"]] is None, sequenceId=seqId)
                        issues = [i for clip in r["report"]["clips"] for i in clip["issues"]]
                        if issues:
                            raise RuntimeError(f"{label}: {'; '.join(issues)}")
                        have[c["index"]] = values
                frames = groupFrames(seqId, group, f"shot{n}")
                seen.append(frames[0])
                return frames

            start = have[group[0]["index"]] or {}
            done = grade.matchShot(render, start, target, passes)
            if done["passes"] == 0 and done["reached"]:
                lines.append(f"{label}: sits with the reference ({differenceText(done['before'], target)}), left as it is")
            else:
                v = done["values"]
                line = (f"{label}: {differenceText(done['before'], target)} -> {differenceText(done['after'], target)} "
                        f"with Lumetri Exposure {v['Exposure']:+.2f}, Temperature {v['Temperature']:+.1f}, "
                        f"Tint {v['Tint']:+.1f} ({done['passes']} render{'s' if done['passes'] != 1 else ''})")
                if not done["reached"]:
                    line += "; still off" + (f", {' and '.join(done['limited'])} at its limit" if done["limited"] else "")
                lines.append(line)
            rows.append((label, seen[0], seen[-1] if len(seen) > 1 else None))
        path = grade.sheet(rows, str(cacheDir() / "grade" / f"{seqId[:8]}_sheet.jpg"))
        return {"lines": lines, "sheet": path}

    job = waitJob(startJob(key, work), waitSeconds)
    if job["status"] == "running":
        return [f"grading: {job['stage']} ({job['progress']:.0%}). Call again with the same arguments."]
    with jobsLock:
        jobs.pop(key, None)
    return ["\n".join(job["result"]["lines"]), Image(path=job["result"]["sheet"])]


def graphicsFolder() -> Path:
    """"chopshop graphics" next to the project, so the renders travel with it."""
    project = (callPremiere("getContext").get("project") or {}).get("path")
    folder = Path(project).parent / "chopshop graphics" if project else cacheDir() / "graphics"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def frameFormat(state: dict) -> tuple:
    return int(state.get("width") or 1920), int(state.get("height") or 1080), float(state.get("fps") or 30)


def graphicReady():
    problem = graphics.ready()
    if problem:
        raise RuntimeError(problem)


@tool
def listGraphics() -> str:
    """The motion graphics templates of the plugin: name, length, what it shows, and each variable with its type
and default. addGraphic and previewGraphic take a template's name and values for its variables."""
    lines = []
    for t in graphics.listTemplates():
        lines.append(f"{t['name']} ({t['duration']:g} s): {t['description']}")
        for v in t["variables"]:
            lines.append(f"  {v['id']} ({v['type']}, default {json.dumps(v['default'], ensure_ascii=False)}): {v['label']}")
    accent = loadConfig().get("graphicsAccent")
    lines.append(f"house accent: {accent}" if accent else "house accent: none set (graphicsAccent in config.json), templates use their own")
    return "\n".join(lines)


@tool
def previewGraphic(template: str | None = None, html: str | None = None, values: dict | None = None,
                   at: float | None = None, duration: float | None = None, sequenceId: str | None = None,
                   assets: list[str] | None = None, waitSeconds: float = 40.0) -> list:
    """Look at a graphic before rendering it: three moments (entrance done, middle, just before it leaves) drawn by
HyperFrames at the sequence's size and laid over the sequence's own frames from `at` on, blended the way Premiere
blends an alpha clip: in linear light, so anything half transparent looks lighter than in a browser. Without at,
over mid grey. Same arguments as addGraphic. Takes 5 to 20 s in the background: while status is previewing, call
again with the same arguments."""
    import numpy as np
    graphicReady()
    seqId, seqName = sequenceNamed(sequenceId)
    text, defaultDuration, name = graphics.prepare(template, html, values, loadConfig().get("graphicsAccent"))
    length = float(duration or defaultDuration)
    key = ("previewGraphic", seqId, json.dumps([template, html, values, at, length, assets], sort_keys=True, default=str))

    def work(onProgress):
        width, height, fps = frameFormat(callPremiere("readSequence", {"sequenceId": seqId}))
        folder = cacheDir() / "graphics"
        stage = graphics.stage(graphics.compose(text, length, width, height, fps), folder / "preview", assets)
        times = [min(0.7, length * 0.25), length / 2, max(length - 0.6, length * 0.75)]
        onProgress("preview", 0.1, "hyperframes draws three frames")
        cells = []
        for k, (t, shot) in enumerate(zip(times, graphics.snapshot(stage, times))):
            if at is None:
                under = np.full(shot.shape[:2] + (3,), 0.45)
            else:
                _, image = exportFramePng(seqId, float(at) + t, folder / f"preview_under_{k}.png")
                under = np.asarray(image.resize((shot.shape[1], shot.shape[0])), dtype=np.float64) / 255
            cells.append((f"{t:.2f} s", graphics.composite(under, shot)))
        sheet = graphics.strip(cells, str(folder / "preview.jpg"))
        where = f"over {seqName} from {float(at):.2f} s" if at is not None else "over grey"
        return {"text": f"{name}, {length:.2f} s at {width}x{height}, {where}, blended in linear light like Premiere", "sheet": sheet}

    job = waitJob(startJob(key, work), waitSeconds)
    if job["status"] == "running":
        return [f"previewing: {job['stage']} ({job['progress']:.0%}). Call again with the same arguments."]
    with jobsLock:
        jobs.pop(key, None)
    return [job["result"]["text"], Image(path=job["result"]["sheet"])]


@tool
def addGraphic(at: float, template: str | None = None, html: str | None = None, values: dict | None = None,
               duration: float | None = None, track: str | None = None, name: str | None = None,
               sequenceId: str | None = None, assets: list[str] | None = None, waitSeconds: float = 40.0) -> list:
    """Put a motion graphic on the timeline. HyperFrames renders it at the sequence's size and frame rate to ProRes
4444 with alpha (20 to 40 s), into a "chopshop graphics" folder next to the project (never over a file), and it is
laid at `at` (sequence seconds) on `track`, or on the lowest video track above V1 that is free for its whole
duration, a new one on top when none is. Answers where it went and the frame Premiere renders in its middle.
template: a name from listGraphics, values: its variables, duration: seconds (the template's own by default).
html: a composition of your own instead, HyperFrames rules: a root element with data-composition-id, data-width,
data-height, data-duration; timed elements with class="clip" and data-start, data-duration; a paused GSAP timeline
registered in window.__timelines under the root's id; a transparent page. <link rel="stylesheet" href="chop.css">
and <script src="chop.js"> give the house look and sizes; files it shows go in assets, named by basename.
Premiere blends in linear light: keep panels solid. Runs in the background: while status is rendering, call again
with the same arguments."""
    graphicReady()
    seqId, seqName = sequenceNamed(sequenceId)
    text, defaultDuration, base = graphics.prepare(template, html, values, loadConfig().get("graphicsAccent"))
    length = float(duration or defaultDuration)
    start, end = float(at), float(at) + length
    key = ("addGraphic", seqId, json.dumps([template, html, values, at, length, track, name, assets], sort_keys=True, default=str))

    def work(onProgress):
        state = callPremiere("readSequence", {"sequenceId": seqId})
        existing = len(state.get("video") or [])
        where = track or graphics.freeTrack(state, start, end) or f"V{existing + 1}"
        fresh = int(where[1:]) > existing
        width, height, fps = frameFormat(state)
        folder = cacheDir() / "graphics"
        stage = graphics.stage(graphics.compose(text, length, width, height, fps), folder / "render", assets)
        out = freeName(graphicsFolder() / f"{name or base}.mov")
        began = time.time()
        onProgress("render", 0.05, f"hyperframes renders {out.name}")
        graphics.render(stage, out, fps)
        took = time.time() - began
        onProgress("place", 0.9, f"premiere lays {out.name} on {where}")
        done = callPremiere("editSequence", {"sequenceId": seqId, "ops": [
            {"op": "overwrite", "media": str(out), "in": 0, "out": length, "at": start, "track": where}]}, timeout=600.0)
        step = done["results"][0]
        if not step.get("ok"):
            raise RuntimeError(f"premiere could not lay {out.name}: {step.get('error')}")
        _, image = exportFramePng(seqId, start + length / 2, folder / "check.png")
        image.thumbnail((960, 960))
        jpg = folder / "check.jpg"
        image.save(jpg, quality=85)
        return {"text": f"{out.name} on {where} {start:.2f}-{end:.2f} of {seqName}{', a new track' if fresh else ''}\nfile {out}\n"
                        f"rendered in {took:.0f} s at {width}x{height}, {fps:g} fps; the frame is Premiere's, mid-graphic",
                "frame": str(jpg)}

    job = waitJob(startJob(key, work), waitSeconds)
    if job["status"] == "running":
        return [f"rendering: {job['stage']} ({job['progress']:.0%}). Call again with the same arguments."]
    with jobsLock:
        jobs.pop(key, None)
    return [job["result"]["text"], Image(path=job["result"]["frame"])]


@tool
def listMogrts(query: str = "") -> list:
    """The user's motion graphics templates (Premiere's local templates folder plus mogrtPaths in config.json),
with the controls each one exposes (text fields, colors, sliders)."""
    templates = mogrt.listTemplates(loadConfig().get("mogrtPaths") or [])
    q = query.lower()
    return [t for t in templates if q in t["name"].lower()] if q else templates


@tool
def addMogrt(template: str, at: float, duration: float, values: dict | None = None, track: str = "V2",
             sequenceId: str | None = None, preset: str | None = None) -> dict:
    """Place a motion graphics template on a sequence: at / duration in sequence seconds, track "V2"...,
values = {control label: value} (text swaps keep the template's style), preset = one of the user's presets
to replay on it (a pop in). Edit its controls later with setClipEffect(effect="mogrt", values=...)."""
    t = mogrt.find(mogrt.listTemplates(loadConfig().get("mogrtPaths") or []), template)
    placed = callPremiere("addMogrt", {"sequenceId": sequenceId, "path": t["path"], "at": float(at), "duration": float(duration),
                                       "track": int(str(track).lstrip("Vv")) - 1, "values": values or {}})
    if preset and placed.get("index", -1) >= 0:
        placed["preset"] = replayPreset(preset, [{"track": placed["track"], "index": placed["index"]}], sequenceId)
    return placed


@tool
def listCaptionStyles() -> dict:
    """Caption styles for buildEdit plan.captions: built-in (pop, karaoke, line), the user's own from captionStyles
in config.json, and caption-like presets from their library to animate each caption (pop ins, stretches)."""
    configured = loadConfig().get("captionStyles") or {}
    styles = {name: captions.normalizeStyle(name, configured) for name in list(captions.builtinStyles) + list(configured)}
    try:
        animations = [presets.summary(p) for p in presetLibrary()
                      if any(w in p["id"].lower() for w in ("subtitle", "caption", "pop", "text", "sous-titre"))][:40]
    except Exception as err:
        animations = [f"no preset library: {err}"]
    return {"styles": styles, "animationPresets": animations,
            "usage": "plan.captions = {style: name or {base, font, size, color, stroke, highlight, y, words, uppercase}, "
                     "preset: preset id (optional), cues: [{start, end, text}] in output seconds (optional, "
                     "default: the rush's speech mapped onto the edit)}"}


@tool
def addCaptions(mediaPath: str, sequenceId: str, captions: list[dict]) -> dict:
    """Premiere's native caption track (sober, editable as text in Premiere) on a built sequence. captions = [{start, end, text}] in output seconds (use the timeline from buildEdit). For animated captions in the user's style, use plan.captions in buildEdit instead."""
    srtPath = pipeline.writeCaptions(mediaPath, sequenceId, captions)
    result = callPremiere("importCaptions", {"srtPath": srtPath, "sequenceId": sequenceId})
    return dict(result, srtPath=srtPath, cues=len(captions))


@tool
def reframeVertical(sequenceId: str, numerator: int = 9, denominator: int = 16, preset: str = "default",
                    name: str | None = None) -> dict:
    """Premiere's own auto reframe (subject tracking) into a new sequence, 9:16 by default. preset: default, slower, faster."""
    return callPremiere("autoReframe", {"sequenceId": sequenceId, "numerator": numerator, "denominator": denominator,
                                        "preset": preset, "name": name})


@tool
def buildRoughCut(mediaPath: str, dropIds: list[int], sequenceName: str | None = None,
                  removeFillers: bool = True, padMs: int = 120, voiceTrack: int = 1) -> dict:
    """Talk only: new sequence from the transcript minus dropped utterance ids, dead air and filler words, every audio track kept."""
    checkMedia(mediaPath)
    inherited, itemName = contextFor(mediaPath)
    options = {"removeFillers": removeFillers, "padMs": padMs, "sequenceName": sequenceName,
               "voiceTrack": voiceTrack, "sourceName": itemName}
    options.update(inherited)
    result = pipeline.roughCut(mediaPath, mode="manual", options=options, dropIds=dropIds)
    imported = callPremiere("importSequenceXml", {"xmlPath": result["xmlPath"]})
    return {"stats": result["stats"], "sequence": imported}


@tool
def markTakes(mediaPath: str, drops: list[dict], voiceTrack: int = 1) -> dict:
    """Talk only, review mode: red markers on the active timeline over utterances you would cut. drops = [{id, reason, note}]."""
    checkMedia(mediaPath)
    job = waitJob(transcriptJob(mediaPath, voiceTrack), 0)
    if job["status"] != "done":
        raise RuntimeError("transcribe this rush first")
    markers = cutList.dropsToMarkers(job["result"]["utterances"], drops)
    return callPremiere("addMarkersForMedia", {"mediaPath": mediaPath, "markers": markers, "color": 1})


@tool
def clearReviewMarkers() -> dict:
    """Remove every chopshopMCP review marker from the active sequence."""
    return callPremiere("clearReviewMarkers")


@tool
def seek(seconds: float) -> dict:
    """Move the playhead of the active sequence (sequence seconds)."""
    return callPremiere("setPlayhead", {"seconds": seconds})


def libraryFolder(folder: str | None) -> str:
    if folder:
        return folder
    known = library.knownFolders()
    if len(known) == 1:
        return known[0]
    raise ValueError("say which folder: " + (", ".join(known) if known else "no library indexed yet, call indexLibrary"))


@tool
def indexLibrary(folder: str, transcribe: bool = True, waitSeconds: float = 40.0) -> dict:
    """Index a folder of clips the user keeps for edits (memes, reactions, b-roll, sound effects, music, pictures), sub folders included: length, a thumbnail, the words heard in clips up to 2 min. Done once, only new or changed files are redone. If status is running, call again. Then look at it with librarySheet and describe what you see with tagLibrary, so searchLibrary finds clips by what they show."""
    cfg = loadConfig()

    def transcriber(path):
        return " ".join(w["text"].strip() for w in pipeline.getTranscript(path, dict(cfg, voiceTrack=1))["words"])

    def work(onProgress):
        return library.summary(library.indexFolder(folder, transcribe, transcriber if transcribe else None, onProgress))
    job = waitJob(startJob(("library", os.path.abspath(folder), bool(transcribe)), work), waitSeconds)
    if job["status"] == "running":
        return {"status": "running", "stage": job["stage"], "progress": job["progress"], "hint": "call indexLibrary again"}
    with jobsLock:
        jobs.pop(("library", os.path.abspath(folder), bool(transcribe)), None)
    return dict(job["result"], status="done", next="librarySheet to see the clips, tagLibrary to describe them")


@tool
def librarySheet(folder: str | None = None, fromId: int = 0, count: int = 20, ids: list[int] | None = None) -> list:
    """Thumbnails of library clips (id, kind, length on each) with one line per clip: words heard, tags, description. Page with fromId, or pass ids (from searchLibrary) to compare candidates. count max 25."""
    folder = libraryFolder(folder)
    out = library.libraryDir() / library.folderKey(folder) / f"sheet_{int(fromId)}_{int(count)}.jpg"
    result = library.sheet(folder, out, int(fromId), max(1, min(25, int(count))), ids)
    text = "\n".join(result["rows"]) + (f"\nmore from id {result['next']}" if result["next"] is not None else "")
    return [text, Image(path=result["path"])]


@tool
def tagLibrary(folder: str | None, entries: list[dict]) -> dict:
    """Write what you saw on library clips: entries = [{id, description (what happens, the emotion, when to use it), tags (reaction, fail, win, awkward, hype, laugh, sad, shock, whoosh, hit, riser, music, b-roll...)}]. searchLibrary uses it."""
    return library.tag(libraryFolder(folder), entries)


@tool
def searchLibrary(query: str, kind: str | None = None, folder: str | None = None, limit: int = 20) -> list:
    """Find library clips by meaning: "shocked reaction", "sad violin", "whoosh", "crowd cheering", "typing b-roll". kind: video, image or audio. Returns paths to use as media in a plan (segment media or cover). Check candidates with librarySheet(ids=...)."""
    return library.search(query, folder, kind, max(1, min(100, int(limit))))


def referencesDir() -> Path:
    path = cacheDir() / "references"
    path.mkdir(parents=True, exist_ok=True)
    return path


def referenceJob(path: str, force: bool = False):
    def work(onProgress):
        return reference.loadOrBreakdown(path, mediaCacheDir(path), onProgress, force)
    key = ("reference", path)
    if force:
        with jobsLock:
            if jobs.get(key, {}).get("status") == "done":
                jobs.pop(key)
    return startJob(key, work)


def requireReference(path: str) -> dict:
    checkMedia(path)
    job = jobs.get(("reference", path))
    if job and job["status"] == "done":
        return job["result"]
    cached = mediaCacheDir(path) / "reference.json"
    if cached.exists():
        data = json.loads(cached.read_text(encoding="utf-8"))
        if data.get("version") == reference.analysisVersion:
            return data
    raise RuntimeError("this reference is not broken down yet, call breakdownReference first")


@tool
def fetchReference(url: str, cookiesFromBrowser: str | None = None, waitSeconds: float = 40.0) -> dict:
    """Download a reference edit (youtube, tiktok, instagram, x...) at 720p with its captions, on this machine. Only for public videos the user points you to. If status is running, call again. If the platform asks to sign in, retry with cookiesFromBrowser 'firefox' (or 'chrome' / 'edge' when closed)."""
    def work(onProgress):
        return reference.fetch(url, referencesDir(), cookiesFromBrowser, onProgress)
    job = waitJob(startJob(("fetch", url, cookiesFromBrowser), work), waitSeconds)
    if job["status"] == "running":
        return {"status": "running", "progress": job["progress"], "hint": "call fetchReference again"}
    return dict(job["result"], status="done", next="breakdownReference(path)")


@tool
def breakdownReference(path: str, waitSeconds: float = 40.0, force: bool = False) -> dict:
    """Measure how a reference edit is cut, frame by frame: cuts and their kind (hard cut, punch-in, jump cut, cut under a flash), shot lengths, pacing per 5 s, camera moves per shot (zoom, pan, shake), flashes, sound hits and how many cuts land on them, speech rate from captions. Returns the numbers, the shot table and a breakdown card to fill. Then watch it with referenceShots and lookAt, read the words with transcribeMedia, fill the card and saveBreakdown."""
    checkMedia(path)
    job = waitJob(referenceJob(path, force), waitSeconds)
    if job["status"] == "running":
        return {"status": "running", "stage": job["stage"], "progress": job["progress"], "hint": "call breakdownReference again"}
    data = job["result"]
    name = Path(path).stem
    return {
        "status": "done",
        "metrics": reference.summary(data),
        "shotTable": reference.shotTable(data, 0, 60),
        "card": reference.cardTemplate(name, data),
        "next": "referenceShots(path, fromShot) to see the shots, lookAt(path, times) for caption and title "
                "details, transcribeMedia(path) for the words when there are no captions, then saveBreakdown",
    }


@tool
def referenceShots(path: str, fromShot: int = 0, count: int = 20) -> list:
    """One thumbnail per shot of a broken down reference (its middle frame) from fromShot, with the matching rows of the shot table: the edit at a glance. count max 25."""
    data = requireReference(path)
    count = max(1, min(25, int(count)))
    out = mediaCacheDir(path) / "sheets" / f"shots_{int(fromShot):05d}_{count}.jpg"
    sheet = reference.shotSheet(data, out, int(fromShot), count)
    return [reference.shotTable(data, int(fromShot), count), Image(path=sheet["path"])]


@tool
def saveBreakdown(name: str, card: str, path: str | None = None, url: str | None = None,
                  format: str | None = None) -> dict:
    """Store a filled breakdown card in the style memory (with the measured numbers when path is given). format: unboxing, gaming, podcast, vlog, tutorial, music, ad, story... so getStyle can pick the right references later."""
    metrics = reference.summary(requireReference(path)) if path else None
    return style.saveBreakdown(name, card, metrics, url, format)


@tool
def getStyle(format: str | None = None, breakdown: str | None = None) -> dict:
    """The user's editing style memory: style.md (taste, pacing, captions, motion, sound, formats, never do) and the saved reference breakdowns (filtered by format). breakdown=name returns that full card. Read it before planning any edit."""
    if breakdown:
        return {"card": style.readBreakdown(breakdown)}
    return style.getStyle(format)


@tool
def rememberStyle(note: str, section: str = "taste") -> dict:
    """Add a rule to the user's style memory when they correct or praise something ('captions smaller and lower', 'never use whip transitions'). One rule per call, concrete, with numbers when possible. section: taste, pacing, captions, motion, sound, formats, never."""
    return {"section": style.remember(note, section)}


@server.prompt()
def chopshop(format: str = "unboxing", target: str = "45 s vertical flash edit and a tightened long version") -> str:
    """Edit the selected rush end to end."""
    return (
        f"Edit the rush selected in Premiere as a {format} video: {target}. "
        f"Follow the chopshopMCP core playbook and the {format} playbook (getPlaybook). "
        "Analyse it, watch the active stretches, log what you see, then build each edit and tell me what you kept and why."
    )


@server.prompt()
def reviewEdit(notes: str) -> str:
    """Revise the last edit with your notes."""
    return f"Revise the last chopshopMCP sequence with these notes, reuse the shot log, no re-analysis: {notes}"


if __name__ == "__main__":
    server.run()
