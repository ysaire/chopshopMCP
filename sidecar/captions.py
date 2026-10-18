"""captions as one text still per chunk, so premiere animation presets scale around the text, not the frame."""

import glob
import hashlib
import json
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

fontDirs = [
    os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
    "/Library/Fonts", "/System/Library/Fonts", os.path.expanduser("~/Library/Fonts"),
    "/usr/share/fonts", os.path.expanduser("~/.fonts"),
]
fallbackFonts = ["Montserrat-Black", "Montserrat-ExtraBold", "Poppins-Bold", "Arial-Black", "Arial-BoldMT", "Impact",
                 "DejaVuSans-Bold"]

baseStyle = {"mode": "pop", "words": 2, "maxChars": 16, "font": "Montserrat-Black", "size": 0.075, "color": "#FFFFFF",
             "stroke": 0.12, "strokeColor": "#000000", "shadow": 0.0, "uppercase": True, "y": 0.72,
             "highlight": None, "preset": None, "lines": 1}
builtinStyles = {
    "pop": {},
    "karaoke": {"mode": "karaoke", "words": 5, "maxChars": 28, "size": 0.052, "highlight": "#FFD400", "y": 0.78,
                "uppercase": False, "lines": 2},
    "line": {"mode": "line", "words": 9, "maxChars": 40, "size": 0.042, "stroke": 0.0, "shadow": 0.1, "y": 0.88,
             "uppercase": False, "font": "Montserrat-SemiBold", "lines": 2},
}
punctuationEnd = re.compile(r"[.!?…,;:]$")
sentenceEnd = re.compile(r"[.!?…]$")


def normFontName(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", name or "").lower()


def fontIndex() -> dict:
    """"familystyle" (no spaces, lowercase) -> file, from the system and user font folders."""
    index = {}
    for folder in fontDirs:
        if not folder or not os.path.isdir(folder):
            continue
        for path in glob.glob(os.path.join(folder, "**", "*.[ot]t[fc]"), recursive=True):
            try:
                family, style = ImageFont.truetype(path, 12).getname()
            except Exception:
                continue
            for key in (family + style, family + "-" + style, family if style.lower() in ("regular", "normal") else None,
                        Path(path).stem):
                if key:
                    index.setdefault(normFontName(key), path)
    return index


fontCache = {}


def findFontFile(name: str | None) -> str | None:
    """a font file path, or a postscript or family + style name ("Montserrat-Black", "Montserrat Black")."""
    if name and os.path.isfile(name):
        return name
    if "index" not in fontCache:
        fontCache["index"] = fontIndex()
    index = fontCache["index"]
    for candidate in ([name] if name else []) + fallbackFonts:
        path = index.get(normFontName(candidate))
        if path:
            return path
    return next(iter(index.values()), None)


def normalizeStyle(style, configured: dict | None = None) -> dict:
    """a built-in or configured style name, or a dict of overrides on top of one ({"base": "karaoke", ...})."""
    configured = configured or {}
    overrides = {}
    if isinstance(style, dict):
        overrides = dict(style)
        style = overrides.pop("base", None) or overrides.get("mode") or "pop"
    name = str(style or "pop")
    chain = configured.get(name) or {}
    base = builtinStyles.get(chain.get("base") or name) or builtinStyles.get(chain.get("mode") or "") or {}
    out = dict(baseStyle, **base)
    out.update({k: v for k, v in chain.items() if k != "base"})
    out.update(overrides)
    out["name"] = name
    if out["mode"] not in ("pop", "karaoke", "line"):
        out["mode"] = "pop"
    out["words"] = max(1, min(14, int(out["words"])))
    return out


def wordsOnTimeline(words: list, segments: list, clips: list, exactFps: float) -> list:
    """source-timed words -> output seconds, for the segments played at normal speed."""
    out = []
    for clip in clips:
        seg = segments[clip["segment"]]
        if abs(seg["speed"] - 1.0) > 1e-6:
            continue
        offset = clip["start"] / exactFps - seg["start"]
        for w in words:
            if w["end"] <= seg["start"] + 0.02 or w["start"] >= seg["end"] - 0.02:
                continue
            start = max(w["start"], seg["start"]) + offset
            end = min(w["end"], seg["end"]) + offset
            if end - start > 0.01:
                out.append({"text": w["text"].strip(), "start": round(start, 3), "end": round(end, 3)})
    out.sort(key=lambda w: w["start"])
    return [w for w in out if w["text"]]


def wordsOnPieces(wordsByMedia: dict, pieces: list) -> list:
    """source-timed words -> output seconds through the sound actually heard; pieces: [{media, in, out, start}] in seconds."""
    out = []
    for p in pieces:
        offset = p["start"] - p["in"]
        for w in wordsByMedia.get(p["media"]) or []:
            if w["end"] <= p["in"] + 0.02 or w["start"] >= p["out"] - 0.02:
                continue
            start = max(w["start"], p["in"]) + offset
            end = min(w["end"], p["out"]) + offset
            if end - start > 0.01:
                out.append({"text": w["text"].strip(), "start": round(start, 3), "end": round(end, 3)})
    out.sort(key=lambda w: w["start"])
    return [w for w in out if w["text"]]


def chunkWords(words: list, style: dict, minDuration: float = 0.08, hold: float = 0.25) -> list:
    """captions [{start, end, text, words: [...], highlight: index | None, first: bool}] in output seconds."""
    groups, current = [], []
    for w in words:
        if current:
            gap = w["start"] - current[-1]["end"]
            text = " ".join(x["text"] for x in current + [w])
            stop = sentenceEnd if style["mode"] == "line" else punctuationEnd
            if (len(current) >= style["words"] or gap > 0.35 or len(text) > style["maxChars"]
                    or stop.search(current[-1]["text"])):
                groups.append(current)
                current = []
        current.append(w)
    if current:
        groups.append(current)

    cues = []
    for gi, group in enumerate(groups):
        nextStart = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else None
        end = group[-1]["end"] + hold
        if nextStart is not None:
            end = nextStart if nextStart - group[-1]["end"] < hold + 0.15 else min(end, nextStart)
        texts = [w["text"] for w in group]
        if style["mode"] == "karaoke":
            for wi, w in enumerate(group):
                subEnd = group[wi + 1]["start"] if wi + 1 < len(group) else end
                cues.append({"start": w["start"] if wi else group[0]["start"], "end": max(subEnd, w["start"] + minDuration),
                             "text": " ".join(texts), "words": texts, "highlight": wi, "first": wi == 0})
        else:
            cues.append({"start": group[0]["start"], "end": max(end, group[0]["start"] + minDuration),
                         "text": " ".join(texts), "words": texts, "highlight": None, "first": True})
    for a, b in zip(cues, cues[1:]):
        a["end"] = min(a["end"], b["start"])
    return [dict(c, start=round(c["start"], 3), end=round(c["end"], 3)) for c in cues if c["end"] - c["start"] >= minDuration / 2]


def cuesFromText(cues: list, style: dict) -> list:
    """explicit cues from claude [{start, end, text}]: words spread evenly for karaoke."""
    out = []
    for c in cues:
        texts = str(c["text"]).split()
        if not texts:
            continue
        start, end = float(c["start"]), float(c["end"])
        if style["mode"] == "karaoke" and len(texts) > 1:
            step = (end - start) / len(texts)
            for i in range(len(texts)):
                out.append({"start": round(start + i * step, 3), "end": round(start + (i + 1) * step, 3), "text": " ".join(texts),
                            "words": texts, "highlight": i, "first": i == 0})
        else:
            out.append({"start": start, "end": end, "text": " ".join(texts), "words": texts, "highlight": None, "first": True})
    return out


def hexColor(value, default=(255, 255, 255)) -> tuple:
    try:
        v = str(value).lstrip("#")
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return default


def wrapWords(words: list, font, maxWidth: float, draw, lines: int) -> list:
    rows = [[]]
    for w in words:
        trial = " ".join(rows[-1] + [w])
        if rows[-1] and draw.textlength(trial, font=font) > maxWidth and len(rows) < lines:
            rows.append([w])
        else:
            rows[-1].append(w)
    return rows


def renderCaption(cue: dict, style: dict, seqW: int, seqH: int, outDir: Path) -> dict:
    """a tight transparent png of the caption: {"path", "width", "height", "posX", "posY"}."""
    words = [w.upper() if style["uppercase"] else w for w in cue["words"]]
    key = hashlib.sha1(json.dumps([words, cue.get("highlight"), style, seqW, seqH], sort_keys=True, default=str)
                       .encode()).hexdigest()[:14]
    outDir = Path(outDir)
    outDir.mkdir(parents=True, exist_ok=True)
    path = outDir / f"caption_{key}.png"
    meta = outDir / f"caption_{key}.json"
    if path.exists() and meta.exists():
        return json.loads(meta.read_text(encoding="utf-8"))

    size = max(10, int(round(style["size"] * seqH)))
    fontPath = findFontFile(style.get("font"))
    font = ImageFont.truetype(fontPath, size) if fontPath else ImageFont.load_default(size=size)
    stroke = int(round(style["stroke"] * size))
    scratch = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    rows = wrapWords(words, font, 0.86 * seqW, scratch, int(style.get("lines") or 1))
    while size > 10 and max(scratch.textlength(" ".join(r), font=font) for r in rows) + 2 * stroke > 0.9 * seqW:
        size = int(size * 0.92)
        font = ImageFont.truetype(fontPath, size) if fontPath else ImageFont.load_default(size=size)
        stroke = int(round(style["stroke"] * size))
    ascent, descent = font.getmetrics()
    lineH = int((ascent + descent) * 1.08)
    widths = [scratch.textlength(" ".join(r), font=font) for r in rows]
    shadow = int(round(style["shadow"] * size))
    pad = stroke + shadow * 2 + int(0.1 * size)
    W = int(max(widths) + 2 * pad)
    H = int(lineH * len(rows) + 2 * pad)

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    fill = hexColor(style["color"]) + (255,)
    accent = hexColor(style["highlight"], (255, 212, 0)) + (255,) if style.get("highlight") else fill
    strokeFill = hexColor(style["strokeColor"], (0, 0, 0)) + (255,)
    spaceW = scratch.textlength(" ", font=font)
    index = 0
    placed = []
    for r, row in enumerate(rows):
        x = pad + (max(widths) - widths[r]) / 2
        y = pad + r * lineH
        for w in row:
            color = accent if cue.get("highlight") == index else fill
            placed.append((x, y, w, color))
            x += scratch.textlength(w, font=font) + spaceW
            index += 1
    if shadow:
        shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shade)
        for x, y, w, _ in placed:
            sd.text((x + shadow * 0.6, y + shadow * 0.8), w, font=font, fill=(0, 0, 0, 170), stroke_width=stroke,
                    stroke_fill=(0, 0, 0, 170))
        layer = Image.alpha_composite(shade.filter(ImageFilter.GaussianBlur(shadow * 0.6)), layer)
        draw = ImageDraw.Draw(layer)
    for x, y, w, color in placed:
        draw.text((x, y), w, font=font, fill=color, stroke_width=stroke, stroke_fill=strokeFill)
    layer.save(path)
    info = {"path": str(path), "width": W, "height": H, "posX": 0.5, "posY": float(style["y"]),
            "font": os.path.basename(fontPath) if fontPath else None}
    meta.write_text(json.dumps(info), encoding="utf-8")
    return info

