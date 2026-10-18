"""style memory: style.md (the user's taste, edited by claude and by hand) and breakdowns/ (one card per reference edit)."""

import datetime
import json
from pathlib import Path

from config import appDir
from reference import slug

sections = {
    "taste": "Taste",
    "pacing": "Pacing",
    "captions": "Captions",
    "motion": "Motion and effects",
    "sound": "Sound",
    "formats": "Formats",
    "never": "Never do",
}

template = """# Editing style

What the user likes, in rules with numbers when possible. Read before planning an edit, add to it when the user
corrects something or praises something. Newest notes win over older ones.

""" + "\n".join(f"## {title}\n" for title in sections.values())


def styleDir() -> Path:
    path = appDir() / "style"
    (path / "breakdowns").mkdir(parents=True, exist_ok=True)
    return path


def stylePath() -> Path:
    path = styleDir() / "style.md"
    if not path.exists():
        path.write_text(template, encoding="utf-8")
    return path


def sectionTitle(section: str) -> str:
    key = (section or "taste").strip().lower()
    if key in sections:
        return sections[key]
    for title in sections.values():
        if title.lower() == key:
            return title
    raise ValueError(f"unknown section, use one of: {', '.join(sections)}")


def remember(note: str, section: str = "taste", source: str | None = None) -> str:
    """append a dated bullet under a section of style.md, returns the section as it now reads."""
    note = " ".join((note or "").split())
    if not note:
        raise ValueError("empty note")
    title = sectionTitle(section)
    path = stylePath()
    lines = path.read_text(encoding="utf-8").splitlines()
    header = f"## {title}"
    if header not in lines:
        lines += ["", header]
    start = lines.index(header)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    stamp = datetime.date.today().isoformat()
    bullet = f"- {note} ({stamp}{', ' + source if source else ''})"
    lines.insert(end, bullet)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return readSection(title)


def readSection(title: str) -> str:
    lines = stylePath().read_text(encoding="utf-8").splitlines()
    header = f"## {title}"
    if header not in lines:
        return ""
    start = lines.index(header)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end]).strip()


def saveBreakdown(name: str, card: str, metrics: dict | None = None, url: str | None = None,
                  formatName: str | None = None) -> dict:
    """breakdowns/<slug>.md holds the card, .json the numbers it was written from."""
    key = slug(name)
    folder = styleDir() / "breakdowns"
    text = card.strip() + "\n"
    if url and url not in text:
        text += f"\nsource: {url}\n"
    (folder / f"{key}.md").write_text(text, encoding="utf-8")
    meta = {"name": name, "url": url, "format": formatName, "saved": datetime.date.today().isoformat()}
    if metrics:
        meta["metrics"] = metrics
    (folder / f"{key}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"saved": key, "path": str(folder / f"{key}.md")}


def listBreakdowns(formatName: str | None = None) -> list:
    out = []
    for card in sorted((styleDir() / "breakdowns").glob("*.md")):
        meta = {}
        try:
            meta = json.loads(card.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if formatName and (meta.get("format") or "").lower() != formatName.lower():
            continue
        steal = ""
        for line in card.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("- what to steal"):
                steal = line.split(":", 1)[-1].strip()[:200]
        out.append({"name": card.stem, "format": meta.get("format"), "url": meta.get("url"), "steal": steal})
    return out


def readBreakdown(name: str) -> str:
    folder = styleDir() / "breakdowns"
    path = folder / f"{slug(name)}.md"
    if not path.exists():
        matches = [p for p in folder.glob("*.md") if slug(name).lower() in p.stem.lower()]
        if len(matches) != 1:
            raise ValueError(f"no single breakdown matches {name!r}, see getStyle for the list")
        path = matches[0]
    return path.read_text(encoding="utf-8")


def getStyle(formatName: str | None = None) -> dict:
    return {"style": stylePath().read_text(encoding="utf-8"), "breakdowns": listBreakdowns(formatName),
            "path": str(stylePath())}
