"""shot log: what claude saw, written once per rush, reused by every edit."""

import json
from pathlib import Path

from sheets import fmtTime


def logPath(cacheDir: Path) -> Path:
    return Path(cacheDir) / "shotLog.json"


def load(cacheDir: Path) -> list:
    try:
        with open(logPath(cacheDir), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return []


def save(cacheDir: Path, entries: list) -> list:
    """merge by time range, newest description wins for the same range."""
    byKey = {f"{e['start']:.2f}-{e['end']:.2f}": e for e in load(cacheDir)}
    for raw in entries:
        start, end = float(raw["start"]), float(raw["end"])
        if end <= start:
            continue
        entry = {
            "start": round(start, 2),
            "end": round(end, 2),
            "description": str(raw.get("description", "")).strip(),
            "interest": max(0, min(10, int(raw.get("interest", 5)))),
            "tags": [str(t).strip().lower() for t in raw.get("tags", []) if str(t).strip()],
        }
        byKey[f"{entry['start']:.2f}-{entry['end']:.2f}"] = entry
    merged = sorted(byKey.values(), key=lambda e: (e["start"], e["end"]))
    with open(logPath(cacheDir), "w", encoding="utf-8") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=1)
    return merged


def formatLog(entries: list) -> str:
    lines = []
    for e in entries:
        tags = ", ".join(e["tags"])
        lines.append(f"[{fmtTime(e['start'])}-{fmtTime(e['end'])}] {e['interest']}/10 {tags} | {e['description']}")
    return "\n".join(lines)
