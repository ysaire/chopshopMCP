"""clip library: one index per folder with probe, thumbnail, words heard and claude's tags; a file keeps its id."""

import hashlib
import json
import os
import re
import time
from pathlib import Path

from PIL import Image, ImageDraw

import media
import sheets
from config import appDir

videoExtensions = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".gif")
audioExtensions = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus")
maxFiles = 3000
maxTranscribeSeconds = 120.0


def libraryDir() -> Path:
    path = appDir() / "library"
    path.mkdir(parents=True, exist_ok=True)
    return path


def folderKey(folder: str) -> str:
    return hashlib.sha1(os.path.normcase(os.path.abspath(folder)).encode("utf-8")).hexdigest()[:12]


def indexPath(folder: str) -> Path:
    return libraryDir() / f"{folderKey(folder)}.json"


def kindOf(path: str) -> str | None:
    lower = path.lower()
    if lower.endswith(media.imageExtensions):
        return "image"
    if lower.endswith(videoExtensions):
        return "video"
    if lower.endswith(audioExtensions):
        return "audio"
    return None


def scan(folder: str) -> list:
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")))
        for name in sorted(files):
            if not name.startswith(".") and kindOf(name):
                out.append(os.path.join(root, name))
                if len(out) >= maxFiles:
                    return out
    return out


def loadIndex(folder: str) -> dict:
    try:
        return json.loads(indexPath(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"folder": os.path.abspath(folder), "files": []}


def saveIndex(index: dict):
    path = indexPath(index["folder"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    folders = knownFolders()
    if index["folder"] not in folders:
        (libraryDir() / "folders.json").write_text(json.dumps(folders + [index["folder"]], ensure_ascii=False), encoding="utf-8")


def knownFolders() -> list:
    try:
        return [f for f in json.loads((libraryDir() / "folders.json").read_text(encoding="utf-8")) if isinstance(f, str)]
    except (OSError, ValueError):
        return []


def thumbnail(path: str, kind: str, duration: float, outPath: Path) -> str | None:
    """the middle of a clip, or the picture itself, 256 px."""
    import masks
    try:
        if kind == "image":
            image = Image.open(path).convert("RGB")
        else:
            image = Image.fromarray(masks.readFrame(path, max(0.0, duration / 2))).convert("RGB")
    except Exception:
        return None
    image.thumbnail((256, 256))
    outPath.parent.mkdir(parents=True, exist_ok=True)
    image.save(outPath, quality=80)
    return str(outPath)


def indexFolder(folder: str, transcribe: bool = True, transcriber=None, onProgress=None) -> dict:
    """reuses unchanged entries; transcriber(path) -> text, for clips with sound up to maxTranscribeSeconds."""
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise ValueError(f"folder not found: {folder}")
    index = loadIndex(folder)
    known = {e["path"]: e for e in index["files"]}
    nextId = max([e["id"] for e in index["files"]], default=-1) + 1
    thumbs = libraryDir() / folderKey(folder)
    paths = scan(folder)
    files = []
    for n, path in enumerate(paths):
        if onProgress and n % 10 == 0:
            onProgress("library", n / max(1, len(paths)), f"{n}/{len(paths)} clips")
        st = os.stat(path)
        old = known.get(path)
        if old and old.get("size") == st.st_size and old.get("mtime") == int(st.st_mtime):
            if transcribe and old.get("words") is None and old.get("hasAudio") and transcriber and old["duration"] <= maxTranscribeSeconds:
                old["words"] = safeTranscribe(transcriber, path)
            files.append(old)
            continue
        kind = kindOf(path)
        try:
            info = media.probe(path)
        except Exception:
            continue
        entryId = old["id"] if old else nextId
        if not old:
            nextId += 1
        duration = 0.0 if kind == "image" else round(float(info.get("duration") or 0), 2)
        entry = {
            "id": entryId, "path": path, "name": os.path.splitext(os.path.basename(path))[0],
            "folder": os.path.relpath(os.path.dirname(path), folder), "kind": kind,
            "duration": duration, "width": info.get("width"), "height": info.get("height"),
            "hasAudio": bool(info.get("hasAudio")) and kind != "image",
            "size": st.st_size, "mtime": int(st.st_mtime),
            "thumb": None if kind == "audio" else thumbnail(path, kind, duration, thumbs / f"{entryId}.jpg"),
            "words": None,
            "description": (old or {}).get("description", ""), "tags": (old or {}).get("tags", []),
        }
        if transcribe and transcriber and entry["hasAudio"] and duration <= maxTranscribeSeconds:
            entry["words"] = safeTranscribe(transcriber, path)
        files.append(entry)
    # a tag written while this ran must survive
    latest = {e["path"]: e for e in loadIndex(folder)["files"]}
    for e in files:
        now = latest.get(e["path"])
        if now and (now.get("description") or now.get("tags")):
            e["description"], e["tags"] = now.get("description", ""), now.get("tags", [])
    index["files"] = sorted(files, key=lambda e: e["id"])
    index["updated"] = int(time.time())
    saveIndex(index)
    if onProgress:
        onProgress("library", 1.0, f"{len(files)} clips")
    return index


def safeTranscribe(transcriber, path: str) -> str:
    try:
        return " ".join(str(transcriber(path) or "").split())[:400]
    except Exception:
        return ""


def summary(index: dict) -> dict:
    kinds = {}
    for e in index["files"]:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    return {"folder": index["folder"], "clips": len(index["files"]), "kinds": kinds,
            "described": sum(1 for e in index["files"] if e.get("description") or e.get("tags")),
            "withWords": sum(1 for e in index["files"] if e.get("words"))}


def entryLine(e: dict) -> str:
    length = "" if e["kind"] == "image" else f" {e['duration']:.1f}s"
    parts = [f"#{e['id']} {e['kind']}{length} {e['name']}"]
    if e.get("words"):
        parts.append(f'says "{e["words"][:90]}"')
    if e.get("tags"):
        parts.append("tags " + ", ".join(e["tags"]))
    if e.get("description"):
        parts.append(e["description"][:120])
    return " | ".join(parts)


def tokens(text: str) -> list:
    return [t for t in re.split(r"[^\w]+", (text or "").lower()) if len(t) > 1]


def similar(a: str, b: str) -> bool:
    """same word, or one is the other plus an ending (shock / shocked, laugh / laughing)."""
    return a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)))


def entryFields(entry: dict) -> list:
    return [(set(tokens(entry.get("description") or "")), 3.0), (set(tokens(" ".join(entry.get("tags") or []))), 3.0),
            (set(tokens(entry["name"] + " " + entry.get("folder", ""))), 2.0), (set(tokens(entry.get("words") or "")), 1.5)]


def search(query: str, folder: str | None = None, kind: str | None = None, limit: int = 20) -> list:
    """rare words weigh more: "shocked reaction" ranks shocked faces above any reaction."""
    import math
    words = tokens(query)
    pool = []
    for f in ([os.path.abspath(folder)] if folder else knownFolders()):
        for e in loadIndex(f)["files"]:
            if not kind or e["kind"] == kind:
                pool.append((f, e, entryFields(e)))
    rarity = {}
    for w in words:
        df = sum(1 for _f, _e, fields in pool if any(similar(w, h) for have, _w in fields for h in have))
        rarity[w] = math.log(1 + len(pool) / (1 + df))
    out = []
    for f, e, fields in pool:
        s = sum(weight * rarity[w] for w in words for have, weight in fields if any(similar(w, h) for h in have))
        if s > 0 or not words:
            out.append((round(s, 2), f, e))
    out.sort(key=lambda x: (-x[0], x[2]["id"]))
    return [dict({k: e[k] for k in ("id", "path", "kind", "duration", "words", "tags", "description")}, folder=f, score=s)
            for s, f, e in out[:limit]]


def tag(folder: str, entries: list) -> dict:
    """what claude saw: [{id, description?, tags?}]."""
    index = loadIndex(folder)
    byId = {e["id"]: e for e in index["files"]}
    done = 0
    for item in entries:
        e = byId.get(int(item.get("id", -1)))
        if not e:
            continue
        if item.get("description") is not None:
            e["description"] = str(item["description"])[:300]
        if item.get("tags") is not None:
            e["tags"] = [str(t).strip().lower()[:30] for t in item["tags"] if str(t).strip()][:12]
        done += 1
    saveIndex(index)
    return {"tagged": done, "described": sum(1 for e in index["files"] if e.get("description") or e.get("tags"))}


def sheet(folder: str, outPath: Path, fromId: int = 0, count: int = 20, ids: list | None = None, cols: int = 5) -> dict:
    """one cell per clip: its thumbnail (or a sound cell), id, kind and length on top."""
    index = loadIndex(folder)
    if ids:
        wanted = set(int(i) for i in ids)
        entries = [e for e in index["files"] if e["id"] in wanted][:count]
    else:
        entries = [e for e in index["files"] if e["id"] >= fromId][:count]
    if not entries:
        raise ValueError("no clip there, indexLibrary first or check the ids")
    cellW, cellH = 256, 144
    rows = (len(entries) + cols - 1) // cols
    image = Image.new("RGB", (min(cols, len(entries)) * cellW, rows * (cellH + sheets.labelHeight)), (12, 12, 12))
    draw = ImageDraw.Draw(image)
    labelFont, bigFont = sheets.font(14), sheets.font(18)
    for i, e in enumerate(entries):
        x, y = (i % cols) * cellW, (i // cols) * (cellH + sheets.labelHeight)
        if e.get("thumb") and os.path.isfile(e["thumb"]):
            thumb = Image.open(e["thumb"]).convert("RGB")
            thumb.thumbnail((cellW, cellH))
            image.paste(thumb, (x + (cellW - thumb.width) // 2, y + sheets.labelHeight + (cellH - thumb.height) // 2))
        else:
            draw.rectangle((x + 2, y + sheets.labelHeight + 2, x + cellW - 3, y + sheets.labelHeight + cellH - 3), outline=(80, 80, 80))
            draw.text((x + 12, y + sheets.labelHeight + 50), f"sound: {e['name'][:22]}", fill=(160, 200, 255), font=bigFont)
        length = "" if e["kind"] == "image" else f" {e['duration']:.1f}s"
        draw.text((x + 4, y + 3), f"#{e['id']} {e['kind']}{length}", fill=(255, 220, 90), font=labelFont)
    outPath = Path(outPath)
    outPath.parent.mkdir(parents=True, exist_ok=True)
    image.save(outPath, quality=80)
    return {"path": str(outPath), "rows": [entryLine(e) for e in entries],
            "next": entries[-1]["id"] + 1 if entries[-1]["id"] < max(e["id"] for e in index["files"]) else None}
