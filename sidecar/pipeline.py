"""rush in, cut plan out. shared by the panel cli and the mcp server."""

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

import cutList
import ears
import edl
import media
import perceive
import xmeml
from config import loadConfig, mediaCacheDir


def noop(stage, pct=None, message=None):
    pass


def readJson(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def writeJson(path: Path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def getProbe(mediaPath: str) -> dict:
    cachePath = mediaCacheDir(mediaPath) / "probe.json"
    cached = readJson(cachePath)
    if cached and "audioStreams" in cached:
        return cached
    info = media.probe(mediaPath)
    writeJson(cachePath, info)
    return info


def getSilences(mediaPath: str, thresholdDb=None, minSilenceMs: int = 350, onProgress=noop, track: int = 1) -> dict:
    key = f"silences_t{int(track)}_{thresholdDb if thresholdDb is not None else 'auto'}_{minSilenceMs}.json"
    cachePath = mediaCacheDir(mediaPath) / key
    cached = readJson(cachePath)
    if cached:
        return cached
    onProgress("silences", 0.0, "measuring audio levels")
    result = ears.detectSilences(mediaPath, thresholdDb, minSilenceMs, track=track)
    writeJson(cachePath, result)
    onProgress("silences", 1.0, f"{len(result['silences'])} silences, threshold {result['thresholdDb']} dB")
    return result


def getTranscript(mediaPath: str, cfg: dict, onProgress=noop) -> dict:
    lang = cfg.get("language") or "auto"
    track = int(cfg.get("voiceTrack") or 1)
    cachePath = mediaCacheDir(mediaPath) / f"transcript_t{track}_{lang}.json"
    cached = readJson(cachePath)
    if cached:
        onProgress("transcribe", 1.0, "transcript from cache")
        return cached
    onProgress("transcribe", 0.0, "loading whisper")
    started = time.time()
    result = ears.transcribe(
        mediaPath,
        language=lang,
        modelName=cfg.get("whisperModel", "auto"),
        device=cfg.get("whisperDevice", "auto"),
        keepFillers=True,
        onProgress=lambda pct: onProgress("transcribe", pct, None),
        track=track,
    )
    result["utterances"] = ears.buildUtterances(result["words"])
    result["seconds"] = round(time.time() - started, 1)
    writeJson(cachePath, result)
    onProgress("transcribe", 1.0, f"{len(result['words'])} words on {result['device']} ({result['model']})")
    return result


def getPerception(mediaPath: str, cfg: dict, onProgress=noop, force: bool = False) -> dict:
    cacheDir = mediaCacheDir(mediaPath)
    voiceTrack = int(cfg.get("voiceTrack") or 1)
    if not force:
        cached = perceive.loadPerception(cacheDir)
        if cached and cached.get("voiceTrack") == voiceTrack:
            onProgress("beats", 1.0, "analysis from cache")
            return cached
    info = getProbe(mediaPath)
    return perceive.perceive(mediaPath, cacheDir, info, voiceTrack=voiceTrack, onProgress=onProgress)


def orDefault(value, default):
    return default if value is None else value


def safeName(text: str) -> str:
    return re.sub(r"[^\w\- ]+", "_", text).strip() or "chopshopMCP"


def roughCut(mediaPath: str, mode: str = "silences", options: dict | None = None, onProgress=noop,
             dropIds: list | None = None) -> dict:
    """mode: manual (claude picked the drops through mcp), silences (no transcript)."""
    cfg = loadConfig()
    cfg.update({k: v for k, v in (options or {}).items() if v is not None})

    info = getProbe(mediaPath)
    duration = info["duration"]
    voiceTrack = int(cfg.get("voiceTrack") or 1)
    silences = getSilences(mediaPath, cfg.get("thresholdDb"), int(cfg.get("minSilenceMs", 350)), onProgress, voiceTrack)
    padSec = float(cfg.get("padMs", 120)) / 1000.0

    transcript = None
    utterances = None
    drops = []
    extraCuts = []

    if mode == "manual":
        transcript = getTranscript(mediaPath, cfg, onProgress)
        utterances = transcript["utterances"]
        if cfg.get("removeFillers"):
            extraCuts = ears.fillerRanges(transcript["words"], transcript.get("language"))
        drops = [{"id": int(i), "reason": "manual", "note": ""} for i in (dropIds or [])]

    dropIdList = [d["id"] for d in drops]
    keep = cutList.buildKeepRanges(
        duration,
        silences["silences"],
        utterances=utterances,
        dropIds=dropIdList,
        extraCuts=extraCuts,
        padSec=padSec,
    )

    result = {
        "mode": mode,
        "mediaPath": mediaPath,
        "probe": info,
        "keepRanges": keep,
        "drops": drops,
        "stats": {
            "sourceDuration": round(duration, 2),
            "keptDuration": round(cutList.totalLength(keep), 2),
            "segments": len(keep),
            "silences": len(silences["silences"]),
            "thresholdDb": silences["thresholdDb"],
            "audioStreams": len(info.get("audioStreams") or []),
            "voiceTrack": voiceTrack,
        },
    }

    seqFps = None
    if cfg.get("ticksPerFrame"):
        seqFps = xmeml.fpsFromTicks(cfg["ticksPerFrame"])
    baseName = Path(mediaPath.replace("\\", "/")).stem
    sourceName = cfg.get("sourceName") or baseName
    seqName = cfg.get("sequenceName") or f"{sourceName} [chop {mode}]"
    outPath = mediaCacheDir(mediaPath) / f"{safeName(seqName)}_{int(time.time())}.xml"
    onProgress("xml", 0.5, "writing sequence xml")
    xmeml.writeXmeml(
        str(outPath), mediaPath, keep, info, seqName,
        fps=seqFps, width=cfg.get("width"), height=cfg.get("height"), clipName=sourceName,
    )
    result["xmlPath"] = str(outPath)
    result["sequenceName"] = seqName
    onProgress("xml", 1.0, "sequence ready")
    return result


def getVoiceTranscript(path: str, cfg: dict, onProgress=noop) -> dict:
    """a voice over file is transcribed like a rush, on its first audio track."""
    return getTranscript(path, dict(cfg, voiceTrack=1), onProgress)


def voiceOverLayout(path: str, spec: dict, cfg: dict, onProgress=noop) -> dict:
    """where each kept piece of the voice lands on the edit (output seconds), with its words.

    spec.pieces = [{ids: [utterance ids] | start, end (voice seconds), at: output seconds}] places pieces by hand
    (edit first: one per slot). without pieces the voice is cleaned (dropIds, dead air, fillers) and laid
    back to back from spec.at (voice first).
    """
    info = getProbe(path)
    duration = float(info.get("duration") or 0)
    transcript = getVoiceTranscript(path, cfg, onProgress)
    utterances = transcript["utterances"]
    byId = {u["id"]: u for u in utterances}
    pad = float(spec.get("padMs", cfg.get("padMs", 120))) / 1000
    pieces = []
    cursor = float(spec.get("at", 0.0))
    if spec.get("pieces"):
        for raw in spec["pieces"]:
            if raw.get("ids"):
                chosen = [byId[i] for i in raw["ids"] if i in byId]
                if not chosen:
                    continue
                start, end = min(u["start"] for u in chosen) - pad, max(u["end"] for u in chosen) + pad
            else:
                start, end = float(raw["start"]), float(raw["end"])
            start, end = max(0.0, start), min(duration, end)
            at = float(raw.get("at", cursor))
            pieces.append({"start": round(start, 3), "end": round(end, 3), "at": round(at, 3)})
            cursor = at + end - start
    else:
        silences = getSilences(path, cfg.get("thresholdDb"), int(cfg.get("minSilenceMs", 350)), onProgress, 1)
        fillers = ears.fillerRanges(transcript["words"], transcript.get("language")) if spec.get("removeFillers", True) else []
        keep = cutList.buildKeepRanges(duration, silences["silences"], utterances, spec.get("dropIds") or [], fillers, pad)
        gap = float(spec.get("gapMs", 150)) / 1000
        for start, end in keep:
            pieces.append({"start": start, "end": end, "at": round(cursor, 3)})
            cursor += end - start + gap
    words = []
    for piece in pieces:
        for w in transcript["words"]:
            if w["start"] >= piece["start"] - 0.02 and w["end"] <= piece["end"] + 0.02:
                offset = piece["at"] - piece["start"]
                words.append({"text": w["text"].strip(), "start": round(w["start"] + offset, 3), "end": round(w["end"] + offset, 3)})
        piece["text"] = " ".join(w["text"].strip() for w in transcript["words"]
                                 if w["start"] >= piece["start"] - 0.02 and w["end"] <= piece["end"] + 0.02)
    length = max((p["at"] + p["end"] - p["start"] for p in pieces), default=0.0)
    return {"path": path, "pieces": pieces, "length": round(length, 3), "words": [w for w in words if w["text"]],
            "language": transcript.get("language")}


def duckRanges(pieces: list, merge: float = 0.6) -> list:
    """output ranges where the voice speaks, close pieces merged so the rush doesn't pump."""
    ranges = []
    for p in sorted(pieces, key=lambda x: x["at"]):
        a, b = p["at"], p["at"] + p["end"] - p["start"]
        if ranges and a <= ranges[-1][1] + merge:
            ranges[-1][1] = max(ranges[-1][1], b)
        else:
            ranges.append([a, b])
    return [[round(a, 3), round(b, 3)] for a, b in ranges]


def voiceOverSlots(timeline: list, perception: dict | None, wordsPerSecond: float = 2.5, minSlot: float = 1.2) -> list:
    """stretches of the edit where nobody talks on the rush: room for a voice over line."""
    speech = [(b["start"], b["end"]) for b in (perception or {}).get("beats", []) if b.get("state") == "speech"]
    free = []
    for entry in timeline:
        s0, s1 = entry["source"]
        o0, o1 = entry["output"]
        if entry.get("muted") or entry["speed"] != 1:
            talk = 0.0
        elif entry.get("media"):
            # another file playing its own sound: not a place for the voice
            talk = s1 - s0
        else:
            talk = sum(max(0.0, min(s1, b) - max(s0, a)) for a, b in speech)
        if talk > 0.3 * (s1 - s0):
            continue
        if free and o0 - free[-1]["end"] < 0.05:
            free[-1]["end"] = o1
            free[-1]["notes"].append(entry.get("note") or "")
        else:
            free.append({"start": o0, "end": o1, "notes": [entry.get("note") or ""]})
    slots = []
    for f in free:
        length = f["end"] - f["start"]
        if length >= minSlot:
            slots.append({"id": len(slots), "start": round(f["start"], 2), "end": round(f["end"], 2),
                          "seconds": round(length, 2), "maxWords": int(length * wordsPerSecond),
                          "onScreen": "; ".join(n for n in f["notes"] if n)})
    return slots


def zoneSpans(zones: list, segments: list, style: str, gap: float = 1.0) -> list:
    """source intervals where zones of one style show up in the edit, merged when closer than gap."""
    raw = []
    for seg in segments:
        for z in zones:
            if z["style"] != style:
                continue
            a, b = max(z["from"], seg["start"]), min(z["to"], seg["end"])
            if b - a > 1e-3:
                raw.append([a, b])
    return mergeSpans(raw, gap)


def overlaps(a: float, b: float, spans: list) -> bool:
    return any(min(b, y) - max(a, x) > 1e-3 for x, y in spans)


def blurriness(zones: list, srcH: int) -> int:
    # premiere blurriness at 1080p, scaled with the source height
    strength = max([z["strength"] for z in zones] or [1.0])
    return int(round(min(500, max(5, 45 * strength * srcH / 1080))))


def mosaicBlocks(zones: list, srcW: int, srcH: int) -> tuple:
    """horizontal and vertical blocks over the whole frame, sized from the smallest zone."""
    sides = [min(z["keys"][0]["box"][2] - z["keys"][0]["box"][0], z["keys"][0]["box"][3] - z["keys"][0]["box"][1])
             for z in zones] or [0.2]
    strength = max([z["strength"] for z in zones] or [1.0])
    blockPx = max(4.0, min(sides) * min(srcW, srcH) * strength / 14)
    return (int(max(2, min(200, round(srcW / blockPx)))), int(max(2, min(200, round(srcH / blockPx)))))


def buildEdit(mediaPath: str, plan: dict, inherited: dict | None = None, sourceName: str | None = None,
              onProgress=noop, maskMode: str | None = None, cutoutQuality: str | None = None, segmenter=None) -> dict:
    """claude's plan -> sequence xml + motion specs + what the host must key in premiere.

    matte mode (default): v1 cuts the source, or the comp nest where zones are blurred. the comp nest holds
    the original, a blurred (or mosaic) duplicate keyed by the matte track above it. titles on v2, the subject
    on v3 as a nest keyed by its own matte, so the title sits behind it.
    baked mode: blur baked into rendered copies on v1, titles on v2, prores 4444 cutouts on v3.
    """
    import masks

    cfg = loadConfig()
    mode = maskMode or plan.get("masks") or cfg.get("maskMode") or "native"
    if mode not in ("matte", "baked", "native"):
        mode = "native"
    info = getProbe(mediaPath)
    norm = edl.normalizePlan(plan, info, mediaPath, getProbe)
    if not info.get("hasVideo") and (any(s["media"] != "main" for s in norm["segments"])
                                     or any(c["video"] for s in norm["segments"] for c in s["cover"])):
        raise ValueError("the main file has no picture: pass a video as mediaPath, other files join through media and cover")
    seqW, seqH = edl.resolveFormat(norm["format"], info, inherited if norm["format"] in (None, "source") else None)
    fps = info.get("fps") or 30.0
    if inherited and inherited.get("ticksPerFrame"):
        fps = xmeml.fpsFromTicks(inherited["ticksPerFrame"])
    _timebase, _ntsc, exactFps = xmeml.rateFromFps(fps)
    segments = norm["segments"]
    zones = norm["blur"]
    renderDir = mediaCacheDir(mediaPath) / "renders"
    warnings = list(norm["warnings"])
    srcW, srcH = int(info.get("width") or seqW), int(info.get("height") or seqH)
    name = norm["name"]
    if mode == "native" and rushZoneStyles(segments, zones)[1]:
        # a stage name: the masked copy grafted into the project comes back under the plan's name
        name = f"{name} (chop stage)"
    ctx = {"masks": masks, "mediaPath": mediaPath, "info": info, "segments": segments, "zones": zones,
           "renderDir": renderDir, "warnings": warnings, "exactFps": exactFps, "seqW": seqW, "seqH": seqH,
           "srcW": srcW, "srcH": srcH, "onProgress": onProgress, "name": name,
           "quality": cutoutQuality or cfg.get("cutoutQuality") or "auto", "segmenter": segmenter,
           "screens": norm["screens"], "effectJobs": [], "nestNames": {}, "media": norm["media"], "maskMode": mode,
           "maskGrafts": [], "maskFeather": int(round(float(orDefault(cfg.get("maskFeather"), 30)) * srcH / 1080)),
           "maskExpansion": int(round(float(orDefault(cfg.get("maskExpansion"), 12)) * srcH / 1080))}
    if mode == "baked" and norm["screens"]:
        warnings.append("screen replacement needs the matte mode, screens skipped in baked mode")

    stabilizePaths(ctx)
    if mode == "baked":
        layers, nests, effects, layerMotions, counts = bakedLayers(ctx)
    else:
        layers, nests, effects, layerMotions, counts = matteLayers(ctx)
    clips = xmeml.toClips(segments, exactFps)
    clipOfSegment = {c["segment"]: n for n, c in enumerate(clips)}
    frame = lambda seconds: int(round(seconds * exactFps))

    pieces = splitEdits(ctx, clips)
    coverLanes, coverMotions, coverAudio, windows = coverLayer(ctx, clips, 1 + len(layers))
    if coverLanes:
        layers.extend(coverLanes)
        layerMotions.extend(coverMotions)
        counts["covers"] = sum(len(lane) for lane in coverLanes)
    if coverAudio:
        counts["coverSounds"] = len(coverAudio)
    cutSpans(pieces, [(frame(w["start"]), frame(w["end"])) for w in windows if w["audio"] == "replace"])
    for clip in clips:
        seg = segments[clip["segment"]]
        title = seg["title"]
        if not title:
            continue
        a, b = clip["start"] / exactFps + title["from"], clip["start"] / exactFps + title["to"]
        if any(w["fullFrame"] and min(b, w["end"]) - max(a, w["start"]) > 0.05 for w in windows):
            warnings.append(f"segment {clip['segment']}: a full frame cover hides its title \"{title['text']}\" (titles sit "
                            "under covers), move the title or give the cover a box")
    ctx["heard"] = [{"media": clips[n].get("media", "main"), "in": p["in"] / exactFps, "out": p["out"] / exactFps,
                     "start": p["start"] / exactFps} for n in sorted(pieces) for p in pieces[n]]

    voice, voiceGroup, duck = None, None, None
    if plan.get("voiceOver"):
        spec = plan["voiceOver"]
        if not os.path.isfile(spec.get("path", "")):
            raise ValueError(f"voice over file not found: {spec.get('path')}")
        onProgress("render", None, "laying out the voice over")
        voice = voiceOverLayout(spec["path"], spec, cfg, onProgress)
        voiceInfo = dict(getProbe(spec["path"]), hasAudio=True)
        ctx["media"]["_voice"] = {"path": spec["path"], "info": voiceInfo, "still": False}
        voiceGroup = {"layout": xmeml.audioLayout(voiceInfo), "role": "voice", "items": [
            {"media": "_voice", "in": frame(p["start"]), "out": frame(p["end"]), "start": frame(p["at"]),
             "end": frame(p["at"]) + frame(p["end"]) - frame(p["start"]), "link": f"vo-{i}"}
            for i, p in enumerate(voice["pieces"], start=1)]}
        videoEnd = clips[-1]["end"] / exactFps if clips else 0.0
        if voice["length"] > videoEnd + 0.05:
            warnings.append(f"the voice over runs {voice['length'] - videoEnd:.1f} s past the last shot, add or stretch segments")
        if spec.get("duck", -14) is not None and any(c["audio"] for c in clips):
            duck = {"db": float(spec.get("duck", -14)), "ranges": duckRanges(voice["pieces"]),
                    "tracks": len(xmeml.audioStreamsOf(info))}
        ctx["voiceWords"] = voice["words"]

    infos = {key: entry["info"] for key, entry in ctx["media"].items()}
    rushGroups = xmeml.clipAudioGroups(clips, infos, pieces)
    coverGroups = coverAudioGroups(coverAudio, infos)
    groups = rushGroups + coverGroups + ([voiceGroup] if voiceGroup else [])
    trackNumbers = xmeml.premiereAudioTracks(groups)
    rushTracks = [t for g in trackNumbers[:len(rushGroups)] for t in g]
    audioJobs = []
    duckSpans = [[a, b, duck["db"]] for a, b in duck["ranges"]] if duck else []
    duckSpans += [[round(w["start"], 3), round(w["end"], 3), w["duck"]] for w in windows if w["audio"] == "duck"]
    if duckSpans and rushTracks:
        audioJobs.append({"kind": "duck", "db": duck["db"] if duck else -12.0, "ranges": sorted(duckSpans), "trackList": rushTracks})
    if duck:
        duck["trackList"] = rushTracks
    levels = []
    for g, group in enumerate(coverGroups):
        for index, item in enumerate(group["items"]):
            if item.get("volume"):
                levels += [{"track": t, "index": index, "db": item["volume"]} for t in trackNumbers[len(rushGroups) + g]]
    if levels:
        audioJobs.append({"kind": "volume", "clips": levels})

    stabilizeJobs(ctx, clips)
    presetJobs = []
    if any(seg["attach"] for seg in segments):
        lanes, attachMotions, jobs = attachLayer(ctx, clips, 1 + len(layers))
        if lanes:
            layers.extend(lanes)
            layerMotions.extend(attachMotions)
            counts["attached"] = sum(len(lane) for lane in lanes)
            presetJobs.extend(jobs)
    if plan.get("captions"):
        lane, captionMotions, job = captionLayer(ctx, plan["captions"], clips, 1 + len(layers), cfg)
        if lane:
            layers.append(lane)
            layerMotions.extend(captionMotions)
            counts["captions"] = len(lane)
            if job:
                presetJobs.append(job)

    motions = []
    for spec in edl.motionSpecs(segments, info, seqW, seqH, ctx["media"]):
        if spec["index"] in clipOfSegment:
            motions.append(dict(spec, index=clipOfSegment[spec["index"]]))
    motions.extend(layerMotions)

    clipName = sourceName or Path(mediaPath.replace("\\", "/")).stem
    outPath = mediaCacheDir(mediaPath) / f"{safeName(norm['name'])}_{mode}_{int(time.time())}.xml"
    sources = {key: {"path": entry["path"], "info": entry["info"]} for key, entry in ctx["media"].items() if key != "main"}
    xmeml.writeXmeml(str(outPath), mediaPath, segments, info, ctx["name"],
                     fps=fps, width=seqW, height=seqH, clipName=clipName, layers=layers, nests=nests,
                     sources=sources, audioGroups=groups)
    onProgress("render", 1.0, "sequence ready")
    result = {
        "xmlPath": str(outPath),
        "sequenceName": ctx["name"],
        "finalName": norm["name"],
        "maskMode": mode,
        "maskGrafts": ctx["maskGrafts"],
        "format": [seqW, seqH],
        "fps": round(fps, 3),
        "duration": round(clips[-1]["end"] / exactFps, 2) if clips else 0,
        "timeline": edl.outputMap(clips, exactFps, segments),
        "media": {key: entry["path"] for key, entry in ctx["media"].items()},
        "motions": motions,
        "matteEffects": effects,
        "presetJobs": presetJobs,
        "effectJobs": ctx["effectJobs"],
        "nestNames": ctx["nestNames"],
        "voiceOver": {k: voice[k] for k in ("pieces", "length")} if voice else None,
        "duck": duck,
        "audioJobs": audioJobs,
        "audioTracks": [{"role": g.get("role", "rush"), "tracks": [f"A{t + 1}" for t in trackNumbers[k]],
                         "clips": len(g["items"])} for k, g in enumerate(groups)],
        "layers": counts,
        "warnings": warnings,
    }
    if hasattr(ctx.get("zoneSegmenter"), "describe"):
        result["outlines"] = ctx["zoneSegmenter"].describe()
    seg = ctx.get("segmenter")
    if ctx.get("gpuChecked") and hasattr(seg, "describe"):
        result["matting"] = seg.describe()
        slow = result["matting"].get("downgraded")
        if slow:
            warnings.append(f"subject matte went from best to fast quality after {slow['afterFrames']} frames at "
                            f"{slow['msPerFrame']} ms each (a busy gpu): edges are softer, free the gpu or set "
                            f"cutoutQuality to fast in config.json")
        elif result["matting"].get("msPerFrame", 0) > 400:
            warnings.append(f"subject matte took {result['matting']['msPerFrame']} ms per frame on "
                            f"{result['matting']['provider']}: set cutoutQuality to fast in config.json for quicker renders")
    return result


def splitEdits(ctx: dict, clips: list) -> dict:
    """sound pieces of each v1 clip (sequence frames) after j and l cuts. the audio edit point moves: the
    incoming sound starts early (j) or the outgoing one runs on (l), the neighbour is trimmed, nothing doubles."""
    fps = ctx["exactFps"]
    frame = lambda seconds: int(round(seconds * fps))
    segs, warnings = ctx["segments"], ctx["warnings"]
    pieces = {n: ([{"start": c["start"], "end": c["end"], "in": c["in"], "out": c["out"]}] if c["audio"] else [])
              for n, c in enumerate(clips)}
    jcuts = set()
    for n, clip in enumerate(clips):
        seg = segs[clip["segment"]]
        if not seg.get("jcut"):
            continue
        if n == 0 or not clip["audio"]:
            warnings.append(f"segment {clip['segment']}: jcut needs a shot before it and its own sound (speed 1), skipped")
            continue
        prev = clips[n - 1]
        lead = min(frame(seg["jcut"]), clip["in"], prev["end"] - prev["start"] - 1)
        if lead <= 0:
            warnings.append(f"segment {clip['segment']}: no room for the jcut (start of the file or a very short shot before)")
            continue
        if segs[prev["segment"]].get("lcut"):
            warnings.append(f"segment {clip['segment']}: jcut and lcut on the same cut, the jcut wins")
        jcuts.add(n)
        p = pieces[n][0]
        p["start"] -= lead
        p["in"] -= lead
        if pieces[n - 1]:
            q = pieces[n - 1][-1]
            q["end"] = min(q["end"], p["start"])
            q["out"] = q["in"] + q["end"] - q["start"]
    for n, clip in enumerate(clips):
        seg = segs[clip["segment"]]
        if not seg.get("lcut") or n + 1 in jcuts:
            continue
        if n + 1 >= len(clips) or not clip["audio"]:
            warnings.append(f"segment {clip['segment']}: lcut needs a shot after it and its own sound (speed 1), skipped")
            continue
        nxt = clips[n + 1]
        entry = (ctx.get("media") or {}).get(clip.get("media", "main")) or {"info": ctx["info"]}
        fileFrames = frame(float(entry["info"].get("duration") or 0))
        tail = min(frame(seg["lcut"]), fileFrames - clip["out"], nxt["end"] - nxt["start"] - 1)
        if tail <= 0:
            warnings.append(f"segment {clip['segment']}: no room for the lcut (end of the file or a very short shot after)")
            continue
        p = pieces[n][-1]
        p["end"] += tail
        p["out"] += tail
        if pieces[n + 1]:
            q = pieces[n + 1][0]
            q["start"] = max(q["start"], p["end"])
            q["in"] = q["out"] - (q["end"] - q["start"])
    # j and l cuts can meet across a short shot: per layout the earlier sound stops where the later one starts
    layoutOf = lambda n: xmeml.layoutKey(((ctx.get("media") or {}).get(clips[n].get("media", "main")) or {"info": ctx["info"]})["info"])
    byLayout = {}
    for n, plist in pieces.items():
        for p in plist:
            byLayout.setdefault(layoutOf(n), []).append(p)
    for plist in byLayout.values():
        plist.sort(key=lambda p: (p["start"], p["end"]))
        for a, b in zip(plist, plist[1:]):
            if a["end"] > b["start"]:
                a["end"] = max(a["start"], b["start"])
                a["out"] = a["in"] + a["end"] - a["start"]
    for n, plist in pieces.items():
        kept = [p for p in plist if p["end"] > p["start"]]
        if plist and not kept:
            warnings.append(f"segment {clips[n]['segment']}: its own sound is fully covered by the split edits around it")
        pieces[n] = kept
    return pieces


def cutSpans(pieces: dict, spans: list):
    """remove sequence frame spans from the cut's sound (a cover replacing it)."""
    for n, plist in pieces.items():
        out = []
        for p in plist:
            parts = [p]
            for a, b in spans:
                kept = []
                for q in parts:
                    if b <= q["start"] or a >= q["end"]:
                        kept.append(q)
                        continue
                    if a > q["start"]:
                        kept.append(dict(q, end=a, out=q["in"] + a - q["start"]))
                    if b < q["end"]:
                        kept.append(dict(q, start=b, **{"in": q["in"] + b - q["start"]}))
                parts = kept
            out.extend(parts)
        pieces[n] = out


def coverLayer(ctx: dict, clips: list, track: int) -> tuple:
    """b-roll, memes, pictures and sounds laid over segments. returns (video lanes, motions, sound items, windows):
    overlapping covers get their own track, picture and sound of a cover stay linked."""
    fps = ctx["exactFps"]
    frame = lambda seconds: int(round(seconds * fps))
    seqW, seqH = ctx["seqW"], ctx["seqH"]
    videoItems, placed, sounds, windows = [], [], [], []
    count = 0
    for clip in clips:
        seg = ctx["segments"][clip["segment"]]
        for cover in seg.get("cover") or []:
            count += 1
            entry = ctx["media"][cover["media"]]
            start = clip["start"] + frame(cover["from"])
            length = max(1, frame(cover["length"]))
            inFrame = frame(cover["start"])
            sourceLength = max(1, frame(cover["end"] - cover["start"]))
            link = f"c{count}" if cover["audio"] != "mute" else None
            if cover["video"]:
                w, h = edl.mediaSize(entry, seqW, seqH)
                if cover["still"]:
                    item = {"path": entry["path"], "start": start, "end": start + length, "in": 0, "out": length,
                            "width": w, "height": h, "frames": None, "alpha": True}
                else:
                    item = {"kind": "media", "media": cover["media"], "start": start, "end": start + length,
                            "in": inFrame, "out": inFrame + sourceLength, "speed": cover["speed"]}
                    if cover["media"] == "main":
                        item = maskedRushCover(ctx, cover, item, sourceLength)
                if link:
                    item["link"] = link
                videoItems.append(item)
                placed.append((cover, w, h))
            if link:
                sounds.append({"media": cover["media"], "start": start, "end": start + length, "in": inFrame,
                               "out": inFrame + length, "link": link, "volume": cover["volume"]})
            windows.append({"start": start / fps, "end": (start + length) / fps, "audio": cover["audio"],
                            "duck": cover["duck"], "media": cover["media"], "fullFrame": cover["video"] and not cover["box"]})
    lanes, where = packLanes(videoItems)
    motions = []
    for i, (cover, w, h) in enumerate(placed):
        lane, index = where[i]
        m = edl.coverMotion(cover, w, h, seqW, seqH)
        if (w, h) != (seqW, seqH) or cover["box"] or cover["zoom"] or cover["fit"] == "fit":
            motions.append({"track": track + lane, "index": index, "from": m["from"], "to": m["to"]})
    return lanes, motions, sounds, windows


def mainCoverRanges(segments: list) -> list:
    """source ranges of the rush shown by covers: blur zones and screens must follow them too."""
    return [{"start": c["start"], "end": c["end"]} for seg in segments for c in seg.get("cover") or []
            if c["media"] == "main" and c["video"]]


def maskedRushCover(ctx: dict, cover: dict, item: dict, sourceLength: int) -> dict:
    """a cover showing the rush shows what v1 would: the comp nest where zones or screens are (matte), a baked
    copy with the zones blurred (baked)."""
    if ctx.get("maskMode") == "baked":
        zones = [z for z in ctx["zones"] if z["to"] >= cover["start"] and z["from"] <= cover["end"]]
        if not zones:
            return item
        ctx["onProgress"]("render", None, f"blurring a cover ({cover['end'] - cover['start']:.1f} s)")
        meta = ctx["masks"].renderSegment(ctx["mediaPath"], cover["start"], cover["end"], ctx["renderDir"], zones=zones)
        return {"path": meta["baked"], "start": item["start"], "end": item["end"], "in": 0, "out": sourceLength,
                "width": meta["width"], "height": meta["height"], "frames": sourceLength, "alpha": False,
                "speed": cover["speed"], **({"link": item["link"]} if item.get("link") else {})}
    if ctx.get("compSpans") and overlaps(cover["start"], cover["end"], ctx["compSpans"]):
        return dict(item, kind="nest", nest="comp")
    return item


def coverAudioGroups(sounds: list, infos: dict) -> list:
    """cover sounds on their own tracks: one set per audio layout, overlapping sounds on another set."""
    byLayout = {}
    for item in sounds:
        byLayout.setdefault(xmeml.layoutKey(infos[item["media"]]), []).append(item)
    groups = []
    for items in byLayout.values():
        layout = xmeml.audioLayout(infos[items[0]["media"]])
        lanes, _where = packLanes(items)
        groups.extend({"layout": layout, "items": lane, "role": "cover"} for lane in lanes)
    return groups


def heardWords(ctx: dict, heard: list, cfg: dict) -> dict:
    """transcript words of every file the cut lets us hear."""
    out = {}
    for key in {p["media"] for p in heard}:
        entry = ctx["media"].get(key) if key != "main" else None
        if key == "main" or not entry:
            out[key] = getTranscript(ctx["mediaPath"], cfg, ctx["onProgress"])["words"]
            continue
        streams = len(xmeml.audioStreamsOf(entry["info"])) or 1
        track = min(int(cfg.get("voiceTrack") or 1), streams)
        out[key] = getTranscript(entry["path"], dict(cfg, voiceTrack=track), ctx["onProgress"])["words"]
    return out


def captionLayer(ctx: dict, spec: dict, clips: list, track: int, cfg: dict) -> tuple:
    """caption stills on their own track, placed by motion, plus the preset to replay on each."""
    import captions
    style = captions.normalizeStyle(spec.get("style") or "pop", cfg.get("captionStyles"))
    source = spec.get("source") or ("voiceover" if ctx.get("voiceWords") is not None else "speech")
    if spec.get("cues"):
        cues = captions.cuesFromText(spec["cues"], style)
    elif source == "voiceover":
        cues = captions.chunkWords(ctx.get("voiceWords") or [], style)
    else:
        heard = ctx.get("heard")
        if heard is None:
            transcript = getTranscript(ctx["mediaPath"], cfg, ctx["onProgress"])
            words = captions.wordsOnTimeline(transcript["words"], ctx["segments"], clips, ctx["exactFps"])
        else:
            words = captions.wordsOnPieces(heardWords(ctx, heard, cfg), heard)
        cues = captions.chunkWords(words, style)
    if not cues:
        ctx["warnings"].append("captions: no words on the timeline (timelapses are skipped)")
        return [], [], None
    ctx["onProgress"]("render", None, f"rendering {len(cues)} captions")
    frame = lambda seconds: int(round(seconds * ctx["exactFps"]))
    lane, motions, animated = [], [], []
    outDir = ctx["renderDir"] / "captions"
    for cue in cues:
        start, end = frame(cue["start"]), frame(cue["end"])
        if end <= start:
            continue
        info = captions.renderCaption(cue, style, ctx["seqW"], ctx["seqH"], outDir)
        lane.append({"path": info["path"], "start": start, "end": end, "width": info["width"], "height": info["height"],
                     "frames": None})
        motions.append({"track": track, "index": len(lane) - 1, "from": {"scalePct": 100.0, "posX": info["posX"],
                                                                          "posY": info["posY"]}, "to": None})
        if cue["first"]:
            animated.append(len(lane) - 1)
    preset = spec.get("preset", style.get("preset"))
    job = {"preset": preset, "track": track, "indexes": animated} if preset and animated else None
    return lane, motions, job


def packLanes(items: list) -> tuple:
    """items that overlap in time can't share a track: greedy tracks, each in time order (premiere's clip order).
    returns (lanes, where) with where[i] = (lane, index in lane) for items[i]."""
    order = sorted(range(len(items)), key=lambda i: (items[i]["start"], items[i]["end"]))
    lanes, ends = [], []
    for i in order:
        for li, e in enumerate(ends):
            if items[i]["start"] >= e:
                lanes[li].append(i)
                ends[li] = items[i]["end"]
                break
        else:
            lanes.append([i])
            ends.append(items[i]["end"])
    where = [None] * len(items)
    for li, lane in enumerate(lanes):
        for k, i in enumerate(lane):
            where[i] = (li, k)
    return [[items[i] for i in lane] for lane in lanes], where


def segMedia(ctx: dict, seg: dict) -> tuple:
    """(path, width, height) of the file a segment plays."""
    entry = (ctx.get("media") or {}).get(seg.get("media", "main"))
    if not entry or seg.get("media", "main") == "main":
        return ctx.get("mediaPath"), ctx["srcW"], ctx["srcH"]
    w, h = edl.mediaSize(entry, ctx["seqW"], ctx["seqH"])
    return entry["path"], w, h


def sourceToSequence(ctx: dict, seg: dict, t: float, x: float, y: float) -> tuple:
    """a source point (normalized) at source time t of a speed 1 segment -> sequence point (normalized), through
    the segment's stabilization if any, then its framing (fill + zoom), exactly where premiere puts it."""
    path = (ctx.get("stabilized") or {}).get(id(seg))
    if path:
        x, y = stabilizedPoint(path, t, x, y)
    _p, srcW, srcH = segMedia(ctx, seg)
    fraction = (t - seg["start"]) / max(1e-6, seg["end"] - seg["start"])
    m = edl.motionFor(edl.viewAt(seg["zoom"], fraction), srcW, srcH, ctx["seqW"], ctx["seqH"])
    scale = m["scalePct"] / 100
    return (m["posX"] + (x - 0.5) * srcW * scale / ctx["seqW"],
            m["posY"] + (y - 0.5) * srcH * scale / ctx["seqH"], scale)


def attachLayer(ctx: dict, clips: list, track: int) -> tuple:
    """labels and stickers riding on tracked objects: tight stills, motion keyed every frame.
    returns (lanes, motions, preset jobs): overlapping labels get their own track."""
    import captions
    import tracking
    from PIL import Image
    items, keysOf, presetOf = [], [], []
    outDir = ctx["renderDir"] / "attach"
    outDir.mkdir(parents=True, exist_ok=True)
    fps = ctx["exactFps"]
    for clip in clips:
        seg = ctx["segments"][clip["segment"]]
        for a in seg["attach"]:
            srcStart, srcEnd = seg["start"] + a["from"], seg["start"] + a["to"]
            ctx["onProgress"]("render", None, f"tracking {a['text'] or 'sticker'} ({srcEnd - srcStart:.1f} s)")
            tracked = tracking.trackBox(segMedia(ctx, seg)[0], srcStart, srcEnd, a["box"])
            if not tracked["frames"]:
                ctx["warnings"].append(f"attach {a['text'] or a['image']}: no frames to track")
                continue
            if len(tracked["lost"]) > 0.2 * len(tracked["frames"]):
                ctx["warnings"].append(f"attach {a['text'] or a['image']}: the object was lost on "
                                       f"{len(tracked['lost'])} frames, the label held its last place")
            if a["image"]:
                img = Image.open(a["image"]).convert("RGBA")
                width = max(8, int(a["size"] * 4 * ctx["seqH"]))
                img = img.resize((width, max(1, int(img.height * width / img.width))))
                key = hashlib.sha1(f"{os.path.abspath(a['image'])}|{width}".encode()).hexdigest()[:14]
                path = outDir / f"sticker_{key}.png"
                img.save(path)
                info = {"path": str(path), "width": img.width, "height": img.height}
            else:
                style = captions.normalizeStyle({"base": "pop", "size": a["size"], "color": a["color"], "stroke": a["stroke"],
                                                 "font": a["font"] or captions.baseStyle["font"], "uppercase": False, "lines": 2})
                info = captions.renderCaption({"words": a["text"].split(), "highlight": None}, style, ctx["seqW"], ctx["seqH"], outDir)
            start = clip["start"] + int(round(a["from"] * fps))
            end = min(clip["end"], clip["start"] + int(round(a["to"] * fps)))
            if end <= start:
                continue
            first = tracked["frames"][0]["box"]
            firstSize = max(1e-6, first[2] - first[0])
            dx, dy = a["offset"]
            keys = []
            for f in tracked["frames"]:
                x0, y0, x1, y1 = f["box"]
                px, py = (x0 + x1) / 2 + dx * (x1 - x0), (y0 + y1) / 2 + dy * (y1 - y0)
                sx, sy, _scale = sourceToSequence(ctx, seg, f["t"], px, py)
                scalePct = 100.0 * ((x1 - x0) / firstSize if a["scaleWithObject"] else 1.0)
                # keep the label clear of the object: half its own size plus the gap
                sx += (info["width"] * scalePct / 100 / 2 / ctx["seqW"] + a["gap"]) * (1 if dx > 0 else -1 if dx < 0 else 0)
                sy += (info["height"] * scalePct / 100 / 2 / ctx["seqH"] + a["gap"]) * (1 if dy > 0 else -1 if dy < 0 else 0)
                keys.append([round(f["t"] - srcStart, 4), round(scalePct, 3), round(sx, 5), round(sy, 5)])
            items.append({"path": info["path"], "start": start, "end": end, "width": info["width"], "height": info["height"],
                          "frames": None})
            keysOf.append(keys)
            presetOf.append(a["preset"])
    lanes, where = packLanes(items)
    motions, jobs = [], []
    for i, keys in enumerate(keysOf):
        lane, index = where[i]
        motions.append({"track": track + lane, "index": index, "from": {"scalePct": keys[0][1], "posX": keys[0][2],
                                                                         "posY": keys[0][3]}, "to": None, "keys": keys})
        if presetOf[i]:
            jobs.append({"preset": presetOf[i], "track": track + lane, "indexes": [index]})
    return lanes, motions, jobs


def stabilizePaths(ctx: dict):
    """camera correction per stabilized segment, computed once: v1, the subject layer and labels all use it."""
    import tracking
    ctx["stabilized"] = {}
    for i, seg in enumerate(ctx["segments"]):
        spec = seg.get("stabilize")
        if not spec:
            continue
        segPath, segW, segH = segMedia(ctx, seg)
        sameSize = (segW, segH) == (ctx["seqW"], ctx["seqH"])
        if spec["method"] == "warp" and seg["speed"] == 1.0 and sameSize and not seg["attach"] and not (seg["title"] or {}).get("behind"):
            continue
        if spec["method"] == "warp":
            ctx["warnings"].append(f"segment {i}: warp stabilizer needs speed 1, a source sized sequence and no layer following "
                                   "the shot, transform stabilization used")
            spec["method"] = "transform"
        ctx["onProgress"]("render", None, f"stabilizing segment {i}")
        path = tracking.cameraPath(segPath, seg["start"], seg["end"], spec["smoothness"])
        if path["frames"]:
            ctx["stabilized"][id(seg)] = path


def stabilizedPoint(path: dict, t: float, x: float, y: float) -> tuple:
    """where a source point ends up after the transform correction (rotation and zoom about the center, then the
    offset, itself scaled by the zoom since transform scales the layer first)."""
    W, H = path["size"]
    frames = path["frames"]
    f = min(frames, key=lambda fr: abs(fr["t"] - t))
    zoom = path["zoom"]
    a = math.radians(f["angle"])
    px, py = (x - 0.5) * W, (y - 0.5) * H
    rx = math.cos(a) * px - math.sin(a) * py
    ry = math.sin(a) * px + math.cos(a) * py
    return (0.5 + (zoom * rx + zoom * f["dx"] * W) / W, 0.5 + (zoom * ry + zoom * f["dy"] * H) / H)


def stabilizeEffect(ctx: dict, path: dict, shift: float = 0.0) -> dict:
    """transform keyed every frame. key times = source seconds + shift (the layer's media time)."""
    zoom = path["zoom"]
    position = [[round(f["t"] + shift, 4), [round(0.5 + zoom * f["dx"], 6), round(0.5 + zoom * f["dy"], 6)]] for f in path["frames"]]
    rotation = [[round(f["t"] + shift, 4), f["angle"]] for f in path["frames"]]
    # premiere 25's transform starts with uniform scale off: both axes set so the zoom never squashes
    return {"effect": "Transform", "matchName": "AE.ADBE Geometry", "params": [
        {"name": "Position", "index": 1, "kind": "point", "keys": position,
         "layerSize": list(path.get("size") or (ctx["srcW"], ctx["srcH"]))},
        {"name": "Uniform Scale", "index": 2, "kind": "bool", "value": True},
        {"name": "Scale Height", "index": 3, "kind": "float", "value": round(100 * zoom, 2)},
        {"name": "Scale Width", "index": 4, "kind": "float", "value": round(100 * zoom, 2)},
        {"name": "Rotation", "index": 7, "kind": "float", "keys": rotation}]}


def stabilizeJobs(ctx: dict, clips: list):
    """v1 clips: the transform correction, or premiere's warp stabilizer when nothing has to follow the shot."""
    for n, clip in enumerate(clips):
        seg = ctx["segments"][clip["segment"]]
        spec = seg.get("stabilize")
        if not spec:
            continue
        path = ctx.get("stabilized", {}).get(id(seg))
        if path:
            ctx["effectJobs"].append({"sequence": "main", "track": 0, "index": n, "effects": [stabilizeEffect(ctx, path)]})
        elif spec["method"] == "warp":
            ctx["effectJobs"].append({"sequence": "main", "track": 0, "index": n, "effects": [
                {"effect": "Warp Stabilizer", "params": [{"name": "Smoothness", "index": 1, "kind": "float",
                                                          "value": round(10 + 90 * spec["smoothness"])}]}]})


def conformReplacement(media: str, srcW: int, srcH: int, seconds: float, outDir: Path) -> dict:
    """the screen content at the rush's size, so corner pin works in the rush's own space: a png for an image,
    an h264 copy (looped if short) for a video. frames are decoded one at a time."""
    from fractions import Fraction
    import av
    from PIL import Image
    outDir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{os.path.abspath(media)}|{os.path.getsize(media)}|{srcW}x{srcH}|{seconds:.2f}".encode()).hexdigest()[:14]
    if media.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")):
        path = outDir / f"screen_{key}.png"
        if not path.exists():
            Image.open(media).convert("RGB").resize((srcW, srcH), Image.LANCZOS).save(path)
        return {"path": str(path), "still": True, "frames": None}
    path = outDir / f"screen_{key}.mp4"
    if path.exists():
        with av.open(str(path)) as c:
            return {"path": str(path), "still": False, "frames": c.streams.video[0].frames or None}
    with av.open(media) as probe:
        rate = probe.streams.video[0].average_rate or Fraction(30, 1)
    need = max(1, int(round(seconds * float(rate))))

    def frames():
        while True:
            produced = 0
            with av.open(media) as src:
                for f in src.decode(src.streams.video[0]):
                    produced += 1
                    yield f.to_image().convert("RGB").resize((srcW, srcH))
            if not produced:
                return

    tmp = path.with_suffix(".part.mp4")
    with av.open(str(tmp), mode="w") as out:
        vs = out.add_stream("libx264", rate=rate)
        vs.width, vs.height, vs.pix_fmt = srcW, srcH, "yuv420p"
        vs.options = {"crf": "16", "preset": "faster"}
        written = 0
        for img in frames():
            for packet in vs.encode(av.VideoFrame.from_image(img)):
                out.mux(packet)
            written += 1
            if written >= need:
                break
        for packet in vs.encode():
            out.mux(packet)
    os.replace(tmp, path)
    return {"path": str(path), "still": False, "frames": written}


def screenLane(ctx: dict, frame) -> tuple:
    """replacements over the used part of each screen's span, corner pin keyed every frame.
    returns (lanes, jobs) with jobs addressed by (lane, index); overlapping screens get their own track."""
    import tracking
    items, specs = [], []
    used = [[seg["start"], seg["end"]] for seg in ctx["segments"] if seg.get("media", "main") == "main"]
    used += [[r["start"], r["end"]] for r in mainCoverRanges(ctx["segments"])]
    order = ["Upper Left", "Upper Right", "Lower Left", "Lower Right"]
    quadCorner = {"Upper Left": 0, "Upper Right": 1, "Lower Right": 2, "Lower Left": 3}
    for k, screen in enumerate(ctx["screens"]):
        spans = sorted([max(a, screen["from"]), min(b, screen["to"])] for a, b in used
                       if min(b, screen["to"]) - max(a, screen["from"]) > 1e-3)
        if not spans:
            continue
        if not os.path.isfile(screen["media"]):
            ctx["warnings"].append(f"screen {k}: replacement not found: {screen['media']}")
            continue
        last = max(b for _, b in spans)
        ctx["onProgress"]("render", None, f"tracking screen {screen['label'] or k} ({last - screen['from']:.1f} s)")
        tracked = tracking.trackQuad(ctx["mediaPath"], screen["from"], last, screen["quad"])
        if len(tracked["lost"]) > 0.1 * max(1, len(tracked["frames"])):
            ctx["warnings"].append(f"screen {k}: tracking lost on {len(tracked['lost'])} frames, check it in premiere")
        media = conformReplacement(screen["media"], ctx["srcW"], ctx["srcH"], last - screen["from"], ctx["renderDir"] / "screens")
        for a, b in spans:
            length = frame(b) - frame(a)
            if length <= 0:
                continue
            # a still starts at 0 for each piece, a video plays on from the screen's start
            base = a if media["still"] else screen["from"]
            offset = frame(a - base)
            items.append({"path": media["path"], "start": frame(a), "end": frame(a) + length, "in": offset, "out": offset + length,
                          "width": ctx["srcW"], "height": ctx["srcH"], "frames": media["frames"], "alpha": False})
            params = []
            for index, name in enumerate(order):
                keys = [[round(f["t"] - base, 4), f["quad"][quadCorner[name]]] for f in tracked["frames"] if a - 1e-6 <= f["t"] <= b + 1e-6]
                params.append({"name": name, "index": index, "kind": "point", "keys": keys, "layerSize": [ctx["srcW"], ctx["srcH"]]})
            specs.append([{"effect": "Corner Pin", "matchName": "AE.ADBE Corner Pin", "params": params}])
    lanes, where = packLanes(items)
    jobs = [{"lane": where[i][0], "index": where[i][1], "effects": spec} for i, spec in enumerate(specs)]
    return lanes, jobs


def titleWindows(ctx: dict, clips: list) -> list:
    """titles in sequence frames: (clip, seg, title, startFrame, endFrame)."""
    out = []
    for clip in clips:
        seg = ctx["segments"][clip["segment"]]
        title = seg["title"]
        if not title:
            continue
        startFrame = clip["start"] + int(round(title["from"] * ctx["exactFps"]))
        endFrame = min(clip["end"], clip["start"] + int(round(title["to"] * ctx["exactFps"])))
        if endFrame > startFrame:
            out.append((clip, seg, title, startFrame, endFrame))
    return out


def titlePng(ctx: dict, title: dict) -> str:
    masks = ctx["masks"]
    key = masks.renderKey(title, ctx["seqW"], ctx["seqH"])
    ctx["renderDir"].mkdir(parents=True, exist_ok=True)
    path = ctx["renderDir"] / f"title_{key}.png"
    # a sequence built earlier keeps premiere's lock on the same png: never rewrite it
    if path.is_file() and path.stat().st_size > 0:
        return str(path)
    return masks.renderTitle(title, ctx["seqW"], ctx["seqH"], str(path))


def mirroredMotion(ctx: dict, seg: dict, title: dict, track: int, index: int):
    """a layer that shows the source must sit exactly where the source sits on v1."""
    segLength = max(1e-6, (seg["end"] - seg["start"]) / seg["speed"])
    sizeDiffers = (ctx["srcW"], ctx["srcH"]) != (ctx["seqW"], ctx["seqH"])
    if not (seg["zoom"] or sizeDiffers):
        return None
    view = lambda f: edl.motionFor(edl.viewAt(seg["zoom"], f), ctx["srcW"], ctx["srcH"], ctx["seqW"], ctx["seqH"])
    animated = bool(seg["zoom"] and seg["zoom"]["to"])
    return {"track": track, "index": index, "from": view(title["from"] / segLength),
            "to": view(title["to"] / segLength) if animated else None}


def getSegmenter(ctx: dict):
    """segmenter given by the caller (instance or factory), else built once per edit."""
    seg = ctx.get("segmenter")
    if seg is None or not hasattr(seg, "mask"):
        ctx.setdefault("segmenterFactory", seg)
        ctx["onProgress"]("render", None, "loading the subject matting models")
        seg = seg() if seg is not None else ctx["masks"].Segmenter(ctx["quality"])
        ctx["segmenter"] = seg
    if hasattr(seg, "describe") and not ctx.get("gpuChecked"):
        ctx["gpuChecked"] = True
        if not seg.describe().get("gpu") and getattr(seg, "quality", "") == "fast":
            ctx["warnings"].append("subject matte in fast quality (no gpu runtime for onnx): edges are softer, "
                                   "run install.bat on a machine with a gpu for birefnet edges")
    return seg


def bakedLayers(ctx: dict) -> tuple:
    """baked: pixels rendered here, nothing to key in premiere (fallback when keys are refused)."""
    masks, segments, zones, exactFps = ctx["masks"], ctx["segments"], ctx["zones"], ctx["exactFps"]
    onProgress, warnings = ctx["onProgress"], ctx["warnings"]

    def overlapping(start, end):
        return [z for z in zones if z["to"] >= start and z["from"] <= end]

    work = [k for k, seg in enumerate(segments) if seg.get("media", "main") == "main" and overlapping(seg["start"], seg["end"])]
    titled = [k for k, seg in enumerate(segments) if seg["title"] and seg["title"]["behind"]]
    totalJobs = max(1, len(work) + len(titled))
    done = [0]

    def stepProgress(stage, pct=None, message=None):
        if pct is not None:
            onProgress("render", (done[0] + pct) / totalJobs, None)

    for k in work:
        seg = segments[k]
        onProgress("render", done[0] / totalJobs, f"blurring segment {k} ({seg['end'] - seg['start']:.1f} s)")
        meta = masks.renderSegment(ctx["mediaPath"], seg["start"], seg["end"], ctx["renderDir"],
                                   zones=overlapping(seg["start"], seg["end"]), onProgress=stepProgress)
        seg["videoFile"] = {"path": meta["baked"], "frames": int(round((seg["end"] - seg["start"]) * exactFps)),
                            "width": meta["width"], "height": meta["height"]}
        for label in meta["lostTracks"]:
            warnings.append(f"segment {k}: tracking lost on {label}, the zone stayed where it was planned")
        done[0] += 1

    titleLane, cutoutLane, layerMotions = [], [], []
    for clip, seg, title, startFrame, endFrame in titleWindows(ctx, xmeml.toClips(segments, exactFps)):
        titleLane.append({"path": titlePng(ctx, title), "start": startFrame, "end": endFrame,
                          "width": ctx["seqW"], "height": ctx["seqH"], "frames": None})
        if not title["behind"]:
            continue
        segmenter = getSegmenter(ctx)
        srcStart, srcEnd = seg["start"] + title["from"], seg["start"] + title["to"]
        onProgress("render", done[0] / totalJobs, f"cutting out the subject for \"{title['text']}\"")
        meta = masks.renderSegment(ctx["mediaPath"], srcStart, srcEnd, ctx["renderDir"], zones=overlapping(srcStart, srcEnd),
                                   cutout={"box": title["subjectBox"], "track": title["trackSubject"]},
                                   segmenter=segmenter, onProgress=stepProgress, bake=False)
        done[0] += 1
        cutoutLane.append({"path": meta["cutout"], "start": startFrame, "end": endFrame,
                           "width": meta["width"], "height": meta["height"], "frames": meta["frames"]})
        motion = mirroredMotion(ctx, seg, title, 2, len(cutoutLane) - 1)
        if motion:
            layerMotions.append(motion)
        path = (ctx.get("stabilized") or {}).get(id(seg))
        if path:
            # the cutout file starts at the title's first source frame
            ctx["effectJobs"].append({"sequence": "main", "track": 2, "index": len(cutoutLane) - 1,
                                      "effects": [stabilizeEffect(ctx, path, shift=-srcStart)]})

    layers = [lane for lane in (titleLane, cutoutLane) if lane]
    counts = {"titles": len(titleLane), "cutouts": len(cutoutLane), "blurredSegments": len(work)}
    return layers, None, None, layerMotions, counts


def rushZoneStyles(segments: list, zones: list) -> tuple:
    """(source ranges of the rush shown in the edit, zone styles that show up in them). blur zones and screens
    live in the rush's time: other files are left alone, covers taken from the rush are not."""
    rushRanges = [seg for seg in segments if seg.get("media", "main") == "main"] + mainCoverRanges(segments)
    return rushRanges, [style for style in ("blur", "pixelate") if zoneSpans(zones, rushRanges, style)]


def zoneSegmenter(ctx: dict):
    """the segmenter outline zones use: sam 2 with video memory, or mobilesam alone when it is not installed
    (a silhouette per frame has no use for hair-level edges)."""
    if ctx.get("zoneSegmenter") is None:
        given = ctx.get("segmenterFactory") or ctx.get("segmenter")
        if hasattr(given, "mask") or hasattr(given, "outline"):
            ctx["zoneSegmenter"] = given
        else:
            ctx["onProgress"]("render", None, "loading the segmentation model for the outline masks")
            ctx["zoneSegmenter"] = given("outline") if callable(given) else ctx["masks"].outlineSegmenter()
            if "warnings" in ctx and getattr(ctx["zoneSegmenter"], "quality", "") != "video":
                ctx["warnings"].append("outline masks segmented by mobilesam frame by frame: install torch and "
                                       "transformers for sam 2 with video memory")
    return ctx["zoneSegmenter"]


def zoneKeys(ctx: dict, zone: dict, a: float, b: float) -> tuple:
    """(keys [(source seconds, normalized box or polygon)], lost labels) of one zone over [a, b): every frame
    when tracked or outlined, the planned keys otherwise."""
    masks = ctx["masks"]
    outline = zone["shape"] == "outline"
    if zone["track"] or outline:
        tracked = masks.trackZones(ctx["mediaPath"], a, b, [zone], segmenter=zoneSegmenter(ctx) if outline else None)
        source = "shapes" if outline else "boxes"
        return [(f["t"], f[source][0][1]) for f in tracked["frames"] if f[source]], tracked["lostTracks"]
    # keys on the first and last frame: premiere ignores a single-key mask path and keys past the out point
    last = max(a, b - 1 / ctx["exactFps"])
    times = {a, last} | {k["t"] for k in zone["keys"] if a < k["t"] < last}
    return [(t, masks.boxAt(zone, t)) for t in sorted(times)], []


def mergeSpans(spans: list, gap: float = 1.0) -> list:
    merged = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


offScreenBox = [-0.3, -0.3, -0.2, -0.2]


def roundShape(shape) -> list:
    """a box [x0, y0, x1, y1] or a polygon [[x, y], ...], rounded for the project file."""
    if shape and isinstance(shape[0], (list, tuple)):
        return [[round(float(x), 5), round(float(y), 5)] for x, y in shape]
    return [round(float(c), 5) for c in shape]


def parkedKeys(keys: list, a: float, b: float, clipStart: float, clipEnd: float, fps: float, park=None) -> list:
    """the zone's keys, parked off screen on the frames of the clip outside [a, b): a mask holds its last
    path forever, and a parked mask hides nothing. park keeps the keys' vertex count."""
    step = 1 / fps
    park = offScreenBox if park is None else park
    out = []
    if a > clipStart + step / 2:
        out += [(clipStart, park), (max(clipStart, a - step), park)]
    out += list(keys)
    if b < clipEnd - step / 2:
        out += [(b, park), (max(b, clipEnd - step), park)]
    return out


def clipZoneMasks(ctx: dict, zones: list, sequence: str, track: int, index: int, inSeconds: float,
                  outSeconds: float, progress) -> tuple:
    """native masks on one clip of the rush itself: (xml filters, effect specs for the host, grafts) for the
    zones showing inside the clip's source range, each mask parked off screen on the clip's other frames.
    keys are source seconds whatever the clip's speed. the gaussian blur comes in through the xml and only
    gets its edge setting from the host; the mosaic is the host's."""
    srcW, srcH, warnings, fps = ctx["srcW"], ctx["srcH"], ctx["warnings"], ctx["exactFps"]
    inside = [(zi, zone, max(zone["from"], inSeconds), min(zone["to"], outSeconds)) for zi, zone in enumerate(zones)]
    inside = [(zi, zone, a, b) for zi, zone, a, b in inside if b - a > 1e-3]
    filters, effects, grafts = [], [], []
    blurZones = [zone for _zi, zone, _a, _b in inside if zone["style"] == "blur"]
    pixelZones = [zone for _zi, zone, _a, _b in inside if zone["style"] == "pixelate"]
    if blurZones:
        amount = blurriness(blurZones, srcH)
        filters.append(xmeml.gaussianBlurFilter(min(100, amount)))
        effects.append({"effect": "Gaussian Blur", "intrinsic": True, "params": [
            {"name": "Repeat Edge Pixels", "index": 2, "value": True}]})
    if pixelZones:
        hBlocks, vBlocks = mosaicBlocks(pixelZones, srcW, srcH)
        effects.append({"effect": "Mosaic", "params": [
            {"name": "Horizontal Blocks", "index": 0, "value": hBlocks},
            {"name": "Vertical Blocks", "index": 1, "value": vBlocks},
            {"name": "Sharp Colors", "index": 2, "value": True}]})
    for zi, zone, a, b in inside:
        label = zone["label"] or f"zone {zi}"
        progress(f"{zone['style']} mask on {label} ({b - a:.1f} s)")
        keys, lost = zoneKeys(ctx, zone, a, b)
        if lost:
            warnings.append(f"{zone['style']} {a:.1f}-{b:.1f}s: tracking lost on {label}, the mask stayed where it was planned")
        if not keys:
            continue
        park = ctx["masks"].offScreenPolygon(len(keys[0][1])) if zone["shape"] == "outline" else offScreenBox
        grafts.append({"sequence": sequence, "track": track, "index": index,
                       "effect": "Gaussian Blur" if zone["style"] == "blur" else "Mosaic",
                       "shape": zone["shape"], "feather": ctx["maskFeather"], "expansion": ctx["maskExpansion"],
                       "label": label, "keys": [[round(t, 5), roundShape(shape)]
                                                for t, shape in parkedKeys(keys, a, b, inSeconds, outSeconds, fps, park)]})
    return filters, effects, grafts


def nativeZoneLanes(ctx: dict, jobs: list, firstLane: int, frame, progress) -> tuple:
    """native masks: one duplicate of the rush per stretch where zones show, its Gaussian Blur and Mosaic each
    limited by one keyframed mask per zone, grafted into the project file instead of a matte. a duplicate is
    opaque outside its masks, so every zone of a stretch shares the same clip whatever its style. returns
    (lanes, effects for the host, grafts)."""
    srcW, srcH, warnings, fps = ctx["srcW"], ctx["srcH"], ctx["warnings"], ctx["exactFps"]
    items, fx, grafts = [], [], []
    for index, (ca, cb) in enumerate(mergeSpans([[a, b] for _zi, _zone, a, b in jobs])):
        start, end = frame(ca), frame(cb)
        if end <= start:
            continue
        inside = [(zi, zone, a, b) for zi, zone, a, b in jobs if a < cb and b > ca]
        dup = {"kind": "source", "start": start, "end": end, "in": start, "out": end, "filters": []}
        blurZones = [zone for _zi, zone, _a, _b in inside if zone["style"] == "blur"]
        pixelZones = [zone for _zi, zone, _a, _b in inside if zone["style"] == "pixelate"]
        if blurZones:
            amount = blurriness(blurZones, srcH)
            dup["filters"].append(xmeml.gaussianBlurFilter(min(100, amount)))
            fx.append({"track": firstLane, "index": index, "effect": "Gaussian Blur", "params": [
                {"name": "Blurriness", "index": 0, "value": amount},
                {"name": "Repeat Edge Pixels", "index": 2, "value": True}]})
        if pixelZones:
            hBlocks, vBlocks = mosaicBlocks(pixelZones, srcW, srcH)
            fx.append({"track": firstLane, "index": index, "effect": "Mosaic", "params": [
                {"name": "Horizontal Blocks", "index": 0, "value": hBlocks},
                {"name": "Vertical Blocks", "index": 1, "value": vBlocks},
                {"name": "Sharp Colors", "index": 2, "value": True}]})
        items.append(dup)
        for zi, zone, a, b in inside:
            label = zone["label"] or f"zone {zi}"
            progress(f"{zone['style']} mask on {label} ({b - a:.1f} s)")
            keys, lost = zoneKeys(ctx, zone, a, b)
            if lost:
                warnings.append(f"{zone['style']} {a:.1f}-{b:.1f}s: tracking lost on {label}, the mask stayed where it was planned")
            if not keys:
                continue
            park = ctx["masks"].offScreenPolygon(len(keys[0][1])) if zone["shape"] == "outline" else offScreenBox
            grafts.append({"sequence": "comp", "track": firstLane, "index": index,
                           "effect": "Gaussian Blur" if zone["style"] == "blur" else "Mosaic",
                           "shape": zone["shape"], "feather": ctx["maskFeather"], "expansion": ctx["maskExpansion"],
                           "label": label, "keys": [[round(t, 5), roundShape(shape)]
                                                    for t, shape in parkedKeys(keys, a, b, ca, cb, fps, park)]})
    return [{"items": items}], fx, grafts


def matteLayers(ctx: dict) -> tuple:
    """mocha style: mattes rendered here, compositing done by premiere on the original footage. native mode
    puts the effect and its keyframed mask on the rush clips themselves, no comp nest, unless a screen or a
    cover of the rush still needs the nest: then each zone's mask goes on a duplicate's effect inside it."""
    masks, segments, zones, exactFps = ctx["masks"], ctx["segments"], ctx["zones"], ctx["exactFps"]
    onProgress, warnings, info = ctx["onProgress"], ctx["warnings"], ctx["info"]
    srcW, srcH = ctx["srcW"], ctx["srcH"]
    frame = lambda seconds: int(round(seconds * exactFps))
    fileFrames = frame(info.get("duration") or 0)
    clips = xmeml.toClips(segments, exactFps)
    titles = titleWindows(ctx, clips)
    rushSegments = [seg for seg in segments if seg.get("media", "main") == "main"]
    rushRanges, styles = rushZoneStyles(segments, zones)
    spans = {style: zoneSpans(zones, rushRanges, style) for style in styles}
    native = ctx.get("maskMode") == "native"
    zoneSpansAll = [span for style in styles for span in spans[style]]
    screenLanes, screenJobs = screenLane(ctx, frame) if ctx.get("screens") else ([], [])
    screenItems = [item for lane in screenLanes for item in lane]
    direct = native and not screenItems and not any(c.get("media") == "main" and overlaps(c["start"], c["end"], zoneSpansAll)
                                                   for seg in segments for c in seg.get("cover") or [])
    if native and styles and not direct:
        warnings.append("native masks inside a comp nest: a screen or a cover of the rush meets a zone")
    directClips = [(n, clip) for n, clip in enumerate(clips)
                   if direct and clip.get("media", "main") == "main" and not clip.get("still") and not clip.get("videoFile")
                   and overlaps(clip["in"] / exactFps, clip["out"] / exactFps, zoneSpansAll)]
    zoneJobs = [(zi, zone, a, b) for zi, zone in enumerate(zones)
                for a, b in zoneSpans([zone], rushRanges, zone["style"])] if native and not direct else []
    behind = [t for t in titles if t[2]["behind"]]
    totalJobs = max(1, (len(zoneJobs) + len(directClips) if native else sum(len(v) for v in spans.values())) + len(behind))
    done = [0]

    def stepProgress(stage, pct=None, message=None):
        if pct is not None:
            onProgress("render", (done[0] + pct) / totalJobs, None)

    def step(message):
        onProgress("render", done[0] / totalJobs, message)
        done[0] += 1

    def matteItem(meta: dict, a: float, b: float) -> dict:
        length = max(1, frame(b) - frame(a))
        frames = int(round(meta["frames"] / max(1e-6, meta["fps"]) * exactFps))
        return {"path": meta["path"], "start": frame(a), "end": frame(a) + length, "in": 0, "out": length,
                "width": meta["width"], "height": meta["height"], "frames": max(length, frames)}

    nests, effects, layers, layerMotions = {}, [], [], []
    name = ctx["name"]

    for n, clip in directClips:
        seg = segments[clip["segment"]]
        filters, fx, grafts = clipZoneMasks(ctx, zones, "main", 0, n, clip["in"] / exactFps, clip["out"] / exactFps, step)
        if filters:
            seg["filters"] = filters
        if fx:
            ctx["effectJobs"].append({"sequence": "main", "track": 0, "index": n, "effects": fx})
        if grafts:
            seg["masked"] = True
            ctx["maskGrafts"].extend(grafts)

    if (styles and not direct) or screenItems:
        compLanes = [{"items": [{"kind": "source", "start": 0, "end": fileFrames, "in": 0, "out": fileFrames}]}]
        compFx = []
        for lane in screenLanes:
            compLanes.append({"items": lane})
        for job in screenJobs:
            ctx["effectJobs"].append({"sequence": "comp", "track": 1 + job["lane"], "index": job["index"], "effects": job["effects"]})
        if native:
            lanes, fx, grafts = nativeZoneLanes(ctx, zoneJobs, len(compLanes), frame, step)
            compLanes.extend(lanes)
            compFx.extend(fx)
            ctx["maskGrafts"].extend(grafts)
        for style in ([] if native else styles):
            dupItems, matteItems = [], []
            dupTrack = len(compLanes)
            matteTrack = dupTrack + 2  # 1-based "video n" in premiere
            for k, (a, b) in enumerate(spans[style]):
                inSpan = [z for z in zones if z["style"] == style and z["to"] > a and z["from"] < b]
                onProgress("render", done[0] / totalJobs, f"{style} matte {k + 1}/{len(spans[style])} ({b - a:.1f} s)")
                meta = masks.renderMatte(ctx["mediaPath"], a, b, ctx["renderDir"], zones=inSpan, onProgress=stepProgress)
                done[0] += 1
                for label in meta["lostTracks"]:
                    warnings.append(f"{style} {a:.1f}-{b:.1f}s: tracking lost on {label}, the zone stayed where it was planned")
                matte = matteItem(meta, a, b)
                dup = {"kind": "source", "start": matte["start"], "end": matte["end"],
                       "in": matte["start"], "out": matte["end"], "filters": []}
                if style == "blur":
                    amount = blurriness(inSpan, srcH)
                    dup["filters"].append(xmeml.gaussianBlurFilter(min(100, amount)))
                    compFx.append({"track": dupTrack, "index": k, "effect": "Gaussian Blur", "params": [
                        {"name": "Blurriness", "index": 0, "value": amount},
                        {"name": "Repeat Edge Pixels", "index": 2, "value": True}]})
                else:
                    hBlocks, vBlocks = mosaicBlocks(inSpan, srcW, srcH)
                    compFx.append({"track": dupTrack, "index": k, "effect": "Mosaic", "params": [
                        {"name": "Horizontal Blocks", "index": 0, "value": hBlocks},
                        {"name": "Vertical Blocks", "index": 1, "value": vBlocks},
                        {"name": "Sharp Colors", "index": 2, "value": True}]})
                compFx.append({"track": dupTrack, "index": k, "effect": "Track Matte Key", "params": [
                    {"name": "Matte", "index": 0, "matteTrack": matteTrack}]})
                dupItems.append(dup)
                matteItems.append(matte)
            compLanes.append({"items": dupItems})
            # the matte track stays on: the key hides it, and reads black from a track whose output is off
            compLanes.append({"items": matteItems})
        nests["comp"] = {"name": f"{name} (chop comp)", "width": srcW, "height": srcH, "lanes": compLanes}
        ctx["nestNames"]["comp"] = nests["comp"]["name"]
        if compFx:
            effects.append({"key": "comp", "name": nests["comp"]["name"], "videoTracks": len(compLanes), "effects": compFx})
        allSpans = [s for v in spans.values() for s in v] + [[i["start"] / exactFps, i["end"] / exactFps] for i in screenItems]
        ctx["compSpans"] = allSpans
        for seg in rushSegments:
            if overlaps(seg["start"], seg["end"], allSpans):
                seg["nest"] = "comp"

    titleLane = [{"path": titlePng(ctx, t[2]), "start": t[3], "end": t[4], "width": ctx["seqW"], "height": ctx["seqH"],
                  "frames": None} for t in titles]
    if titleLane:
        layers.append(titleLane)
    if behind:
        fillItems, matteItems, subjectLane = [], [], []
        allSpans = [s for v in spans.values() for s in v] + [[i["start"] / exactFps, i["end"] / exactFps] for i in screenItems]
        for k, (clip, seg, title, startFrame, endFrame) in enumerate(behind):
            segmenter = getSegmenter(ctx)
            length = endFrame - startFrame
            inFrame = clip["in"] + (startFrame - clip["start"])
            a, b = inFrame / exactFps, (inFrame + length) / exactFps
            onProgress("render", done[0] / totalJobs, f"subject matte for \"{title['text']}\" ({b - a:.1f} s)")
            meta = masks.renderMatte(ctx["mediaPath"], a, b, ctx["renderDir"],
                                     subject={"box": title["subjectBox"], "track": title["trackSubject"]},
                                     segmenter=segmenter, onProgress=stepProgress)
            done[0] += 1
            matte = dict(matteItem(meta, a, b), start=inFrame, end=inFrame + length)
            fill = {"kind": "source", "start": inFrame, "end": inFrame + length, "in": inFrame, "out": inFrame + length}
            if "comp" in nests and overlaps(a, b, allSpans):
                # the subject shows the blurred zones too, exactly like v1
                fill = dict(fill, kind="nest", nest="comp")
            elif direct and overlaps(a, b, zoneSpansAll):
                filters, fx, grafts = clipZoneMasks(ctx, zones, "subject", 0, k, a, b, step)
                if filters:
                    fill["filters"] = filters
                if fx:
                    ctx["effectJobs"].append({"sequence": "subject", "track": 0, "index": k, "effects": fx})
                ctx["maskGrafts"].extend(grafts)
            fillItems.append(fill)
            matteItems.append(matte)
            subjectLane.append({"kind": "nest", "nest": "subject", "start": startFrame, "end": endFrame,
                                "in": inFrame, "out": inFrame + length})
            motion = mirroredMotion(ctx, seg, title, 1 + len(layers), k)
            if motion:
                layerMotions.append(motion)
            path = (ctx.get("stabilized") or {}).get(id(seg))
            if path:
                # the subject nest runs on source time, same keys as v1
                ctx["effectJobs"].append({"sequence": "main", "track": 1 + len(layers), "index": k,
                                          "effects": [stabilizeEffect(ctx, path)]})
        nests["subject"] = {"name": f"{name} (chop subject)", "width": srcW, "height": srcH,
                            "lanes": [{"items": fillItems}, {"items": matteItems}]}
        ctx["nestNames"]["subject"] = nests["subject"]["name"]
        effects.append({"key": "subject", "name": nests["subject"]["name"], "videoTracks": 2, "effects": [
            {"track": 0, "index": k, "effect": "Track Matte Key", "params": [{"name": "Matte", "index": 0, "matteTrack": 2}]}
            for k in range(len(fillItems))]})
        layers.append(subjectLane)

    counts = {"titles": len(titleLane), "cutouts": len(behind),
              "blurredSegments": sum(1 for seg in segments if seg.get("nest") == "comp" or seg.get("masked")),
              "mattes": (0 if native else sum(len(v) for v in spans.values())) + len(behind)}
    if native:
        counts["masks"] = len(ctx["maskGrafts"])
    return layers, nests, effects, layerMotions, counts


def writeCaptions(mediaPath: str, sequenceName: str, captions: list) -> str:
    folder = mediaCacheDir(mediaPath) / "captions"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{safeName(sequenceName)}_{int(time.time())}.srt"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(edl.captionsToSrt(captions))
    return str(path)
