"""one local pass over the rush: thumbnails, motion, cuts, levels, transients, vad speech, 1 to 8 s idle / active / speech beats."""

import bisect
import json
import math
import time
from pathlib import Path

import av
import numpy as np

import media

perceptionVersion = 1
sampleEvery = 0.5
thumbEvery = 1.0
thumbWidth = 320
shotChangeThreshold = 0.18


def noop(stage, pct=None, message=None):
    pass


def analyzeVideo(mediaPath: str, outDir: Path, duration: float, onProgress=noop) -> dict:
    thumbDir = outDir / "thumbs"
    thumbDir.mkdir(parents=True, exist_ok=True)
    samples, thumbs, shotChanges = [], [], []
    with av.open(mediaPath) as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            return {"samples": [], "thumbs": [], "shotChanges": [], "thumbSize": None}
        stream.thread_type = "AUTO"
        try:
            # sampling every 0.5 s does not need b-frames, roughly halves decode time
            stream.codec_context.skip_frame = "NONREF"
        except Exception:
            pass

        lastSample = -1e9
        lastThumb = -1e9
        lastReport = 0.0
        previous = None
        thumbSize = None
        for frame in container.decode(stream):
            t = frame.time
            if t is None or t - lastSample < sampleEvery - 1e-6:
                continue
            lastSample = t
            small = frame.reformat(width=64, height=36, format="gray").to_ndarray().astype(np.float32) / 255.0
            motion = float(np.mean(np.abs(small - previous))) if previous is not None else 0.0
            samples.append([round(t, 3), round(motion, 5), round(float(small.mean()), 4)])
            if previous is not None and motion >= shotChangeThreshold:
                shotChanges.append(round(t, 3))
            previous = small

            if t - lastThumb >= thumbEvery - 1e-6:
                lastThumb = t
                rotation = int(round(getattr(frame, "rotation", 0) or 0))
                height = max(2, int(round(thumbWidth * frame.height / frame.width / 2)) * 2)
                image = frame.reformat(width=thumbWidth, height=height, format="rgb24").to_image()
                if rotation:
                    image = image.rotate(rotation, expand=True)
                thumbSize = image.size
                name = f"{t:09.3f}.jpg"
                image.save(thumbDir / name, quality=72)
                thumbs.append([round(t, 3), name])

            if duration and t - lastReport > duration / 50:
                lastReport = t
                onProgress("video", min(t / duration, 1.0), None)
    return {"samples": samples, "thumbs": thumbs, "shotChanges": shotChanges, "thumbSize": thumbSize}


def detectOnsets(db: np.ndarray, windowSec: float, floorDb: float) -> np.ndarray:
    """transients: level jumps of 8 db over the last 60 ms, well above room tone."""
    look = max(1, int(round(0.06 / windowSec)))
    refractory = look
    onsets = []
    last = -refractory
    for i in range(look, len(db)):
        if db[i] > floorDb + 10 and db[i] - db[i - look:i].min() > 8 and i - last >= refractory:
            onsets.append(i * windowSec)
            last = i
    return np.array(onsets)


def analyzeAudioTrack(mediaPath: str, track: int, duration: float) -> dict:
    db, windowSec = media.loudnessProfile(mediaPath, windowMs=20, track=track)
    if not len(db):
        return {"track": track, "bins": []}
    floor = float(np.percentile(db, 10))
    onsets = detectOnsets(db, windowSec, floor)
    binCount = max(1, int(math.ceil(duration / sampleEvery)))
    perBin = max(1, int(round(sampleEvery / windowSec)))
    bins = []
    for b in range(binCount):
        chunk = db[b * perBin:(b + 1) * perBin]
        if not len(chunk):
            bins.append([-100.0, 0])
            continue
        # energy mean in db, not mean of db values
        level = 10 * math.log10(float(np.mean(10 ** (chunk / 10))) + 1e-12)
        bins.append([round(level, 1), 0])
    for t in onsets:
        b = int(t / sampleEvery)
        if b < len(bins):
            bins[b][1] += 1
    return {"track": track, "floorDb": round(floor, 1), "peakDb": round(float(db.max()), 1), "bins": bins}


def detectSpeech(mediaPath: str, track: int) -> list:
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except Exception:
        return []
    audio = media.decodeAudioMono(mediaPath, 16000, track=track)
    if not len(audio):
        return []
    stamps = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=400, speech_pad_ms=100))
    return [[round(s["start"] / 16000, 2), round(s["end"] / 16000, 2)] for s in stamps]


def coverage(ranges: list, start: float, end: float) -> float:
    total = 0.0
    for s, e in ranges:
        if e <= start:
            continue
        if s >= end:
            break
        total += min(e, end) - max(s, start)
    return total / max(1e-6, end - start)


def segmentBeats(duration: float, samples: list, audio: list, speech: list, shotChanges: list,
                 maxBeat: float = 8.0, minBeat: float = 1.0) -> tuple:
    binCount = max(1, int(math.ceil(duration / sampleEvery)))
    motion = np.zeros(binCount)
    for t, m, _b in samples:
        b = min(binCount - 1, int(round(t / sampleEvery)))
        motion[b] = max(motion[b], m)
    level = np.full(binCount, -100.0)
    onsets = np.zeros(binCount)
    for trackInfo in audio:
        for b, (lv, on) in enumerate(trackInfo["bins"][:binCount]):
            level[b] = max(level[b], lv)
            onsets[b] += on
    speechCov = np.array([coverage(speech, b * sampleEvery, (b + 1) * sampleEvery) for b in range(binCount)])

    # adaptive: what counts as movement depends on the camera noise of this rush
    motionThreshold = max(0.006, 2.5 * float(np.percentile(motion, 20))) if len(samples) else 1.0
    states = []
    for b in range(binCount):
        if speechCov[b] >= 0.5:
            states.append("speech")
        elif motion[b] > motionThreshold or onsets[b] >= 2:
            states.append("active")
        else:
            states.append("idle")
    smoothed = states[:]
    for b in range(1, binCount - 1):
        window = states[b - 1:b + 2]
        top = max(set(window), key=window.count)
        if window.count(top) >= 2:
            smoothed[b] = top

    cutBins = {min(binCount - 1, int(round(t / sampleEvery))) for t in shotChanges}
    runs = []
    for b, state in enumerate(smoothed):
        if not runs or runs[-1]["state"] != state or b in cutBins:
            runs.append({"state": state, "first": b, "last": b, "cut": b in cutBins})
        else:
            runs[-1]["last"] = b

    merged = []
    for run in runs:
        length = (run["last"] - run["first"] + 1) * sampleEvery
        if merged and length < minBeat and not run["cut"]:
            merged[-1]["last"] = run["last"]
        else:
            merged.append(dict(run))

    beats = []
    for run in merged:
        start = run["first"] * sampleEvery
        end = min(duration, (run["last"] + 1) * sampleEvery)
        pieces = max(1, int(math.ceil((end - start) / maxBeat)))
        step = (end - start) / pieces
        for p in range(pieces):
            s, e = start + p * step, start + (p + 1) * step
            b0, b1 = int(s / sampleEvery), max(int(s / sampleEvery) + 1, int(math.ceil(e / sampleEvery)))
            beats.append({
                "id": len(beats),
                "start": round(s, 2),
                "end": round(e, 2),
                "state": run["state"],
                "cut": bool(run["cut"] and p == 0),
                "motion": round(float(motion[b0:b1].mean()), 4),
                "levelDb": round(float(level[b0:b1].max()), 1),
                "onsetsPerSec": round(float(onsets[b0:b1].sum()) / max(1e-6, e - s), 2),
                "speech": round(float(speechCov[b0:b1].mean()), 2),
            })
    return beats, motionThreshold


def perceive(mediaPath: str, outDir: Path, info: dict, voiceTrack: int = 1, withSpeech: bool = True,
             onProgress=noop) -> dict:
    started = time.time()
    duration = float(info.get("duration") or 0)
    onProgress("video", 0.0, "watching the rush (motion, cuts, thumbnails)")
    video = analyzeVideo(mediaPath, outDir, duration, onProgress) if info.get("hasVideo") else \
        {"samples": [], "thumbs": [], "shotChanges": [], "thumbSize": None}

    audio = []
    streams = info.get("audioStreams") or []
    for i, _stream in enumerate(streams, start=1):
        onProgress("audio", (i - 1) / max(1, len(streams)), f"listening to A{i}")
        audio.append(analyzeAudioTrack(mediaPath, i, duration))

    speech = []
    if withSpeech and streams:
        onProgress("speech", 0.0, f"looking for speech on A{voiceTrack}")
        speech = detectSpeech(mediaPath, voiceTrack)

    beats, motionThreshold = segmentBeats(duration, video["samples"], audio, speech, video["shotChanges"])
    speechSec = sum(e - s for s, e in speech)
    idleSec = sum(b["end"] - b["start"] for b in beats if b["state"] == "idle")
    result = {
        "version": perceptionVersion,
        "mediaPath": mediaPath,
        "duration": round(duration, 3),
        "sampleEvery": sampleEvery,
        "thumbEvery": thumbEvery,
        "thumbSize": video["thumbSize"],
        "thumbs": video["thumbs"],
        "samples": video["samples"],
        "shotChanges": video["shotChanges"],
        "audio": [{k: v for k, v in a.items()} for a in audio],
        "speech": speech,
        "voiceTrack": voiceTrack,
        "beats": beats,
        "stats": {
            "beats": len(beats),
            "speechRatio": round(speechSec / max(1e-6, duration), 3),
            "idleRatio": round(idleSec / max(1e-6, duration), 3),
            "shotChanges": len(video["shotChanges"]),
            "motionThreshold": round(motionThreshold, 4),
            "seconds": round(time.time() - started, 1),
        },
    }
    with open(outDir / "perception.json", "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    onProgress("beats", 1.0, f"{len(beats)} beats, {round(100 * result['stats']['idleRatio'])}% idle, "
                             f"{round(100 * result['stats']['speechRatio'])}% speech")
    return result


def loadPerception(outDir: Path) -> dict | None:
    try:
        with open(outDir / "perception.json", "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if data.get("version") == perceptionVersion else None
    except (OSError, ValueError):
        return None


def nearestThumb(perception: dict, t: float):
    times = [x[0] for x in perception["thumbs"]]
    if not times:
        return None
    i = bisect.bisect_left(times, t)
    candidates = [j for j in (i - 1, i) if 0 <= j < len(times)]
    j = min(candidates, key=lambda k: abs(times[k] - t))
    return perception["thumbs"][j]


def beatAt(perception: dict, t: float):
    starts = [b["start"] for b in perception["beats"]]
    i = bisect.bisect_right(starts, t) - 1
    return perception["beats"][i] if 0 <= i < len(starts) else None


def formatOverview(perception: dict, logEntries: list | None = None, fromBeat: int = 0, limit: int = 400) -> str:
    """compact beat table for claude, idle stretches folded into one line."""
    def fmt(t):
        m = int(t // 60)
        return f"{m:02d}:{t - m * 60:04.1f}"

    st = perception["stats"]
    tracks = ", ".join(f"A{a['track']} peak {a.get('peakDb')} dB floor {a.get('floorDb')} dB" for a in perception["audio"])
    lines = [
        f"rush {fmt(perception['duration'])}, {st['beats']} beats, idle {round(100 * st['idleRatio'])}%, "
        f"speech {round(100 * st['speechRatio'])}% (A{perception.get('voiceTrack', 1)}), hard cuts {st['shotChanges']}",
        f"audio: {tracks or 'none'}",
        "columns: beat, range, length, state, motion, loudest level dB, transients per s, speech share, cut = hard cut at start",
    ]
    logged = {}
    for e in logEntries or []:
        logged.setdefault(round(e["start"], 1), e)

    beats = [b for b in perception["beats"] if b["id"] >= fromBeat]
    shown = 0
    i = 0
    while i < len(beats) and shown < limit:
        b = beats[i]
        if b["state"] == "idle":
            j = i
            while j + 1 < len(beats) and beats[j + 1]["state"] == "idle" and not beats[j + 1]["cut"]:
                j += 1
            if j > i:
                e = beats[j]
                lines.append(f"b{b['id']}-b{e['id']} {fmt(b['start'])}-{fmt(e['end'])} {e['end'] - b['start']:.1f}s idle")
                i = j + 1
                shown += 1
                continue
        note = ""
        entry = logged.get(round(b["start"], 1))
        if entry:
            note = f" | logged {entry['interest']}/10: {entry['description'][:60]}"
        lines.append(
            f"b{b['id']} {fmt(b['start'])}-{fmt(b['end'])} {b['end'] - b['start']:.1f}s {b['state']} "
            f"mot {b['motion']:.3f} lvl {b['levelDb']:.0f} on {b['onsetsPerSec']:.1f} sp {b['speech']:.1f}"
            f"{' cut' if b['cut'] else ''}{note}"
        )
        shown += 1
        i += 1
    if i < len(beats):
        lines.append(f"... more beats, call again with fromBeat={beats[i]['id']}")
    return "\n".join(lines)
