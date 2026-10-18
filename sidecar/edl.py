"""edit plans: what claude decides, validated and turned into timeline instructions.

a plan is the editor's intent in source seconds:
{
  "name": "unboxing flash 45s",
  "format": "source" | "vertical" | "square" | {"width": 1080, "height": 1350},
  "segments": [
    {"start": 82.0, "end": 84.5},                                    # plain cut
    {"start": 120.0, "end": 168.0, "speed": 6},                      # timelapse, muted
    {"start": 301.2, "end": 303.0, "zoom": {"scale": 1.4, "x": 0.62, "y": 0.45}},           # punch-in
    {"start": 410.0, "end": 414.0, "zoom": {"from": {"scale": 1.0}, "to": {"scale": 1.25, "x": 0.5, "y": 0.4}}}  # slow push
  ]
}
zoom x / y is the point of interest in the source frame (0..1), scale is relative to "fill the frame".
"""

import masks

speedMin, speedMax = 0.25, 20.0
scaleMin, scaleMax = 1.0, 4.0

formats = {
    "vertical": (1080, 1920),
    "square": (1080, 1080),
    "portrait": (1080, 1350),
}


def clamp(value, low, high):
    return max(low, min(high, value))


def normalizeView(view: dict | None) -> dict:
    view = view or {}
    return {
        "scale": round(clamp(float(view.get("scale", 1.0)), scaleMin, scaleMax), 4),
        "x": round(clamp(float(view.get("x", 0.5)), 0.0, 1.0), 4),
        "y": round(clamp(float(view.get("y", 0.5)), 0.0, 1.0), 4),
    }


def normalizeZoom(zoom) -> dict | None:
    if not zoom:
        return None
    if "from" in zoom or "to" in zoom:
        first = normalizeView(zoom.get("from"))
        last = normalizeView(zoom.get("to") or zoom.get("from"))
        return {"from": first, "to": last}
    return {"from": normalizeView(zoom), "to": None}


def resolveFormat(fmt, info: dict, inherited: dict | None = None) -> tuple:
    if isinstance(fmt, dict) and fmt.get("width") and fmt.get("height"):
        return int(fmt["width"]), int(fmt["height"])
    if isinstance(fmt, str) and fmt in formats:
        return formats[fmt]
    if inherited and inherited.get("width") and inherited.get("height"):
        return int(inherited["width"]), int(inherited["height"])
    return int(info.get("width") or 1920), int(info.get("height") or 1080)


class MediaTable:
    """the files a plan plays: "main" is the rush, the others come from plan.sources aliases or plain paths."""

    reserved = ("main", "rush")

    def __init__(self, mainPath: str | None, info: dict, aliases: dict | None, probe=None):
        self.entries = {"main": {"path": mainPath, "info": info, "still": False}}
        self.warnings = []
        self.aliases = {}
        for k, v in (aliases or {}).items():
            k = str(k).strip()
            if not k or k in self.reserved or k.startswith("_"):
                self.warnings.append(f"sources: {k!r} is a reserved name, use another alias")
                continue
            self.aliases[k] = str(v)
        self.mainPath = mainPath
        self.probe = probe

    def key(self, ref) -> str:
        """raises ValueError when the file is missing."""
        import os
        ref = "" if ref is None else str(ref).strip()
        if ref in ("", "main", "rush") or (self.mainPath and samePath(ref, self.mainPath)):
            return "main"
        if ref in self.entries:
            return ref
        path = self.aliases.get(ref, ref)
        for key, entry in self.entries.items():
            if entry["path"] and samePath(entry["path"], path):
                return key
        if not os.path.isfile(path):
            raise ValueError(f"file not found: {path}")
        if self.probe is None:
            import media
            self.probe = media.probe
        info = self.probe(path)
        if ref in self.aliases:
            key = ref
        else:
            # generated keys never collide with an alias or another file
            n = len(self.entries)
            while f"_f{n}" in self.entries:
                n += 1
            key = f"_f{n}"
        self.entries[key] = {"path": path, "info": info, "still": bool(info.get("still"))}
        return key

    def __getitem__(self, key):
        return self.entries[key]


def samePath(a: str, b: str) -> bool:
    import os
    norm = lambda p: os.path.normcase(os.path.abspath(str(p).replace("\\", "/")))
    return norm(a) == norm(b)


audioModes = ("mute", "mix", "duck", "replace")


def normalizeCover(raw: dict, segLength: float, table: MediaTable) -> dict:
    """a cover over a segment, from = seconds into the segment (output time). raises ValueError / KeyError."""
    if not isinstance(raw, dict) or not raw.get("media"):
        raise ValueError("needs media")
    key = table.key(raw["media"])
    entry = table[key]
    mInfo, still = entry["info"], entry["still"]
    hasVideo = bool(mInfo.get("hasVideo"))
    hasAudio = bool(mInfo.get("hasAudio")) and not still
    duration = float(mInfo.get("duration") or 0)
    at = clamp(float(raw.get("from", raw.get("at", 0.0)) or 0.0), 0.0, max(0.0, segLength - 0.04))
    speed = 1.0 if still else round(clamp(float(raw.get("speed", 1.0) or 1.0), speedMin, speedMax), 3)
    if still:
        start, end = 0.0, max(0.1, float(raw.get("duration", segLength - at) or 1.0))
    else:
        start = clamp(float(raw.get("start", 0.0) or 0.0), 0.0, duration)
        if raw.get("end") is not None:
            end = clamp(float(raw["end"]), start, duration)
        elif raw.get("duration") is not None:
            end = clamp(start + float(raw["duration"]) * speed, start, duration)
        else:
            end = clamp(start + (segLength - at) * speed, start, duration)
        if end - start < 0.04:
            raise ValueError("shorter than a frame")
    audio = str(raw.get("audio") or ("mute" if hasVideo and not still else "mix"))
    audio = audio if audio in audioModes else "mute"
    warnings = []
    if audio != "mute" and not hasAudio:
        audio = "mute"
    if audio != "mute" and speed != 1.0:
        warnings.append(f"sound muted at x{speed}")
        audio = "mute"
    box = masks.normalizeBox(raw["box"]) if raw.get("box") and hasVideo else None
    return {
        "media": key,
        "still": still,
        "video": hasVideo,
        "start": round(start, 3),
        "end": round(end, 3),
        "from": round(at, 3),
        "length": round((end - start) / speed, 3),
        "speed": speed,
        "audio": audio,
        "volume": clamp(float(raw.get("volume", 0.0) or 0.0), -60.0, 12.0),
        "duck": clamp(float(raw.get("duck", -12.0)), -60.0, 0.0),
        "box": box,
        "fit": "fit" if raw.get("fit") == "fit" else "fill",
        "zoom": None if box else normalizeZoom(raw.get("zoom")),
        "note": str(raw.get("note", ""))[:200],
        "warnings": warnings,
    }


def normalizePlan(plan: dict, info: dict, mainPath: str | None = None, probe=None) -> dict:
    table = MediaTable(mainPath, info, plan.get("sources"), probe)
    fps = float(info.get("fps") or 30.0)
    minLength = 2.0 / fps
    segments, warnings = [], list(table.warnings)
    for i, raw in enumerate(plan.get("segments") or []):
        try:
            key = table.key(raw.get("media"))
        except (TypeError, ValueError) as err:
            warnings.append(f"segment {i}: {err}, skipped")
            continue
        entry = table[key]
        still = entry["still"]
        if entry["info"].get("hasVideo") is False:
            warnings.append(f"segment {i}: {entry['path']} has no picture, lay it over a segment as a cover, skipped")
            continue
        duration = float(entry["info"].get("duration") or 0)
        try:
            if still:
                hold = raw.get("duration")
                if hold is None:
                    hold = float(raw["end"]) - float(raw.get("start", 0.0))
                start, end = 0.0, clamp(float(hold), 0.0, 60.0)
            else:
                start, end = float(raw["start"]), float(raw["end"])
        except (KeyError, TypeError, ValueError):
            warnings.append(f"segment {i}: needs numeric start and end{' (or duration for a picture)' if still else ''}, skipped")
            continue
        if not still:
            start, end = clamp(start, 0.0, duration), clamp(end, 0.0, duration)
        if end - start < minLength:
            warnings.append(f"segment {i}: shorter than 2 frames, skipped")
            continue
        speed = 1.0 if still else round(clamp(float(raw.get("speed", 1.0) or 1.0), speedMin, speedMax), 3)
        audio = raw.get("audio") or ("keep" if speed == 1.0 else "mute")
        if audio == "keep" and speed != 1.0:
            # sped-up audio sounds broken on asmr and speech
            warnings.append(f"segment {i}: audio muted at x{speed}")
            audio = "mute"
        if still or entry["info"].get("hasAudio") is False:
            audio = "mute"
        segLength = (end - start) / speed
        title = masks.normalizeTitle(raw.get("title"), segLength)
        if title and title["behind"] and speed != 1.0:
            warnings.append(f"segment {i}: title behind the subject needs speed 1, placed in front")
            title["behind"] = False
        if title and title["behind"] and not title["subjectBox"]:
            warnings.append(f"segment {i}: title behind needs subject.box, placed in front")
            title["behind"] = False
        if title and title["behind"] and key != "main":
            warnings.append(f"segment {i}: title behind the subject works on the main rush only, placed in front")
            title["behind"] = False
        attach = []
        for j, a in enumerate(raw.get("attach") or []):
            item = normalizeAttach(a, segLength)
            if item is None:
                warnings.append(f"segment {i}: attach {j} needs text or image and a box, skipped")
            elif speed != 1.0 or still:
                warnings.append(f"segment {i}: attach {j} needs a video at speed 1, skipped")
            else:
                attach.append(item)
        stabilize = raw.get("stabilize")
        if stabilize and still:
            stabilize = None
        if stabilize:
            stabilize = stabilize if isinstance(stabilize, dict) else {}
            stabilize = {"smoothness": clamp(float(stabilize.get("smoothness", 0.5)), 0.0, 1.0),
                         "method": "warp" if stabilize.get("method") == "warp" else "transform"}
        covers = []
        rawCovers = raw.get("cover") or raw.get("covers") or []
        for j, c in enumerate([rawCovers] if isinstance(rawCovers, dict) else rawCovers):
            try:
                cover = normalizeCover(c, segLength, table)
            except (KeyError, TypeError, ValueError) as err:
                warnings.append(f"segment {i}: cover {j} {err or 'needs media'}, skipped")
                continue
            warnings.extend(f"segment {i}: cover {j} {w}" for w in cover.pop("warnings"))
            covers.append(cover)
        seg = {
            "start": round(start, 3),
            "end": round(end, 3),
            "speed": speed,
            "audio": "mute" if audio == "mute" else "keep",
            "media": key,
            "still": still,
            "zoom": normalizeZoom(raw.get("zoom")),
            "title": title,
            "attach": attach,
            "stabilize": stabilize or None,
            "cover": covers,
            # split edits: sound starts before the picture (j) or runs on over the next one (l)
            "jcut": round(clamp(float(raw.get("jcut", 0.0) or 0.0), 0.0, 3.0), 3),
            "lcut": round(clamp(float(raw.get("lcut", 0.0) or 0.0), 0.0, 3.0), 3),
            "note": str(raw.get("note", ""))[:200],
        }
        segments.append(seg)
    if not segments:
        raise ValueError("the plan has no usable segment: " + "; ".join(warnings))
    duration = float(info.get("duration") or 0)
    zones = []
    for i, raw in enumerate(plan.get("blur") or []):
        try:
            zones.append(masks.normalizeZone(raw, duration))
        except (KeyError, TypeError, ValueError):
            warnings.append(f"blur {i}: needs box [x0, y0, x1, y1] or keys, skipped")
    screens = []
    for i, raw in enumerate(plan.get("screens") or []):
        try:
            quad = [[clamp(float(x), -0.5, 1.5), clamp(float(y), -0.5, 1.5)] for x, y in raw["quad"]]
            if len(quad) != 4 or not raw.get("media"):
                raise ValueError
            start = clamp(float(raw.get("from", 0.0)), 0.0, duration)
            end = clamp(float(raw.get("to", duration)), start, duration)
            screens.append({"quad": quad, "from": round(start, 3), "to": round(end, 3), "media": str(raw["media"]),
                            "label": str(raw.get("label", ""))[:60]})
        except (KeyError, TypeError, ValueError):
            warnings.append(f"screen {i}: needs quad (4 corners: upper left, upper right, lower right, lower left), from, to and media, skipped")
    if (zones or screens) and any(s["media"] != "main" for s in segments):
        warnings.append("blur zones and replaced screens follow the main rush only, segments from other files are left as they are")
    return {"name": str(plan.get("name") or "chopshopMCP edit"), "format": plan.get("format", "source"),
            "segments": segments, "blur": zones, "screens": screens, "warnings": warnings,
            "media": table.entries}


def normalizeAttach(raw: dict, segLength: float) -> dict | None:
    """a label or sticker that rides on a tracked object. box = the object at `from` (seconds into the segment)."""
    if not isinstance(raw, dict) or not raw.get("box") or not (str(raw.get("text", "")).strip() or raw.get("image")):
        return None
    start = clamp(float(raw.get("from", 0.0)), 0.0, segLength)
    end = clamp(float(raw.get("to", segLength)), start, segLength)
    anchor = raw.get("anchor", "above")
    offsets = {"above": (0.0, -0.5), "below": (0.0, 0.5), "left": (-0.5, 0.0), "right": (0.5, 0.0), "center": (0.0, 0.0)}
    return {
        "text": str(raw.get("text", "")).strip()[:80],
        "image": raw.get("image"),
        "box": masks.normalizeBox(raw["box"]),
        "from": round(start, 3),
        "to": round(end if end > start else segLength, 3),
        # offset in box sizes from the box center, gap keeps the label off the object
        "offset": [float(v) for v in raw.get("offset", offsets.get(anchor, offsets["above"]))],
        "gap": float(raw.get("gap", 0.02)),
        "size": clamp(float(raw.get("size", 0.05)), 0.02, 0.3),
        "color": raw.get("color", "#FFFFFF"),
        "stroke": clamp(float(raw.get("stroke", 0.12)), 0.0, 0.3),
        "font": raw.get("font"),
        "scaleWithObject": bool(raw.get("scaleWithObject", False)),
        "preset": raw.get("preset"),
    }


def viewAt(zoom: dict | None, fraction: float) -> dict:
    """framing at a point of a segment (0 start, 1 end), for layers that must follow v1."""
    if not zoom:
        return normalizeView(None)
    a, b = zoom["from"], zoom["to"] or zoom["from"]
    f = clamp(fraction, 0.0, 1.0)
    return {k: round(a[k] + f * (b[k] - a[k]), 4) for k in ("scale", "x", "y")}


def motionFor(view: dict, srcW: int, srcH: int, seqW: int, seqH: int) -> dict:
    """premiere motion values that frame the point of interest, never showing black edges."""
    fill = max(seqW / srcW, seqH / srcH)
    scaleAbs = fill * view["scale"]

    def axis(center: float, src: int, seq: int) -> float:
        half = scaleAbs * src / seq / 2
        if half <= 0.5:
            return 0.5
        pos = 0.5 - scaleAbs * (center - 0.5) * src / seq
        return round(clamp(pos, 1 - half, half), 4)

    return {"scalePct": round(100 * scaleAbs, 2), "posX": axis(view["x"], srcW, seqW), "posY": axis(view["y"], srcH, seqH)}


def mediaSize(entry: dict, seqW: int, seqH: int) -> tuple:
    info = entry["info"] if "info" in entry else entry
    return int(info.get("width") or seqW), int(info.get("height") or seqH)


def motionSpecs(segments: list, info: dict, seqW: int, seqH: int, mediaTable: dict | None = None) -> list:
    """one spec per video clip that needs scale or position, indexed like the clips on v1."""
    specs = []
    for index, seg in enumerate(segments):
        entry = (mediaTable or {}).get(seg.get("media", "main")) or {"info": info}
        srcW, srcH = mediaSize(entry, seqW, seqH)
        sizeDiffers = (srcW, srcH) != (seqW, seqH)
        zoom = seg["zoom"]
        if not zoom and not sizeDiffers:
            continue
        zoom = zoom or {"from": normalizeView(None), "to": None}
        spec = {"track": 0, "index": index, "from": motionFor(zoom["from"], srcW, srcH, seqW, seqH), "to": None}
        if zoom["to"]:
            spec["to"] = motionFor(zoom["to"], srcW, srcH, seqW, seqH)
        specs.append(spec)
    return specs


def coverMotion(cover: dict, srcW: int, srcH: int, seqW: int, seqH: int) -> dict:
    """motion values of a cover: inside its box (picture in picture), or the whole frame (fill or fit, zoom)."""
    if cover["box"]:
        x0, y0, x1, y1 = cover["box"]
        scaleAbs = min((x1 - x0) * seqW / srcW, (y1 - y0) * seqH / srcH)
        first = {"scalePct": round(100 * scaleAbs, 2), "posX": round((x0 + x1) / 2, 4), "posY": round((y0 + y1) / 2, 4)}
        return {"from": first, "to": None}
    if cover["fit"] == "fit":
        scaleAbs = min(seqW / srcW, seqH / srcH)
        return {"from": {"scalePct": round(100 * scaleAbs, 2), "posX": 0.5, "posY": 0.5}, "to": None}
    zoom = cover["zoom"] or {"from": normalizeView(None), "to": None}
    return {"from": motionFor(zoom["from"], srcW, srcH, seqW, seqH),
            "to": motionFor(zoom["to"], srcW, srcH, seqW, seqH) if zoom["to"] else None}


def outputMap(clips: list, exactFps: float, segments: list) -> list:
    """where each segment landed on the new timeline, so captions can be placed."""
    out = []
    for clip in clips:
        seg = segments[clip["segment"]]
        entry = {
            "source": [seg["start"], seg["end"]],
            "output": [round(clip["start"] / exactFps, 3), round(clip["end"] / exactFps, 3)],
            "speed": seg["speed"],
            "note": seg["note"],
        }
        if seg.get("media", "main") != "main":
            entry["media"] = seg["media"]
        if seg.get("audio") == "mute":
            entry["muted"] = True
        if seg.get("cover"):
            entry["covers"] = [{"media": c["media"], "at": round(clip["start"] / exactFps + c["from"], 3),
                                "length": c["length"], "audio": c["audio"]} for c in seg["cover"]]
        out.append(entry)
    return out


def srtTime(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def captionsToSrt(captions: list) -> str:
    """captions are in output (sequence) seconds."""
    blocks = []
    cues = sorted((c for c in captions if float(c["end"]) > float(c["start"]) and str(c.get("text", "")).strip()),
                  key=lambda c: float(c["start"]))
    for i, cue in enumerate(cues, start=1):
        text = str(cue["text"]).strip().replace("\r", "")
        blocks.append(f"{i}\n{srtTime(float(cue['start']))} --> {srtTime(float(cue['end']))}\n{text}\n")
    return "\n".join(blocks)
