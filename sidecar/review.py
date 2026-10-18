"""watch a rendered edit: shots, rhythm, black, frozen and silent stretches, loudness, a frame of every shot."""

import os
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw

import eyes
import media
from sheets import fmtTime, font

h264Folder = "4E49434B_48323634"
presetNames = {"review": "02 - Match Source - Low bitrate.epr", "master": "01 - Match Source - High bitrate.epr"}
defaultRoots = [str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Adobe")]

youtubeLufs = -14.0
sheetColumns = 6
cellWidth = 256

blackMean, blackHigh, blackMinSec = 0.07, 0.15, 0.2
frozenMax, frozenMinSec = 1.5 / 255, 1.0
cutMin, cutRatio, cutOverLocal, cutWindow = 0.015, 5.0, 0.01, 8
maxListed = 6
silentDb, silentMinSec, silentWindow = -50.0, 0.75, 0.05

# itu-r bs.1770 k-weighting at 48 khz: the high shelf, then the rlb high pass
kStages = [((1.53512485958697, -2.69169618940638, 1.19839281085285), (1.0, -1.69065929318241, 0.73248077421585)),
           ((1.0, -2.0, 1.0), (1.0, -1.99004745483398, 0.99007225036621))]
loudRate = 48000


def presetFor(kind: str, premiereVersion: str, roots: list | None = None) -> str:
    """premiere's own h.264 match source preset (review = low bitrate, master = high), so a vertical edit exports vertical."""
    if kind not in presetNames:
        raise ValueError(f"preset kind {kind!r}: review or master")
    year = 2000 + int(str(premiereVersion).split(".")[0])
    looked = []
    for root in roots or defaultRoots:
        path = Path(root) / f"Adobe Premiere Pro {year}" / "MediaIO" / "systempresets" / h264Folder / presetNames[kind]
        looked.append(str(path))
        if path.is_file():
            return str(path)
    raise ValueError(f"no {presetNames[kind]} for premiere {premiereVersion}, looked at: " + "; ".join(looked))


def kWeighted(x: np.ndarray, block: int = 1 << 20) -> np.ndarray:
    """in the frequency domain, block by block with a second of lead-in, since numpy has no iir filter."""
    lead = loudRate
    size = 1 << int(np.ceil(np.log2(block + 2 * lead)))
    z1 = np.exp(-2j * np.pi * np.fft.rfftfreq(size, 1 / loudRate) / loudRate)
    h = np.ones_like(z1)
    for b, a in kStages:
        h *= (b[0] + b[1] * z1 + b[2] * z1 ** 2) / (a[0] + a[1] * z1 + a[2] * z1 ** 2)
    out = np.empty_like(x, dtype=np.float64)
    for s in range(0, x.shape[1], block):
        first = max(0, s - lead)
        seg = x[:, first:s + block]
        y = np.fft.irfft(np.fft.rfft(seg, size, axis=1) * h, size, axis=1)
        out[:, s:s + block] = y[:, s - first:s - first + min(block, x.shape[1] - s)]
    return out


def integratedLoudness(samples: np.ndarray, rate: int) -> float | None:
    """lufs per bs.1770-4 (400 ms blocks every 100 ms, gates at -70 and -10 lu), None when everything is gated out."""
    if rate != loudRate:
        raise ValueError(f"loudness is measured at {loudRate} hz, resample first")
    x = np.atleast_2d(np.asarray(samples, dtype=np.float64))
    step = loudRate // 10
    segments = x.shape[1] // step
    if segments < 4:
        return None
    sq = kWeighted(x)[:, :segments * step] ** 2
    parts = sq.reshape(x.shape[0], segments, step).sum(axis=2)
    blocks = (parts[:, :-3] + parts[:, 1:-2] + parts[:, 2:-1] + parts[:, 3:]) / (4 * step)
    weights = np.array([1.41 if i >= 3 and x.shape[0] >= 5 else 1.0 for i in range(x.shape[0])])
    power = (weights[:, None] * blocks).sum(axis=0)
    with np.errstate(divide="ignore"):
        levels = -0.691 + 10 * np.log10(power)
    kept = power[levels > -70.0]
    if not kept.size:
        return None
    relative = -0.691 + 10 * np.log10(kept.mean()) - 10.0
    kept = power[(levels > -70.0) & (levels > relative)]
    return float(-0.691 + 10 * np.log10(kept.mean()))


def runs(flags, starts: list, step: float, minSec: float, backOne: bool = False) -> list:
    """(start, end) of each True stretch at least minSec long; backOne: flags mark repeats, so start a frame earlier."""
    out, i, n = [], 0, len(flags)
    while i < n:
        if not flags[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and flags[j + 1]:
            j += 1
        first = i - 1 if backOne and i > 0 else i
        a, b = starts[first], starts[j] + step
        if b - a >= minSec - 1e-6:
            out.append((round(a, 3), round(b, 3)))
        i = j + 1
    return out


def mergeRuns(stretches: list, gap: float) -> list:
    """stretches split by a blip shorter than gap become one."""
    out = []
    for a, b in stretches:
        if out and a - out[-1][1] <= gap + 1e-6:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def stretchesText(label: str, stretches: list, duration: float, note: str = "") -> list:
    """each stretch when there are few, a summary when listing them would bury the rest."""
    if len(stretches) <= maxListed:
        return [f"{label} {fmtTime(a)}-{fmtTime(b)}{note}" for a, b in stretches]
    total = sum(b - a for a, b in stretches)
    a, b = max(stretches, key=lambda s: s[1] - s[0])
    return [f"{label} {total:.1f} s of {duration:.1f} s in {len(stretches)} stretches, longest {b - a:.1f} s at {fmtTime(a)}{note}"]


def scanPicture(mediaPath: str, onProgress) -> dict:
    times, means, highs, diffs, peaks = [], [], [], [], []
    previous = None
    with av.open(mediaPath) as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            return {"times": [], "black": [], "frozen": [], "cuts": []}
        stream.thread_type = "AUTO"
        duration = float(stream.duration * stream.time_base) if stream.duration else None
        for frame in container.decode(stream):
            if frame.time is None:
                continue
            g = frame.reformat(width=64, height=36, format="gray").to_ndarray().astype(np.float32) / 255.0
            times.append(float(frame.time))
            means.append(float(g.mean()))
            highs.append(float(np.percentile(g, 99)))
            change = np.abs(g - previous) if previous is not None else np.zeros_like(g)
            diffs.append(float(change.mean()))
            peaks.append(float(change.max()))
            previous = g
            if duration and len(times) % 300 == 0:
                onProgress("review", min(0.9, 0.9 * frame.time / duration), f"watching {fmtTime(frame.time)}")
    if not times:
        return {"times": [], "black": [], "frozen": [], "cuts": []}
    step = float(np.median(np.diff(times))) if len(times) > 1 else 1 / 30
    black = [m < blackMean and h < blackHigh for m, h in zip(means, highs)]
    # a held frame decodes identical; the slightest real movement leaves a sharp local change
    frozen = [p < frozenMax and not k for p, k in zip(peaks, black)]
    frozen[0] = False
    d = np.array(diffs)
    cuts = []
    for i in range(1, len(d)):
        around = np.concatenate([d[max(1, i - cutWindow):i], d[i + 1:i + 1 + cutWindow]])
        # obs repeats every other picture: those zero changes would drag the local level down
        around = around[around > 1e-4]
        base = float(np.median(around)) if around.size else 0.0
        if d[i] >= cutMin and d[i] >= cutRatio * base + cutOverLocal:
            if cuts and cuts[-1][0] >= i - 2:
                if d[i] > cuts[-1][1]:
                    cuts[-1] = (i, d[i])
                continue
            cuts.append((i, d[i]))
    return {"times": times, "step": step, "black": runs(black, times, step, blackMinSec),
            "frozen": runs(frozen, times, step, frozenMinSec, backOne=True),
            "cuts": [round(times[i], 3) for i, _score in cuts]}


def readSound(mediaPath: str) -> tuple:
    """(stereo float samples at 48 khz, time of the first sample) or (None, 0)."""
    with av.open(mediaPath) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            return None, 0.0
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=loudRate)
        chunks, offset = [], None
        for frame in container.decode(stream):
            if offset is None:
                offset = float(frame.time or 0.0)
            chunks.extend(out.to_ndarray() for out in resampler.resample(frame))
        chunks.extend(out.to_ndarray() for out in resampler.resample(None))
    if not chunks:
        return None, 0.0
    return np.concatenate(chunks, axis=1), offset or 0.0


def scanSound(mediaPath: str) -> dict:
    audio, offset = readSound(mediaPath)
    if audio is None or not audio.size:
        return {"silent": [], "loudness": None, "peak": None}
    window = int(loudRate * silentWindow)
    count = audio.shape[1] // window
    mono = audio.mean(axis=0)[:count * window].reshape(count, window)
    db = 20 * np.log10(np.sqrt((mono.astype(np.float64) ** 2).mean(axis=1)) + 1e-12)
    starts = [offset + i * silentWindow for i in range(count)]
    peak = float(np.abs(audio).max())
    return {"silent": mergeRuns(runs(list(db < silentDb), starts, silentWindow, silentMinSec), 0.25),
            "loudness": integratedLoudness(audio, loudRate),
            "peak": round(20 * np.log10(peak), 2) if peak > 0 else None}


def scanMedia(mediaPath: str, onProgress=None) -> dict:
    progress = onProgress or (lambda *a, **k: None)
    info = media.probe(mediaPath)
    picture = scanPicture(mediaPath, progress)
    progress("review", 0.92, "listening to the mix")
    sound = scanSound(mediaPath)
    duration = float(info.get("duration") or 0.0)
    edges = [0.0] + [c for c in picture["cuts"] if 0.0 < c < duration] + [duration]
    shots = [{"start": round(a, 3), "end": round(b, 3)} for a, b in zip(edges, edges[1:]) if b - a > 1e-3]
    return {"duration": duration, "width": info.get("width"), "height": info.get("height"), "fps": info.get("fps"),
            "cuts": picture["cuts"], "shots": shots, "black": picture["black"], "frozen": picture["frozen"],
            "silent": sound["silent"], "loudness": sound["loudness"], "peak": sound["peak"]}


def sheetShots(shots: list, maxCells: int) -> list:
    if len(shots) <= maxCells:
        return list(shots)
    picks = sorted({int(round(i)) for i in np.linspace(0, len(shots) - 1, maxCells)})
    return [shots[i] for i in picks]


def shotSheet(mediaPath: str, shots: list, outPath: str, maxCells: int = 48) -> str:
    """a frame from the middle of every shot, labelled with its number, start and length."""
    picked = sheetShots(shots, maxCells)
    numbers = {id(s): i + 1 for i, s in enumerate(shots)}
    cellDir = Path(outPath).with_suffix("")
    grabbed = eyes.grabFrames(mediaPath, [(s["start"] + s["end"]) / 2 for s in picked], str(cellDir), maxSide=cellWidth)
    images = [Image.open(g["path"]).convert("RGB") for g in grabbed]
    aspect = images[0].height / images[0].width if images else 9 / 16
    cellHeight = max(1, int(round(cellWidth * aspect)))
    columns = max(1, min(sheetColumns, len(picked)))
    rows = max(1, -(-len(picked) // columns))
    sheet = Image.new("RGB", (columns * cellWidth, rows * cellHeight), (16, 16, 16))
    draw = ImageDraw.Draw(sheet)
    label = font(14)
    for k, shot in enumerate(picked):
        x, y = (k % columns) * cellWidth, (k // columns) * cellHeight
        if k < len(images):
            sheet.paste(images[k].resize((cellWidth, cellHeight)), (x, y))
        text = f"#{numbers[id(shot)]} {fmtTime(shot['start'])} ({shot['end'] - shot['start']:.1f}s)"
        draw.rectangle([x, y, x + cellWidth, y + 18], fill=(0, 0, 0))
        draw.text((x + 4, y + 2), text, fill=(255, 220, 90), font=label)
    sheet.save(outPath, quality=85)
    return outPath


def loudnessVerdict(lufs: float | None, peak: float | None) -> str:
    if lufs is None:
        return "no sound"
    gap = lufs - youtubeLufs
    if abs(gap) <= 1.0:
        verdict = "on target for youtube"
    elif gap < 0:
        verdict = f"{-gap:.1f} dB under youtube's {youtubeLufs:g} LUFS, youtube never turns a quiet video up: raise the mix"
    else:
        verdict = f"{gap:.1f} dB over youtube's {youtubeLufs:g} LUFS, youtube turns it down: louder gains nothing"
    text = f"loudness {lufs:.1f} LUFS integrated"
    if peak is not None:
        text += f", peak {peak:.1f} dBFS"
        if peak > -1.0:
            verdict += "; peak above -1 dBFS, it may clip once encoded"
    return f"{text}: {verdict}"


def describe(report: dict) -> str:
    shots = report.get("shots") or []
    lines = [f"render {fmtTime(report['duration'])}, {report.get('width')}x{report.get('height')} at "
             f"{(report.get('fps') or 0):g} fps, {len(shots)} shots"]
    lengths = [s["end"] - s["start"] for s in shots]
    if lengths:
        longest = int(np.argmax(lengths))
        lines.append(f"rhythm: median shot {np.median(lengths):.1f} s, mean {np.mean(lengths):.1f} s, longest "
                     f"{lengths[longest]:.1f} s at {fmtTime(shots[longest]['start'])}, "
                     f"{sum(1 for x in lengths if x < 0.5)} under 0.5 s")
    duration = float(report.get("duration") or 0.0)
    problems = stretchesText("black", report.get("black") or [], duration)
    problems += stretchesText("frozen", report.get("frozen") or [], duration, " (a held frame or a perfectly still shot)")
    problems += stretchesText("silent", report.get("silent") or [], duration)
    lines.append("check: " + "; ".join(problems) if problems else "no black, frozen or silent stretch")
    lines.append(loudnessVerdict(report.get("loudness"), report.get("peak")))
    return "\n".join(lines)
