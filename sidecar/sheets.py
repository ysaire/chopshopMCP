"""contact sheets: a grid of timestamped thumbnails, how claude watches a rush cheaply."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import perceive

labelHeight = 22


def fmtTime(t: float) -> str:
    minutes = int(t // 60)
    return f"{minutes:02d}:{t - minutes * 60:04.1f}"


def pickTimes(start: float, end: float, every: float | None, maxCells: int, thumbEvery: float) -> list:
    span = max(0.0, end - start)
    if every is None or every <= 0:
        every = max(thumbEvery, span / maxCells) if span else thumbEvery
    times = []
    t = start
    while t < end - 1e-6 and len(times) < maxCells:
        times.append(round(t, 3))
        t += every
    return times or [round(start, 3)]


def font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pillow < 10.1
        return ImageFont.load_default()


def contactSheet(perception: dict, cacheDir: Path, start: float, end: float, every: float | None = None,
                 cols: int = 5, maxCells: int = 20) -> dict:
    thumbDir = Path(cacheDir) / "thumbs"
    times = pickTimes(start, end, every, maxCells, perception.get("thumbEvery", 1.0))
    picks = []
    for t in times:
        thumb = perception and perceive.nearestThumb(perception, t)
        if thumb and (not picks or picks[-1][1] != thumb[1]):
            picks.append(thumb)
    if not picks:
        raise ValueError("no thumbnails for this range, run analyzeRush first")

    cellW, cellH = Image.open(thumbDir / picks[0][1]).size
    rows = (len(picks) + cols - 1) // cols
    usedCols = min(cols, len(picks))
    sheet = Image.new("RGB", (usedCols * cellW, rows * (cellH + labelHeight)), (12, 12, 12))
    draw = ImageDraw.Draw(sheet)
    labelFont = font(15)
    listed = []
    for i, (t, name) in enumerate(picks):
        x, y = (i % cols) * cellW, (i // cols) * (cellH + labelHeight)
        thumb = Image.open(thumbDir / name).convert("RGB")
        if thumb.size != (cellW, cellH):
            thumb = thumb.resize((cellW, cellH))
        sheet.paste(thumb, (x, y + labelHeight))
        beat = perceive.beatAt(perception, t)
        label = f"{fmtTime(t)}  b{beat['id']}" if beat else fmtTime(t)
        draw.text((x + 6, y + 3), label, fill=(255, 220, 90), font=labelFont)
        listed.append({"time": t, "beat": beat["id"] if beat else None})

    outDir = Path(cacheDir) / "sheets"
    outDir.mkdir(parents=True, exist_ok=True)
    outPath = outDir / f"sheet_{start:08.2f}_{end:08.2f}.jpg"
    sheet.save(outPath, quality=80)
    return {"path": str(outPath), "cells": listed, "every": round(listed[1]["time"] - listed[0]["time"], 2) if len(listed) > 1 else None,
            "asked": times}
