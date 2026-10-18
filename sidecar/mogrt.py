"""motion graphics templates: find the user's .mogrt files and read which controls they expose."""

import glob
import json
import os
import zipfile
from pathlib import Path


def templateFolders(extra: list | None = None) -> list:
    """premiere's local templates folder plus folders from config."""
    appData = os.environ.get("APPDATA") or str(Path.home() / "Library" / "Application Support")
    folders = [os.path.join(appData, "Adobe", "Common", "Motion Graphics Templates")]
    for entry in extra or []:
        folders.append(os.path.expandvars(os.path.expanduser(str(entry))))
    return [f for f in dict.fromkeys(folders) if os.path.isdir(f)]


def labelOf(control: dict) -> str:
    label = control.get("label")
    if isinstance(label, dict):
        for entry in label.get("strDB") or []:
            if entry.get("str"):
                return entry["str"]
    return str(label or control.get("id") or "")


def readControls(path: str) -> list:
    """[{label, type}] from definition.json inside the template, best effort."""
    try:
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.lower().endswith("definition.json")), None)
            if not name:
                return []
            data = json.loads(z.read(name).decode("utf-8", "replace"))
    except (zipfile.BadZipFile, OSError, ValueError):
        return []
    controls = data.get("clientControls") or []
    return [{"label": labelOf(c), "type": c.get("type")} for c in controls if isinstance(c, dict)]


def listTemplates(extra: list | None = None) -> list:
    out = []
    for folder in templateFolders(extra):
        for path in sorted(glob.glob(os.path.join(folder, "**", "*.mogrt"), recursive=True)):
            out.append({"name": Path(path).stem, "path": path, "controls": readControls(path)})
    return out


def find(templates: list, query: str) -> dict:
    if os.path.isfile(query):
        return {"name": Path(query).stem, "path": query, "controls": readControls(query)}
    q = query.lower()
    hits = [t for t in templates if t["name"].lower() == q] or [t for t in templates if q in t["name"].lower()]
    if len(hits) != 1:
        raise ValueError(f"{len(hits)} templates match {query!r}" + (": " + ", ".join(h["name"] for h in hits[:8]) if hits else ""))
    return hits[0]
