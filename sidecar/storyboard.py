"""storyboard: one framed still per segment of a plan, so an edit can be judged before anything renders."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import edl
import masks
import xmeml


def mmss(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def coverCell(cover: dict, entry: dict, basePath: str, seqW: int, seqH: int, cellW: int, cellH: int,
              outPath: Path, at: float) -> dict:
    """a cover as the viewer sees it: inside its box over the shot below, or the whole frame."""
    t = 0.0 if cover["still"] else (cover["start"] + cover["end"]) / 2
    rgb = masks.readFrame(entry["path"], t)
    picture = Image.fromarray(rgb).convert("RGB")
    if cover["box"]:
        base = Image.open(basePath).convert("RGB").resize((cellW * 2, cellH * 2))
        x0, y0, x1, y1 = cover["box"]
        boxW, boxH = int((x1 - x0) * base.width), int((y1 - y0) * base.height)
        picture.thumbnail((max(1, boxW), max(1, boxH)))
        cx, cy = int((x0 + x1) / 2 * base.width), int((y0 + y1) / 2 * base.height)
        base.paste(picture, (cx - picture.width // 2, cy - picture.height // 2))
        picture = base
    else:
        w, h = picture.size
        view = edl.viewAt(cover["zoom"], 0.5)
        if cover["fit"] == "fit":
            canvas = Image.new("RGB", (seqW, seqH))
            picture.thumbnail((seqW, seqH))
            canvas.paste(picture, ((seqW - picture.width) // 2, (seqH - picture.height) // 2))
            picture = canvas
        else:
            picture = picture.crop(masks.visibleCrop(w, h, seqW, seqH, view))
    picture.save(outPath, quality=85)
    label = Path(entry["path"]).stem[:14]
    return {"segment": None, "path": str(outPath), "output": [at, at + cover["length"]], "source": [cover["start"], cover["end"]],
            "speed": cover["speed"], "note": cover["note"], "extras": [f"cover {label}", cover["audio"]]}


def storyboard(mediaPath: str, plan: dict, info: dict, outDir: Path, inherited: dict | None = None,
               segmenter=None, maxCells: int = 40) -> dict:
    norm = edl.normalizePlan(plan, info, mediaPath)
    seqW, seqH = edl.resolveFormat(norm["format"], info, inherited if norm["format"] in (None, "source") else None)
    _tb, _ntsc, exactFps = xmeml.rateFromFps(info.get("fps") or 30.0)
    clips = xmeml.toClips(norm["segments"], exactFps)
    outDir = Path(outDir)
    outDir.mkdir(parents=True, exist_ok=True)
    portrait = seqH > seqW
    cellW = 240 if portrait else 360
    cellH = int(cellW * seqH / seqW)
    labelH = 34
    cells = []
    for clip in clips[:maxCells]:
        seg = norm["segments"][clip["segment"]]
        entry = norm["media"][seg.get("media", "main")]
        path = entry["path"] or mediaPath
        title = seg["title"]
        if title:
            t = seg["start"] + (title["from"] + title["to"]) / 2 * seg["speed"]
        else:
            t = (seg["start"] + seg["end"]) / 2
        if seg.get("still"):
            t = 0.0
        fraction = (t - seg["start"]) / max(1e-6, seg["end"] - seg["start"])
        view = edl.viewAt(seg["zoom"], fraction)
        model = segmenter if title and title["behind"] else None
        zones = norm["blur"] if seg.get("media", "main") == "main" else []
        frame = masks.previewFrame(path, t, zones, title, str(outDir / f"board_{clip['segment']:03d}_{t:09.3f}.jpg"),
                                   seqSize=(seqW, seqH), view=view, segmenter=model, maxSide=max(cellW, cellH) * 2)
        extras = [k for k in ("zoom", "title", "attach", "stabilize", "jcut", "lcut") if seg.get(k)]
        if seg.get("media", "main") != "main":
            extras.insert(0, Path(path).stem[:16])
        cells.append({"segment": clip["segment"], "path": frame, "output": [clip["start"] / exactFps, clip["end"] / exactFps],
                      "source": [seg["start"], seg["end"]], "speed": seg["speed"], "note": seg["note"], "extras": extras})
        for k, cover in enumerate(seg.get("cover") or []):
            if not cover["video"] or len(cells) >= maxCells:
                continue
            cells.append(coverCell(cover, norm["media"][cover["media"]], frame, seqW, seqH, cellW, cellH,
                                   outDir / f"board_{clip['segment']:03d}_cover{k}.jpg",
                                   clip["start"] / exactFps + cover["from"]))
    cols = max(1, min(6 if portrait else 4, len(cells)))
    rows = max(1, (len(cells) + cols - 1) // cols)
    sheet = Image.new("RGB", (cols * cellW, rows * (cellH + labelH)), (18, 18, 18))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(size=13)
    except TypeError:
        font = ImageFont.load_default()
    for i, cell in enumerate(cells):
        x, y = (i % cols) * cellW, (i // cols) * (cellH + labelH)
        im = Image.open(cell["path"]).convert("RGB").resize((cellW, cellH))
        sheet.paste(im, (x, y))
        speed = f" x{cell['speed']:g}" if cell["speed"] != 1 else ""
        extras = (" " + "+".join(cell["extras"])) if cell["extras"] else ""
        color = (120, 200, 255) if cell["segment"] is None else (255, 220, 0)
        draw.text((x + 4, y + cellH + 2), f"{i + 1}. {mmss(cell['output'][0])}{speed}{extras}", fill=color, font=font)
        draw.text((x + 4, y + cellH + 17), (cell["note"] or "")[:40], fill=(200, 200, 200), font=font)
    path = outDir / f"storyboard_{abs(hash(str(plan))) % 10 ** 10}.jpg"
    sheet.save(path, quality=85)
    total = clips[-1]["end"] / exactFps if clips else 0.0
    return {"path": str(path), "duration": round(total, 2), "segments": len(clips), "shown": len(cells),
            "format": [seqW, seqH], "warnings": norm["warnings"]}
