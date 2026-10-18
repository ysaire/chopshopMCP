"""shot matching: lumetri exposure, temperature and tint moved until a shot's premiere render measures like the reference."""

import math

import numpy as np

lumaWeights = np.array([0.2126, 0.7152, 0.0722])
measureWidth = 480
bandLow, bandHigh = 0.12, 0.9
# channel spread as a share of luma: loose enough for a lamp's cast, tight once that cast is removed
looseSpread, neutralSpread = 0.4, 0.15
neutralMinShare = 0.03


def shrink(rgb: np.ndarray) -> np.ndarray:
    rgb = np.asarray(rgb, dtype=np.float64)
    if rgb.shape[1] > measureWidth:
        step = int(math.ceil(rgb.shape[1] / measureWidth))
        rgb = rgb[::step, ::step]
    return rgb.reshape(-1, 3)


def greyPixels(rgb: np.ndarray) -> np.ndarray:
    """pixels grey under the frame's own light (a white box under a warm lamp, never a blue pad), else the mid band."""
    px = shrink(rgb)
    luma = px @ lumaWeights
    band = (luma > bandLow) & (luma < bandHigh)
    if not band.any():
        return np.ones(len(px), dtype=bool)
    pick = band & ((px.max(1) - px.min(1)) < looseSpread * luma)
    for _ in range(3):
        if pick.mean() < neutralMinShare:
            return band
        cast = px[pick].mean(0) + 1e-4
        flat = px / cast * cast.mean()
        pick = band & ((flat.max(1) - flat.min(1)) < neutralSpread * luma)
    return pick if pick.mean() >= neutralMinShare else band


def frameStats(rgb: np.ndarray, pick: np.ndarray | None = None) -> dict:
    """0-1 display values: luma, 5th/95th percentiles, warm = ln(R/B), green = ln(G / sqrt(R B)) on grey pixels.
    reuse the pick after a correction: picked anew, grey pixels always look grey."""
    px = shrink(rgb)
    if pick is None:
        pick = greyPixels(rgb)
    luma = px @ lumaWeights
    r, g, b = (float(np.mean(px[pick, k])) + 1e-4 for k in range(3))
    return {"luma": float(luma.mean()), "lo": float(np.percentile(luma, 5)), "hi": float(np.percentile(luma, 95)),
            "warm": math.log(r / b), "green": math.log(g / math.sqrt(r * b)), "grey": round(float(pick.mean()), 3)}


def mergeStats(stats: list) -> dict:
    return {k: float(np.mean([s[k] for s in stats])) for k in ("luma", "lo", "hi", "warm", "green", "grey")}


# per slider unit on premiere 25 renders: log2 mean luma (less above +1), ln(R/B), ln(G / sqrt(R B)) on grey pixels
slopes = {"Exposure": 0.36, "Temperature": 0.00577, "Tint": -0.00433}
limits = {"Exposure": (-4.0, 4.0), "Temperature": (-100.0, 100.0), "Tint": (-100.0, 100.0)}
steps = {"Exposure": 0.01, "Temperature": 0.1, "Tint": 0.1}
tolerance = {"Exposure": 0.04, "Temperature": 0.015, "Tint": 0.015}


def gaps(stats: dict, target: dict) -> dict:
    return {"Exposure": math.log2(max(target["luma"], 1e-4) / max(stats["luma"], 1e-4)),
            "Temperature": target["warm"] - stats["warm"], "Tint": target["green"] - stats["green"]}


def sits(gap: dict) -> bool:
    return all(abs(gap[k]) <= tolerance[k] for k in gap)


def matchShot(render, values: dict, target: dict, passes: int = 3) -> dict:
    """render(None) gives the clip's frames as they are, render(values) sets the sliders first; each pass relearns the slopes."""
    frames = render(None)
    picks = [greyPixels(f) for f in frames]

    def measure(fs):
        return mergeStats([frameStats(f, p) for f, p in zip(fs, picks)])

    current = {k: float(values.get(k, 0.0)) for k in slopes}
    stats = before = measure(frames)
    slope = dict(slopes)
    limited, done = [], 0
    while done < passes and not sits(gaps(stats, target)):
        gap = gaps(stats, target)
        nxt, limited = {}, []
        for k in slopes:
            low, high = limits[k]
            want = current[k] + gap[k] / slope[k]
            if want < low or want > high:
                limited.append(k)
            nxt[k] = round(min(high, max(low, want)) / steps[k]) * steps[k]
            nxt[k] = round(nxt[k], 2)
        if nxt == current:
            break
        after = measure(render(nxt))
        done += 1
        moved = gaps(stats, target)
        left = gaps(after, target)
        for k in slopes:
            dv = nxt[k] - current[k]
            if abs(dv) < steps[k]:
                continue
            seen = (moved[k] - left[k]) / dv
            if seen * slopes[k] > 0 and 0.2 < seen / slopes[k] < 5:
                slope[k] = seen
        current, stats = nxt, after
    return {"values": current, "before": before, "after": stats, "passes": done, "reached": sits(gaps(stats, target)),
            "limited": limited if not sits(gaps(stats, target)) else []}


def throughEdits(clips: list) -> list:
    """timeline pieces grouped into shots: a piece carrying on the same file where the previous one stopped is a razor cut."""
    groups = []
    for c in sorted(clips, key=lambda c: c["start"]):
        prev = groups[-1][-1] if groups else None
        if (prev and prev.get("mediaPath") == c.get("mediaPath") and abs(prev["end"] - c["start"]) < 1e-3
                and abs(prev["outPoint"] - c["inPoint"]) < 0.02 and (prev.get("speed") or 1) == (c.get("speed") or 1)):
            groups[-1].append(c)
        else:
            groups.append([c])
    return groups


def groupLabel(group: list, track: str = "V1") -> str:
    first, last = group[0]["index"], group[-1]["index"]
    return f"{track} #{first}" if first == last else f"{track} #{first}-#{last}"


thumbWidth, labelHeight, sheetPad = 320, 22, 8


def sheet(rows: list, path: str) -> str:
    """one row per shot, its label above: the frame before and after, or the reference alone."""
    from PIL import Image, ImageDraw

    from sheets import font

    def thumb(rgb):
        img = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8))
        return img.resize((thumbWidth, img.height * thumbWidth // img.width))

    cells = [[thumb(f) for f in (before, after) if f is not None] for _, before, after in rows]
    heights = [labelHeight + max(c.height for c in row) for row in cells]
    out = Image.new("RGB", (2 * thumbWidth + 3 * sheetPad, sum(heights) + (len(rows) + 1) * sheetPad), (24, 24, 24))
    draw = ImageDraw.Draw(out)
    y = sheetPad
    for (label, _, after), row, h in zip(rows, cells, heights):
        draw.text((sheetPad, y + 3), label + ("   before | after" if after is not None else ""), fill=(230, 230, 230), font=font(14))
        for k, cell in enumerate(row):
            out.paste(cell, (sheetPad + k * (thumbWidth + sheetPad), y + labelHeight))
        y += h + sheetPad
    out.save(path, quality=88)
    return path
