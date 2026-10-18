"""reference edits: fetch a video the user admires and measure how it is cut, so its style can be learned.

perception samples a rush every half second, too coarse for shorts cut every 0.4 s: here every frame is
compared to the previous one at low resolution. a cut is a spike far above the local change, a flash is a
spike that comes back to the same picture, camera moves come from points tracked between frames.
"""

import html
import json
import math
import os
import re
import shutil
from pathlib import Path

import av
import cv2
import numpy as np
from PIL import Image, ImageDraw

import media
import perceive
import sheets

analysisVersion = 1


def slug(text: str) -> str:
    return re.sub(r"[^\w\-]+", "_", text or "", flags=re.UNICODE).strip("_")[:60] or "reference"


def fetch(url: str, outDir: Path, cookiesFromBrowser: str | None = None, onProgress=None) -> dict:
    """download with yt-dlp on this machine, 720p max (cuts and frames, not 4k), native or auto captions
    when the platform has them. {"path", "url", "title", "uploader", "duration", "captions"}."""
    try:
        import yt_dlp
    except ImportError as err:
        raise RuntimeError("yt-dlp is missing, run install.bat again or pip install yt-dlp") from err
    outDir = Path(outDir)
    outDir.mkdir(parents=True, exist_ok=True)
    merged = bool(shutil.which("ffmpeg"))
    options = {
        "format": ("bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/b[ext=mp4]/b" if merged
                   else "b[height<=720][ext=mp4][acodec!=none][vcodec!=none]/18/b[acodec!=none][vcodec!=none]"),
        "merge_output_format": "mp4",
        "outtmpl": str(outDir / "%(extractor)s_%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        # original language only: translated tracks are rate limited
        "subtitleslangs": ["en", "fr", "en-orig", "fr-orig"],
        "subtitlesformat": "vtt",
        "logger": QuietLogger(),
    }
    if shutil.which("deno") is None and shutil.which("node"):
        # youtube needs a javascript runtime, node is there for hyperframes anyway
        options["js_runtimes"] = {"node": {}}
    if cookiesFromBrowser:
        options["cookiesfrombrowser"] = (cookiesFromBrowser,)
    if onProgress:
        options["progress_hooks"] = [lambda d: onProgress("download", downloadPct(d), None)]
    # captions in a second pass: a refused translated track (429) must not cost the video
    subtitleOptions = {k: options.pop(k) for k in ("writesubtitles", "writeautomaticsub", "subtitleslangs", "subtitlesformat")}
    with yt_dlp.YoutubeDL(options) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
        except Exception as err:
            text = str(err)
            if "not a bot" in text or "Sign in" in text or "429" in text:
                raise RuntimeError("the platform wants a signed-in browser: retry with cookiesFromBrowser "
                                   "'firefox' (chrome and edge lock their cookies while open), or download "
                                   "the video yourself and pass its path to breakdownReference") from err
            raise
        path = Path(ydl.prepare_filename(info))
    try:
        subOptions = dict(options, skip_download=True, progress_hooks=[], ignoreerrors=True, **subtitleOptions)
        with yt_dlp.YoutubeDL(subOptions) as ydl:
            ydl.process_ie_result(info, download=True)
    except Exception:
        pass
    if path.suffix.lower() != ".mp4" and path.with_suffix(".mp4").exists():
        path = path.with_suffix(".mp4")
    captions = sorted(str(p) for p in outDir.glob(f"*{info.get('id')}*.vtt"))
    meta = {"path": str(path), "url": url, "title": info.get("title"), "uploader": info.get("uploader"),
            "duration": info.get("duration"), "captions": captions}
    (outDir / f"{path.stem}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


class QuietLogger:
    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def downloadPct(d: dict) -> float | None:
    total = d.get("total_bytes") or d.get("total_bytes_estimate")
    if total and d.get("downloaded_bytes") is not None:
        return min(1.0, d["downloaded_bytes"] / total)
    return None


def sidecarCaptions(path: str) -> list:
    """vtt files yt-dlp wrote next to a downloaded reference."""
    p = Path(path)
    return sorted(str(c) for c in p.parent.glob(f"{p.stem}*.vtt"))


def readVtt(path: str) -> list:
    """[{start, end, text}] from a webvtt file. youtube auto captions roll: each cue repeats the previous
    line, so only lines not seen in the last cues are kept, and the 10 ms bridge cues are skipped."""
    cues, recent = [], []
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    stamp = re.compile(r"(\d+):(\d\d):(\d\d)[.,](\d+)\s*-->\s*(\d+):(\d\d):(\d\d)[.,](\d+)")
    toSec = lambda h, m, s, ms: int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 10 ** len(ms)
    for block in re.split(r"\n{2,}", text.replace("\r\n", "\n")):
        lines = block.strip().splitlines()
        for i, line in enumerate(lines):
            m = stamp.search(line)
            if not m:
                continue
            start, end = toSec(*m.groups()[:4]), toSec(*m.groups()[4:])
            # ">>" marks a speaker change
            body = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", l)).replace(">>", "")).strip() for l in lines[i + 1:]]
            fresh = [l for l in body if l and l not in recent]
            if end - start >= 0.05 and fresh:
                cues.append({"start": round(start, 3), "end": round(end, 3), "text": " ".join(fresh)})
                recent = (recent + fresh)[-4:]
            break
    return cues


def cameraStep(prev: np.ndarray, cur: np.ndarray) -> tuple:
    """similarity between two gray frames: (scale, dx, dy as share of width, inliers, tracked)."""
    pts = cv2.goodFeaturesToTrack(prev, maxCorners=150, qualityLevel=0.01, minDistance=5)
    if pts is None or len(pts) < 8:
        return (1.0, 0.0, 0.0, 0, 0)
    nxt, status, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None, winSize=(15, 15), maxLevel=3)
    ok = status.ravel() == 1
    if ok.sum() < 8:
        return (1.0, 0.0, 0.0, 0, int(len(pts)))
    m, inliers = cv2.estimateAffinePartial2D(pts[ok], nxt[ok], method=cv2.RANSAC, ransacReprojThreshold=1.0)
    if m is None:
        return (1.0, 0.0, 0.0, 0, int(len(pts)))
    h, w = prev.shape
    cx, cy = w / 2, h / 2
    # translation of the frame centre, so a centred zoom reads as zoom only
    dx = m[0, 0] * cx + m[0, 1] * cy + m[0, 2] - cx
    dy = m[1, 0] * cx + m[1, 1] * cy + m[1, 2] - cy
    return (float(math.hypot(m[0, 0], m[1, 0])), float(dx / w), float(dy / w), int(inliers.sum()), int(len(pts)))


def scanFrames(path: str, onProgress=None) -> dict:
    """one decode pass: change score, brightness, 16x9 colour signature and camera step for every frame."""
    times, scores, luma, sigs, steps = [], [], [], [], []
    prevSmall = prevGray = None
    with av.open(path) as container:
        stream = next(s for s in container.streams if s.type == "video")
        stream.thread_type = "AUTO"
        fps = float(stream.average_rate or 30)
        total = stream.frames or 0
        if not total and stream.duration:
            total = int(float(stream.duration * stream.time_base) * fps)
        for i, frame in enumerate(container.decode(stream)):
            w = 160
            h = max(16, int(round(w * frame.height / max(1, frame.width) / 2)) * 2)
            rgb = frame.reformat(width=w, height=h, format="rgb24").to_ndarray()
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            small = cv2.resize(rgb, (64, max(8, h * 64 // w)), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
            times.append(frame.time if frame.time is not None else i / fps)
            luma.append(float(gray.mean()) / 255.0)
            sigs.append(cv2.resize(rgb, (16, 9), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0)
            if prevSmall is None:
                scores.append(0.0)
                steps.append((1.0, 0.0, 0.0, 0, 0))
            else:
                # pixel change plus colour distribution change: robust to motion inside a shot
                pixel = float(np.abs(small - prevSmall).mean())
                histA = np.histogram(prevSmall, bins=16, range=(0, 1))[0]
                histB = np.histogram(small, bins=16, range=(0, 1))[0]
                hist = float(np.abs(histA - histB).sum() / max(1, histA.sum()) / 2)
                scores.append(0.6 * pixel + 0.4 * hist)
                steps.append(cameraStep(prevGray, gray))
            prevSmall, prevGray = small, gray
            if onProgress and total and i % 100 == 0:
                onProgress("frames", min(1.0, i / total), None)
    width, height = (prevGray.shape[1], prevGray.shape[0]) if prevGray is not None else (0, 0)
    return {"fps": fps, "times": times, "scores": np.array(scores), "luma": np.array(luma), "sigs": sigs,
            "steps": steps, "aspect": height / width if width else 0.5625}


def sigDiff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a - b).mean())


def findFlashes(luma: np.ndarray, maxFrames: int = 3, maxFade: int = 15) -> list:
    """white or black frames that jump away and come back: a hard flash (back within a few frames) or a
    flash that fades out (jump to near white or black, then a steady return). [(first, last)]."""
    out, n, i = [], len(luma), 1
    while i < n - 1:
        found = None
        for sign in (1.0, -1.0):
            if sign * (luma[i] - luma[i - 1]) < 0.2:
                continue
            for k in range(1, maxFrames + 1):
                end = i + k
                if end >= n:
                    break
                run = luma[i:end]
                if np.all(sign * (run - luma[i - 1]) > 0.15) and sign * (luma[end - 1] - luma[end]) > 0.15:
                    found = (i, end - 1)
                    break
            extreme = luma[i] > 0.85 if sign > 0 else luma[i] < 0.08
            if not found and extreme and sign * (luma[i] - luma[i - 1]) >= 0.25:
                # fade back: no frame brighter (darker) than the previous one, until it is well away from the peak
                for k in range(1, maxFade + 1):
                    end = i + k
                    if end >= n or sign * (luma[end] - luma[end - 1]) > 0.03:
                        break
                    if sign * (luma[i] - luma[end]) > 0.25:
                        found = (i, end - 1)
                        break
            if found:
                break
        if found:
            out.append(found)
            i = found[1] + 2
        else:
            i += 1
    return out


def cutKind(step: tuple, before: np.ndarray | None = None, after: np.ndarray | None = None) -> str:
    """what happened at a cut: new picture, zoom jump on the same picture, or same framing jump cut."""
    scale, dx, dy, inliers, tracked = step
    # a zoom jump pushes edge points out of frame, a quarter of them agreeing is already the same picture
    if inliers >= 20 and tracked and inliers / tracked >= 0.25 and abs(scale - 1.0) >= 0.06:
        return "punch"
    if before is not None and after is not None:
        # jump cut: most of the frame (wall, desk, background) did not move, only the subject did
        if float((np.abs(before - after).mean(axis=-1) < 0.04).mean()) >= 0.5:
            return "jump"
    elif inliers >= 20 and tracked and inliers / tracked >= 0.5:
        return "jump"
    return "cut"


def detectCuts(scan: dict) -> list:
    """[{frame, t, kind}] with kind cut / punch / jump / flashCut (cut hidden under a flash)."""
    scores = scan["scores"].copy()
    times, sigs, fps = scan["times"], scan["sigs"], scan["fps"]
    n = len(scores)
    cuts = []
    # flashes first: a cut under a flash changes the picture between before and after
    for first, last in findFlashes(scan["luma"]):
        after = min(n - 1, last + 1)
        if first >= 1 and sigDiff(sigs[first - 1], sigs[after]) > 0.08:
            cuts.append({"frame": first, "t": round(float(times[first]), 3), "kind": "flashCut"})
        scores[first:after + 1] = 0.0
    window = max(3, int(fps))
    for i in range(1, n):
        s = scores[i]
        if s <= 0.03:
            continue
        lo, hi = max(1, i - window), min(n, i + window + 1)
        around = np.concatenate([scores[lo:i], scores[i + 1:hi]])
        local = float(np.median(around)) if len(around) else 0.0
        if s > 3.5 * local + 0.02 and s >= scores[max(1, i - 2):i + 3].max():
            # a one frame glitch that comes back to the same picture is not a cut
            back = i + 1 < n and sigDiff(sigs[i - 1], sigs[i + 1]) < 0.02 and scores[i + 1] > 0.5 * s
            if not back:
                cuts.append({"frame": i, "t": round(float(times[i]), 3), "kind": cutKind(scan["steps"][i], sigs[i - 1], sigs[i])})
    # punch-ins barely change the pixels at low resolution: catch them from the camera step itself
    for i in range(1, n):
        if scores[i] > 0 and cutKind(scan["steps"][i]) == "punch" and not any(abs(c["frame"] - i) < 3 for c in cuts):
            near = [abs(scan["steps"][j][0] - 1.0) for j in (i - 1, i + 1) if 0 < j < n]
            if all(d < 0.03 for d in near):
                cuts.append({"frame": i, "t": round(float(times[i]), 3), "kind": "punch"})
    cuts.sort(key=lambda c: c["frame"])
    merged = []
    for c in cuts:
        if merged and c["frame"] - merged[-1]["frame"] < 2.5:
            continue
        merged.append(c)
    return merged


def smooth(values: np.ndarray, radius: int = 3) -> np.ndarray:
    if len(values) < 3:
        return values
    kernel = np.ones(2 * radius + 1) / (2 * radius + 1)
    padded = np.pad(values, radius, mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def describeShot(steps: list, fps: float) -> dict:
    """camera inside one shot: zoom and pan over the shot, shake as jitter around the smooth path."""
    good = [s for s in steps if s[3] >= 12]
    if len(good) < max(2, len(steps) // 2):
        return {"zoom": 0.0, "panX": 0.0, "panY": 0.0, "shake": 0.0, "tracked": False, "punches": []}
    scale = np.array([s[0] for s in good])
    dx = np.array([s[1] for s in good])
    dy = np.array([s[2] for s in good])
    # zoom jumps inside a shot (punch-ins done with keyframes, not cuts)
    punches = [i for i, s in enumerate(steps) if s[3] >= 12 and abs(s[0] - 1.0) >= 0.06]
    calm = np.abs(scale - 1.0) < 0.06
    zoom = float(np.prod(scale[calm]) - 1.0) if calm.any() else 0.0
    jitter = float(np.hypot(dx - smooth(dx), dy - smooth(dy)).mean()) if len(dx) > 4 else 0.0
    # pan is where the camera goes: content sliding left means the camera moves right
    return {"zoom": round(zoom * 100, 1), "panX": round(-float(dx.sum()) * 100, 1), "panY": round(-float(dy.sum()) * 100, 1),
            "shake": round(jitter * 100, 2), "tracked": True, "punches": punches}


def cameraLabel(cam: dict) -> str:
    if not cam["tracked"]:
        return "untracked"
    parts = []
    if abs(cam["zoom"]) >= 3:
        parts.append(f"zoom {'+' if cam['zoom'] > 0 else ''}{cam['zoom']:.0f}%")
    for value, plus, minus in ((cam["panX"], "right", "left"), (cam["panY"], "down", "up")):
        if abs(value) >= 5:
            parts.append(f"pan {plus if value > 0 else minus} {abs(value):.0f}%")
    if cam["shake"] >= 0.4:
        parts.append(f"shake {cam['shake']:.1f}")
    if cam["punches"]:
        parts.append(f"{len(cam['punches'])} punch")
    return ", ".join(parts) or "static"


def buildShots(scan: dict, cuts: list, flashes: list, duration: float) -> list:
    times, steps, fps = scan["times"], scan["steps"], scan["fps"]
    flashFrames = {f for a, b in flashes for f in range(a, b + 2)}
    starts = [(0, "start")] + [(c["frame"], c["kind"]) for c in cuts]
    shots = []
    for k, (frame, kind) in enumerate(starts):
        endFrame = starts[k + 1][0] if k + 1 < len(starts) else len(times)
        inside = [steps[f] for f in range(frame + 1, endFrame) if f not in flashFrames]
        cam = describeShot(inside, fps)
        start = float(times[frame]) if frame < len(times) else duration
        end = float(times[endFrame]) if endFrame < len(times) else duration
        shots.append({"id": k, "start": round(start, 3), "length": round(max(0.0, end - start), 3), "cut": kind,
                      "camera": cam, "flashes": sum(1 for a, _ in flashes if frame <= a < endFrame)})
    return shots


def soundProfile(path: str, info: dict, cuts: list) -> dict:
    if not info.get("hasAudio"):
        return {"hasAudio": False}
    db, windowSec = media.loudnessProfile(path, windowMs=10, track=1)
    if not len(db):
        return {"hasAudio": False}
    floor = float(np.percentile(db, 10))
    onsets = perceive.detectOnsets(db, windowSec, floor)
    duration = len(db) * windowSec
    hits = [c for c in cuts if len(onsets) and float(np.min(np.abs(onsets - c["t"]))) <= 0.07]
    rate = len(onsets) / max(1e-6, duration)
    return {
        "hasAudio": True,
        "medianDb": round(float(np.median(db)), 1),
        "peakDb": round(float(db.max()), 1),
        # near silent windows: pauses, drops before a hit
        "silentShare": round(float((db < float(np.percentile(db, 95)) - 35).mean()), 3),
        "hitsPerMinute": round(rate * 60, 1),
        # cuts landing on a sound hit (sfx, beat, impact), with the share chance alone would give
        "cutsOnHits": round(len(hits) / len(cuts), 3) if cuts else None,
        "cutsOnHitsByChance": round(min(1.0, rate * 0.14), 3),
        "hitTimes": [round(float(t), 2) for t in onsets[:400]],
    }


def speechProfile(captions: list, duration: float) -> dict:
    if not captions:
        return {"captions": False}
    # [music], [laughter] are sound tags, not words
    words = sum(len(re.sub(r"\[[^\]]*\]", " ", c["text"]).split()) for c in captions)
    covered = sum(max(0.0, c["end"] - c["start"]) for c in captions)
    return {"captions": True, "wordsPerMinute": round(words / max(1e-6, duration) * 60, 1),
            "speechShare": round(min(1.0, covered / max(1e-6, duration)), 3),
            "opening": " ".join(c["text"] for c in captions if c["start"] < 5.0)[:240]}


def rhythm(shots: list, duration: float) -> dict:
    lengths = np.array([s["length"] for s in shots if s["length"] > 0.02])
    cutTimes = [s["start"] for s in shots[1:]]
    windows = max(1, int(math.ceil(duration / 5)))
    perWindow = [sum(1 for c in cutTimes if w * 5 <= c < (w + 1) * 5) for w in range(windows)]
    kinds = {}
    for s in shots[1:]:
        kinds[s["cut"]] = kinds.get(s["cut"], 0) + 1
    return {
        "shots": len(shots),
        "cutsPerMinute": round(len(cutTimes) / max(1e-6, duration) * 60, 1),
        "cutKinds": kinds,
        "shotSeconds": {"mean": round(float(lengths.mean()), 2), "median": round(float(np.median(lengths)), 2),
                        "p10": round(float(np.percentile(lengths, 10)), 2), "p90": round(float(np.percentile(lengths, 90)), 2),
                        "longest": round(float(lengths.max()), 2)} if len(lengths) else None,
        "firstCut": cutTimes[0] if cutTimes else None,
        "cutsInFirst3s": sum(1 for c in cutTimes if c < 3.0),
        "cutsPer5s": perWindow[:60],
    }


def cameraSummary(shots: list) -> dict:
    tracked = [s for s in shots if s["camera"]["tracked"]]
    if not tracked:
        return {"trackedShots": 0}
    share = lambda test: round(sum(1 for s in tracked if test(s["camera"])) / len(tracked), 2)
    return {
        "trackedShots": len(tracked),
        "zoomInShare": share(lambda c: c["zoom"] >= 3),
        "zoomOutShare": share(lambda c: c["zoom"] <= -3),
        "panShare": share(lambda c: abs(c["panX"]) >= 5 or abs(c["panY"]) >= 5),
        "shakyShare": share(lambda c: c["shake"] >= 0.4),
        "punchesInsideShots": sum(len(s["camera"]["punches"]) for s in tracked),
    }


def breakdown(path: str, captionFiles: list | None = None, onProgress=None) -> dict:
    """the numbers of a reference edit: cut rhythm and kinds, camera moves, flashes, sound hits, speech."""
    info = media.probe(path)
    scan = scanFrames(path, onProgress)
    duration = float(info.get("duration") or (scan["times"][-1] + 1 / scan["fps"] if scan["times"] else 0.0))
    flashes = findFlashes(scan["luma"])
    cuts = detectCuts(scan)
    shots = buildShots(scan, cuts, flashes, duration)
    if onProgress:
        onProgress("sound", 0.9, None)
    captions = []
    for f in (captionFiles if captionFiles is not None else sidecarCaptions(path)):
        if os.path.isfile(f):
            captions = readVtt(f)
            if captions:
                break
    width, height = info.get("width"), info.get("height")
    return {
        "version": analysisVersion,
        "path": path,
        "duration": round(duration, 2),
        "fps": round(scan["fps"], 3),
        "size": [width, height],
        "vertical": bool(width and height and height > width),
        "rhythm": rhythm(shots, duration),
        "camera": cameraSummary(shots),
        "flashes": [round(float(scan["times"][a]), 3) for a, _ in flashes],
        "sound": soundProfile(path, info, cuts),
        "speech": speechProfile(captions, duration),
        "captionCues": captions[:600],
        "shots": shots,
    }


def loadOrBreakdown(path: str, cacheFolder: Path, onProgress=None, force: bool = False) -> dict:
    cached = Path(cacheFolder) / "reference.json"
    if cached.exists() and not force:
        try:
            data = json.loads(cached.read_text(encoding="utf-8"))
            if data.get("version") == analysisVersion:
                return data
        except ValueError:
            pass
    data = breakdown(path, None, onProgress)
    cached.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def summary(data: dict) -> dict:
    """the metrics without the long lists, for a tool result."""
    out = {k: v for k, v in data.items() if k not in ("shots", "captionCues", "path", "version")}
    out["sound"] = {k: v for k, v in data["sound"].items() if k != "hitTimes"}
    return out


def shotTable(data: dict, fromShot: int = 0, limit: int = 80) -> str:
    hits = np.array(data["sound"].get("hitTimes") or [])
    lines = ["columns: shot, start, length, how it starts (cut, punch = zoom jump, jump = same framing, flashCut), "
             "camera inside the shot, flashes, hit = sound hit on the cut"]
    shots = data["shots"][fromShot:fromShot + limit]
    for s in shots:
        hit = len(hits) and s["id"] > 0 and float(np.min(np.abs(hits - s["start"]))) <= 0.07
        extra = (f"  {s['flashes']} flash" if s["flashes"] else "") + ("  hit" if hit else "")
        lines.append(f"#{s['id']:<3} {sheets.fmtTime(s['start'])}  {s['length']:5.2f}s  {s['cut']:<8} {cameraLabel(s['camera'])}{extra}")
    if fromShot + limit < len(data["shots"]):
        lines.append(f"... {len(data['shots']) - fromShot - limit} more shots, call again with fromShot={fromShot + limit}")
    return "\n".join(lines)


def shotSheet(data: dict, outPath: Path, fromShot: int = 0, count: int = 20, cols: int = 5) -> dict:
    """one thumbnail per shot (its middle frame), labelled with shot id, start and length: the edit at a glance."""
    shots = data["shots"][fromShot:fromShot + count]
    if not shots:
        raise ValueError(f"no shot {fromShot}, this reference has {len(data['shots'])}")
    targets = [s["start"] + s["length"] / 2 for s in shots]
    picked = [None] * len(targets)
    with av.open(data["path"]) as container:
        stream = next(s for s in container.streams if s.type == "video")
        stream.thread_type = "AUTO"
        container.seek(int(max(0.0, targets[0] - 1.0) / stream.time_base), stream=stream, backward=True)
        k = 0
        for frame in container.decode(stream):
            if frame.time is None:
                continue
            while k < len(targets) and frame.time >= targets[k] - 0.5 / max(1.0, data["fps"]):
                picked[k] = frame.to_image()
                k += 1
            if k >= len(targets):
                break
    cellW = 160 if data["vertical"] else 256
    cellH = int(round(cellW * (data["size"][1] or 9) / (data["size"][0] or 16)))
    rows = (len(shots) + cols - 1) // cols
    sheet = Image.new("RGB", (min(cols, len(shots)) * cellW, rows * (cellH + sheets.labelHeight)), (12, 12, 12))
    draw = ImageDraw.Draw(sheet)
    labelFont = sheets.font(14)
    for i, (shot, image) in enumerate(zip(shots, picked)):
        x, y = (i % cols) * cellW, (i // cols) * (cellH + sheets.labelHeight)
        if image is not None:
            sheet.paste(image.convert("RGB").resize((cellW, cellH)), (x, y + sheets.labelHeight))
        draw.text((x + 4, y + 3), f"#{shot['id']} {sheets.fmtTime(shot['start'])} {shot['length']:.1f}s",
                  fill=(255, 220, 90), font=labelFont)
    outPath = Path(outPath)
    outPath.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(outPath, quality=80)
    return {"path": str(outPath), "shots": [s["id"] for s in shots]}


def cardTemplate(name: str, data: dict) -> str:
    r = data["rhythm"]
    shot = r["shotSeconds"] or {}
    cam = data["camera"]
    snd = data["sound"]
    lines = [
        f"# {name}",
        "",
        f"- format: {'vertical' if data['vertical'] else 'horizontal'}, {data['duration']:.0f} s",
        f"- rhythm: {r['cutsPerMinute']} cuts/min, median shot {shot.get('median')} s (p10 {shot.get('p10')}, p90 {shot.get('p90')}), "
        f"first cut at {r['firstCut']} s, {r['cutsInFirst3s']} cuts in the first 3 s, cut kinds "
        + ", ".join(f"{k} {v}" for k, v in sorted(r["cutKinds"].items(), key=lambda kv: -kv[1])),
        f"- pacing per 5 s: {r['cutsPer5s']}",
    ]
    if cam.get("trackedShots"):
        lines.append(f"- camera: zoom in {cam['zoomInShare']:.0%} of shots, out {cam['zoomOutShare']:.0%}, pans {cam['panShare']:.0%}, "
                     f"shaky {cam['shakyShare']:.0%}, {cam['punchesInsideShots']} punch-ins inside shots, "
                     f"{len(data['flashes'])} flash{'' if len(data['flashes']) == 1 else 'es'} ({len(data['flashes']) / max(1e-6, data['duration']) * 60:.1f} per min)")
    if snd.get("hasAudio"):
        onHits = f"{snd['cutsOnHits']:.0%} of cuts on a hit (chance {snd['cutsOnHitsByChance']:.0%})" if snd["cutsOnHits"] is not None else "no cuts"
        lines.append(f"- sound: {snd['hitsPerMinute']} hits/min, {onHits}, near silence {snd['silentShare']:.1%} of the time")
    if data["speech"].get("captions"):
        lines.append(f"- speech: {data['speech']['wordsPerMinute']} words/min, speech {data['speech']['speechShare']:.0%} of the runtime")
    lines += [
        "- hook: (first 2 s: what is shown, what is said, why you keep watching)",
        "- structure: (setup, escalation, payoff, ending or loop back to the start)",
        "- captions: (font, weight, size, position, words per caption, animation, colours, emphasis)",
        "- motion: (zooms, shakes, slides, transitions, speed ramps: where and how often)",
        "- sound: (music, sfx on cuts, risers, silences before hits, voice processing)",
        "- what to steal: (3 concrete rules for the user's edits, with numbers)",
    ]
    return "\n".join(lines)
