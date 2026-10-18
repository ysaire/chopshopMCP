"""fcp7 xml (xmeml v4) writer, modeled on what premiere 25 itself exports.

premiere writes every stereo source track as two "exploded" xml tracks (left, right)
pointing at the same sourcetrack, see tests/data/premiere2025Export.xml.
"""

import os
import uuid
import xml.etree.ElementTree as et
from urllib.parse import quote

ticksPerSecond = 254016000000


def rateFromFps(fps: float) -> tuple:
    """returns (timebase, ntsc, exactFps). 29.97 -> (30, True, 29.97002997)."""
    rounded = round(fps)
    if abs(fps - rounded) < 0.01:
        return int(rounded), False, float(rounded)
    timebase = int(round(fps * 1001 / 1000))
    return timebase, True, timebase * 1000 / 1001


def ticksPerFrame(timebase: int, ntsc: bool) -> int:
    return ticksPerSecond * 1001 // (timebase * 1000) if ntsc else ticksPerSecond // timebase


def fpsFromTicks(ticks) -> float:
    return ticksPerSecond / float(ticks)


def isWindowsPath(mediaPath: str) -> bool:
    return len(mediaPath) > 2 and mediaPath[1] == ":" and mediaPath[0].isalpha()


def pathToUrl(mediaPath: str) -> str:
    # unc share: \\nas\rushes\a.mp4 -> file://nas/rushes/a.mp4
    if mediaPath.startswith("\\\\") or mediaPath.startswith("//"):
        return "file://" + quote(mediaPath.replace("\\", "/")[2:], safe="/")
    absPath = mediaPath if isWindowsPath(mediaPath) else os.path.abspath(mediaPath)
    normalized = absPath.replace("\\", "/")
    if isWindowsPath(mediaPath):
        return "file://localhost/" + normalized[0] + "%3a" + quote(normalized[2:], safe="/")
    return "file://localhost" + quote(normalized, safe="/")


def sub(parent, tag, text=None, **attrs):
    node = et.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        node.text = str(text)
    return node


def addRate(parent, timebase: int, ntsc: bool):
    rate = sub(parent, "rate")
    sub(rate, "timebase", timebase)
    sub(rate, "ntsc", "TRUE" if ntsc else "FALSE")


def addTimecode(parent, timebase: int, ntsc: bool):
    tc = sub(parent, "timecode")
    addRate(tc, timebase, ntsc)
    sub(tc, "string", "00:00:00:00")
    sub(tc, "frame", 0)
    sub(tc, "displayformat", "NDF")


def toClips(items: list, exactFps: float) -> list:
    """[start, end] pairs or plan segments in source seconds -> back-to-back frame-accurate clips."""
    clips = []
    cursor = 0
    for index, item in enumerate(items):
        if isinstance(item, dict):
            start, end = item["start"], item["end"]
            speed, audio = float(item.get("speed", 1.0)), item.get("audio", "keep")
        else:
            start, end = item
            speed, audio = 1.0, "keep"
        inFrame = int(round(start * exactFps))
        outFrame = int(round(end * exactFps))
        if outFrame <= inFrame:
            continue
        length = max(1, int(round((outFrame - inFrame) / speed)))
        clip = {"in": inFrame, "out": outFrame, "start": cursor, "end": cursor + length,
                "speed": speed, "audio": audio != "mute", "segment": index, "media": "main"}
        if isinstance(item, dict):
            for key in ("videoFile", "nest", "media", "still", "filters"):
                if item.get(key):
                    clip[key] = item[key]
        if clip.get("still"):
            clip["audio"] = False
        clips.append(clip)
        cursor += length
    return clips


def addSpeedFilter(parent, speed: float, mediaType: str):
    """constant speed, the way fcp xml conforms carry it (premiere: clip speed / duration)."""
    flt = sub(parent, "filter")
    effect = sub(flt, "effect")
    sub(effect, "name", "Time Remap")
    sub(effect, "effectid", "timeremap")
    sub(effect, "effectcategory", "motion")
    sub(effect, "effecttype", "motion")
    sub(effect, "mediatype", mediaType)
    for paramId, value, low, high in (
        ("variablespeed", 0, 0, 1),
        ("speed", round(speed * 100, 3), -100000, 100000),
        ("reverse", "FALSE", None, None),
        ("frameblending", "FALSE", None, None),
    ):
        param = sub(effect, "parameter", authoringApp="PremierePro")
        sub(param, "parameterid", paramId)
        sub(param, "name", paramId)
        if low is not None:
            sub(param, "valuemin", low)
            sub(param, "valuemax", high)
        sub(param, "value", value)


def audioStreamsOf(info: dict) -> list:
    if not info.get("hasAudio"):
        return []
    streams = info.get("audioStreams")
    if streams:
        return streams
    return [{"channels": info.get("channels") or 2, "sampleRate": info.get("sampleRate") or 48000}]


def audioLayout(info: dict) -> list:
    """one entry per xml audio track: stereo sources explode into left + right tracks."""
    layout = []
    for sourceTrack, stream in enumerate(audioStreamsOf(info), start=1):
        if (stream.get("channels") or 2) >= 2:
            for exploded in (0, 1):
                layout.append({"sourceTrack": sourceTrack, "exploded": exploded, "explodedCount": 2,
                               "stereo": True, "outputChannel": exploded + 1})
        else:
            layout.append({"sourceTrack": sourceTrack, "exploded": 0, "explodedCount": 1,
                           "stereo": False, "outputChannel": 1})
    return layout


def gaussianBlurFilter(radius: float) -> dict:
    """the shape premiere 25 writes for gaussian blur in its own xml export."""
    return {"name": "Gaussian Blur", "effectid": "Gaussian Blur", "category": "Blur", "type": "motion",
            "params": [{"id": "radius", "name": "Radius", "min": 0, "max": 100, "value": round(float(radius), 2)}]}


def addFilter(parent, spec: dict):
    flt = sub(parent, "filter")
    effect = sub(flt, "effect")
    sub(effect, "name", spec["name"])
    sub(effect, "effectid", spec.get("effectid", spec["name"]))
    sub(effect, "effectcategory", spec.get("category", ""))
    sub(effect, "effecttype", spec.get("type", "motion"))
    sub(effect, "mediatype", spec.get("mediatype", "video"))
    sub(effect, "pproBypass", "false")
    for p in spec.get("params", []):
        param = sub(effect, "parameter", authoringApp="PremierePro")
        sub(param, "parameterid", p["id"])
        sub(param, "name", p.get("name", p["id"]))
        if p.get("min") is not None:
            sub(param, "valuemin", p["min"])
            sub(param, "valuemax", p["max"])
        sub(param, "value", p["value"])


def addFormat(video, timebase: int, ntsc: bool, width: int, height: int):
    fmt = sub(video, "format")
    sc = sub(fmt, "samplecharacteristics")
    addRate(sc, timebase, ntsc)
    sub(sc, "width", width)
    sub(sc, "height", height)
    sub(sc, "anamorphic", "FALSE")
    sub(sc, "pixelaspectratio", "square")
    sub(sc, "fielddominance", "none")
    sub(sc, "colordepth", 24)


def laneOf(lane) -> dict:
    return lane if isinstance(lane, dict) else {"items": lane, "enabled": True}


def layoutKey(info: dict) -> tuple:
    return tuple((lane["stereo"], lane["sourceTrack"]) for lane in audioLayout(info))


def premiereTrackCount(layout: list) -> int:
    """premiere folds an exploded stereo pair back into one track."""
    return sum(1 for lane in layout if lane["exploded"] == 0)


def clipAudioGroups(clips: list, infos: dict, pieces: dict | None = None) -> list:
    """sound of the v1 clips: one group of tracks per audio layout (the rush's first), each clip's pieces on its
    group. pieces = {clip position: [{start, end, in, out}]} replaces a clip's own range (split edits, cut spans);
    the first piece stays linked to the picture. a layout with no piece left gets no tracks."""
    heard = []
    for n, clip in enumerate(clips, start=1):
        key = clip.get("media", "main")
        if not clip["audio"] or not audioStreamsOf(infos[key]):
            continue
        own = [{"start": clip["start"], "end": clip["end"], "in": clip["in"], "out": clip["out"]}]
        for j, piece in enumerate(pieces.get(n - 1, own) if pieces is not None else own):
            if piece["end"] > piece["start"]:
                heard.append((key, dict(piece, media=key, link=f"v1-{n}" if j == 0 else f"v1-{n}-{j}")))
    groups, byKey = [], {}
    order = sorted(heard, key=lambda kv: kv[0] != "main")  # the rush's layout first, stable otherwise
    for key, item in order:
        layout = layoutKey(infos[key])
        if layout not in byKey:
            byKey[layout] = len(groups)
            groups.append({"layout": audioLayout(infos[key]), "items": [], "role": "rush"})
        groups[byKey[layout]]["items"].append(item)
    for group in groups:
        group["items"].sort(key=lambda it: it["start"])
    return groups


def premiereAudioTracks(groups: list) -> list:
    """0-based premiere audio track numbers of each group, in xml order."""
    out, cursor = [], 0
    for group in groups:
        n = premiereTrackCount(group["layout"])
        out.append(list(range(cursor, cursor + n)))
        cursor += n
    return out


def buildXmeml(mediaPath: str, keepRanges: list, info: dict, sequenceName: str,
               fps: float | None = None, width: int | None = None, height: int | None = None,
               clipName: str | None = None, layers: list | None = None, nests: dict | None = None,
               extraAudio: list | None = None, sources: dict | None = None, audioGroups: list | None = None) -> str:
    """keepRanges: [start, end] pairs or plan segments, cut back to back on v1. a segment with "videoFile"
    plays a rendered copy, one with "nest" plays a nested sequence (same frames as the source), one with "media"
    plays another file (a key of sources), "still" holds a picture.

    layers: video tracks above v1, each a list of items or {"items": [...], "enabled": bool}. items:
      rendered file {"path", "start", "end" (sequence frames), "width", "height", "frames" (None for stills)}
      source duplicate {"kind": "source", "start", "end", "in", "out", "filters": [...]}
      nested sequence {"kind": "nest", "nest": key, "start", "end", "in", "out"}
      another file {"kind": "media", "media": key, "start", "end", "in", "out", "speed", "link"}
    nests: {key: {"name", "width", "height", "lanes": [lane, ...]}}, lanes like layers, v1 first.
    a nest is written in full where it is first used, then referenced by id.
    sources: other files {key: {"path", "info"}}, "main" is the rush.
    audioGroups: every audio track, in order: [{"layout": audioLayout(info), "items": [{"media", "start", "end",
      "in", "out", "link"}]}]. items sharing a link move together (and with a video item of that link).
      default: the v1 clips' own sound (clipAudioGroups), then extraAudio.
    extraAudio: other sound files under the rush tracks (a voice over):
      [{"path", "info" (probe), "clips": [{"in", "out", "start", "end"} in frames]}]
    """
    seqFps = fps or info.get("fps") or 30.0
    timebase, ntsc, exactFps = rateFromFps(seqFps)
    frameTicks = ticksPerFrame(timebase, ntsc)
    seqWidth = int(width or info.get("width") or 1920)
    seqHeight = int(height or info.get("height") or 1080)
    fileName = os.path.basename(mediaPath.replace("\\", "/"))
    itemName = clipName or fileName
    clips = toClips(keepRanges, exactFps)
    totalFrames = clips[-1]["end"] if clips else 0
    hasVideo = bool(info.get("hasVideo"))
    nests = nests or {}

    mediaTable = {"main": {"path": mediaPath, "info": info, "name": itemName, "masterclip": "masterclip-1"}}
    for n, (key, src) in enumerate((sources or {}).items(), start=2):
        if key != "main":
            mediaTable[key] = {"path": src["path"], "info": src["info"], "masterclip": f"masterclip-{n}",
                               "name": os.path.basename(src["path"].replace("\\", "/"))}
    for k, extra in enumerate(extraAudio or [], start=1):
        mediaTable[f"extra{k}"] = {"path": extra["path"], "info": dict(extra["info"], hasAudio=True),
                                   "masterclip": f"masterclip-x{k}",
                                   "name": os.path.basename(extra["path"].replace("\\", "/"))}
    for entry in mediaTable.values():
        entry["frames"] = int(round((entry["info"].get("duration") or 0) * exactFps))
    infos = {key: entry["info"] for key, entry in mediaTable.items()}
    fileFrames = mediaTable["main"]["frames"]
    streams = audioStreamsOf(info)
    sampleRate = int((streams[0].get("sampleRate") if streams else None) or info.get("sampleRate") or 48000)

    if audioGroups is None:
        audioGroups = clipAudioGroups(clips, infos)
        for k, extra in enumerate(extraAudio or [], start=1):
            audioGroups.append({"layout": audioLayout(infos[f"extra{k}"]), "role": "extra", "items": [
                dict(piece, media=f"extra{k}", link=f"x{k}-{i}") for i, piece in enumerate(extra["clips"], start=1)]})

    root = et.Element("xmeml", {"version": "4"})
    seq = sub(root, "sequence", id="sequence-1", explodedTracks="true")
    sub(seq, "uuid", str(uuid.uuid4()))
    sub(seq, "duration", totalFrames)
    addRate(seq, timebase, ntsc)
    sub(seq, "name", sequenceName)
    mediaNode = sub(seq, "media")

    fileIds = {}
    nestIds = {}

    # every clipitem of a link group (picture and each exploded sound track) points at all the others
    linkMembers = {}
    audioTracks = []
    xmlTrack = 0
    for g, group in enumerate(audioGroups):
        items = group["items"]
        for lane in group["layout"]:
            xmlTrack += 1
            placed = []
            for idx, item in enumerate(items, start=1):
                itemId = f"clipitem-a{xmlTrack}-{idx}"
                link = item.get("link") or f"g{g}-{idx}"
                linkMembers.setdefault(link, []).append(("audio", xmlTrack, idx, itemId))
                placed.append((item, itemId, link))
            audioTracks.append({"lane": lane, "items": placed})

    def registerVideo(link: str, trackNumber: int, index: int, itemId: str):
        linkMembers.setdefault(link, []).insert(0, ("video", trackNumber, index, itemId))

    if hasVideo:
        for i, clip in enumerate(clips, start=1):
            registerVideo(f"v1-{i}", 1, i, f"clipitem-v{i}")
    for laneNumber, lane in enumerate(layers or [], start=2):
        for i, item in enumerate(laneOf(lane)["items"], start=1):
            if item.get("link"):
                registerVideo(item["link"], laneNumber, i, f"clipitem-l{laneNumber}-{i}")

    def addLinks(parent, link: str | None):
        members = linkMembers.get(link) if link else None
        if not members:
            return
        for mediatype, trackNumber, index, itemId in members:
            ln = sub(parent, "link")
            sub(ln, "linkclipref", itemId)
            sub(ln, "mediatype", mediatype)
            sub(ln, "trackindex", trackNumber)
            sub(ln, "clipindex", index)
            if mediatype == "audio":
                sub(ln, "groupindex", 1)

    def sampleChars(parent, w, h):
        sc = sub(parent, "samplecharacteristics")
        addRate(sc, timebase, ntsc)
        sub(sc, "width", w)
        sub(sc, "height", h)
        sub(sc, "anamorphic", "FALSE")
        sub(sc, "pixelaspectratio", "square")
        sub(sc, "fielddominance", "none")

    def fileNode(parent, key: str = "main", rendered: dict | None = None):
        """a played file (with its sound) or a rendered video / still; full definition on first use only."""
        fileKey = rendered["path"] if rendered else "media:" + key
        if fileKey in fileIds:
            sub(parent, "file", id=fileIds[fileKey])
            return fileIds[fileKey]
        fileId = f"file-{len(fileIds) + 1}"
        fileIds[fileKey] = fileId
        f = sub(parent, "file", id=fileId)
        if rendered:
            sub(f, "name", os.path.basename(rendered["path"]))
            sub(f, "pathurl", pathToUrl(rendered["path"]))
            addRate(f, timebase, ntsc)
            sub(f, "duration", rendered.get("frames") or rendered.get("length") or 1)
            addTimecode(f, timebase, ntsc)
            sampleChars(sub(sub(f, "media"), "video"), rendered.get("width") or seqWidth, rendered.get("height") or seqHeight)
            return fileId
        entry = mediaTable[key]
        mInfo = entry["info"]
        sub(f, "name", entry["name"] if key != "main" else fileName)
        sub(f, "pathurl", pathToUrl(entry["path"]))
        addRate(f, timebase, ntsc)
        sub(f, "duration", entry["frames"])
        addTimecode(f, timebase, ntsc)
        fm = sub(f, "media")
        if mInfo.get("hasVideo"):
            sampleChars(sub(fm, "video"), mInfo.get("width") or seqWidth, mInfo.get("height") or seqHeight)
        # one audio block per source track, like premiere does
        for stream in audioStreamsOf(mInfo):
            a = sub(fm, "audio")
            sc = sub(a, "samplecharacteristics")
            sub(sc, "depth", 16)
            sub(sc, "samplerate", int(stream.get("sampleRate") or sampleRate))
            sub(a, "channelcount", int(stream.get("channels") or 2))
        return fileId

    def spedClip(clip: dict) -> dict:
        """premiere multiplies a clip's in and out by its speed when it reads the xml: written divided."""
        if float(clip.get("speed", 1.0)) == 1.0:
            return clip
        inFrame = int(round(clip["in"] / clip["speed"]))
        return dict(clip, **{"in": inFrame, "out": inFrame + (clip["end"] - clip["start"])})

    def clipCommon(parent, clip, key: str = "main"):
        entry = mediaTable[key]
        sub(parent, "masterclipid", entry["masterclip"])
        sub(parent, "name", entry["name"])
        sub(parent, "enabled", "TRUE")
        sub(parent, "duration", entry["frames"])
        addRate(parent, timebase, ntsc)
        sub(parent, "start", clip["start"])
        sub(parent, "end", clip["end"])
        sub(parent, "in", clip["in"])
        sub(parent, "out", clip["out"])
        sub(parent, "pproTicksIn", clip["in"] * frameTicks)
        sub(parent, "pproTicksOut", clip["out"] * frameTicks)

    def nestNode(parent, key):
        if key in nestIds:
            sub(parent, "sequence", id=nestIds[key])
            return
        nestId = f"sequence-{len(nestIds) + 2}"
        nestIds[key] = nestId
        nest = nests[key]
        # premiere writes nests as duration, rate, name, media, timecode
        node = sub(parent, "sequence", id=nestId)
        sub(node, "duration", fileFrames)
        addRate(node, timebase, ntsc)
        sub(node, "name", nest["name"])
        video = sub(sub(node, "media"), "video")
        addFormat(video, timebase, ntsc, int(nest["width"]), int(nest["height"]))
        for laneNumber, lane in enumerate(nest["lanes"], start=1):
            writeLane(video, laneOf(lane), f"n{nestId.split('-')[1]}-{laneNumber}")
        addTimecode(node, timebase, ntsc)

    def writeItem(track, item: dict, itemId: str):
        kind = item.get("kind") or ("file" if item.get("path") else "source")
        start, end = int(item["start"]), int(item["end"])
        length = max(1, end - start)
        inFrame = int(item.get("in", 0))
        outFrame = int(item.get("out", inFrame + length))
        speed = float(item.get("speed", 1.0))
        if kind in ("nest", "source", "media") and speed != 1.0:
            # premiere multiplies a clip's in and out by its speed when it reads the xml
            inFrame = int(round(inFrame / speed))
            outFrame = inFrame + length
        node = sub(track, "clipitem", id=itemId)
        if kind in ("source", "media"):
            entry = mediaTable[item.get("media", "main") if kind == "media" else "main"]
            sub(node, "masterclipid", entry["masterclip"])
            sub(node, "name", entry["name"])
            duration = entry["frames"]
        elif kind == "nest":
            sub(node, "masterclipid", f"masterclip-nest-{item['nest']}")
            sub(node, "name", nests[item["nest"]]["name"])
            duration = fileFrames
        else:
            sub(node, "masterclipid", itemId.replace("clipitem", "masterclip"))
            sub(node, "name", os.path.basename(item["path"]))
            duration = item.get("frames") or length
        sub(node, "enabled", "TRUE")
        sub(node, "duration", duration)
        addRate(node, timebase, ntsc)
        sub(node, "start", start)
        sub(node, "end", start + length)
        sub(node, "in", inFrame)
        sub(node, "out", outFrame)
        sub(node, "pproTicksIn", inFrame * frameTicks)
        sub(node, "pproTicksOut", outFrame * frameTicks)
        if kind == "nest":
            nestNode(node, item["nest"])
        elif kind == "media":
            sub(node, "alphatype", "none")
            fileNode(node, item["media"])
        else:
            sub(node, "alphatype", "straight" if kind == "file" and item.get("alpha", True) else "none")
            fileNode(node, rendered=dict(item, length=length) if kind == "file" else None)
        for spec in item.get("filters") or []:
            addFilter(node, spec)
        if float(item.get("speed", 1.0)) != 1.0:
            addSpeedFilter(node, float(item["speed"]), "video")
        return node

    def writeLane(video, lane: dict, prefix: str):
        track = sub(video, "track")
        for i, item in enumerate(lane["items"], start=1):
            node = writeItem(track, item, f"clipitem-{prefix}-{i}")
            if item.get("link"):
                addLinks(node, item["link"])
        sub(track, "enabled", "TRUE" if lane.get("enabled", True) else "FALSE")
        sub(track, "locked", "FALSE")

    video = sub(mediaNode, "video")
    addFormat(video, timebase, ntsc, seqWidth, seqHeight)
    if hasVideo:
        track = sub(video, "track")
        for i, clip in enumerate(clips, start=1):
            rendered = clip.get("videoFile")
            key = clip.get("media", "main")
            if rendered:
                # rendered copy starts at the segment's first frame
                length = clip["out"] - clip["in"]
                item = sub(track, "clipitem", id=f"clipitem-v{i}")
                clipCommon(item, dict(clip, **{"in": 0, "out": length}))
                item.find("masterclipid").text = f"masterclip-r{i}"
                item.find("name").text = os.path.basename(rendered["path"])
                item.find("duration").text = str(rendered.get("frames") or length)
                sub(item, "alphatype", "none")
                fileNode(item, rendered=rendered)
                if clip["speed"] != 1.0:
                    addSpeedFilter(item, clip["speed"], "video")
            elif clip.get("nest"):
                item = writeItem(track, {"kind": "nest", "nest": clip["nest"], "start": clip["start"], "end": clip["end"],
                                         "in": clip["in"], "out": clip["out"], "speed": clip["speed"]}, f"clipitem-v{i}")
            elif clip.get("still"):
                still = mediaTable[key]
                item = writeItem(track, {"kind": "file", "path": still["path"], "start": clip["start"], "end": clip["end"],
                                         "in": 0, "out": clip["end"] - clip["start"], "frames": None, "alpha": True,
                                         "width": still["info"].get("width"), "height": still["info"].get("height")},
                                 f"clipitem-v{i}")
            elif key != "main":
                item = writeItem(track, {"kind": "media", "media": key, "start": clip["start"], "end": clip["end"],
                                         "in": clip["in"], "out": clip["out"], "speed": clip["speed"]}, f"clipitem-v{i}")
            else:
                item = sub(track, "clipitem", id=f"clipitem-v{i}")
                clipCommon(item, spedClip(clip))
                sub(item, "alphatype", "none")
                fileNode(item)
                for spec in clip.get("filters") or []:
                    addFilter(item, spec)
                if clip["speed"] != 1.0:
                    addSpeedFilter(item, clip["speed"], "video")
            addLinks(item, f"v1-{i}")
        sub(track, "enabled", "TRUE")
        sub(track, "locked", "FALSE")

    for laneNumber, lane in enumerate(layers or [], start=2):
        writeLane(video, laneOf(lane), f"l{laneNumber}")

    audio = sub(mediaNode, "audio")
    sub(audio, "numOutputChannels", 2)
    afmt = sub(audio, "format")
    asc = sub(afmt, "samplecharacteristics")
    sub(asc, "depth", 16)
    sub(asc, "samplerate", sampleRate)
    outputs = sub(audio, "outputs")
    for channel in (1, 2):
        group = sub(outputs, "group")
        sub(group, "index", channel)
        sub(group, "numchannels", 1)
        sub(group, "downmix", 0)
        ch = sub(group, "channel")
        sub(ch, "index", channel)

    for entry in audioTracks:
        lane = entry["lane"]
        track = sub(
            audio, "track",
            currentExplodedTrackIndex=lane["exploded"],
            totalExplodedTrackCount=lane["explodedCount"],
            premiereTrackType="Stereo" if lane["stereo"] else "Mono",
        )
        for item, itemId, link in entry["items"]:
            node = sub(track, "clipitem", id=itemId, premiereChannelType="stereo" if lane["stereo"] else "mono")
            key = item.get("media", "main")
            clipCommon(node, item, key)
            fileNode(node, key)
            st = sub(node, "sourcetrack")
            sub(st, "mediatype", "audio")
            sub(st, "trackindex", lane["sourceTrack"])
            addLinks(node, link)
        sub(track, "enabled", "TRUE")
        sub(track, "locked", "FALSE")
        sub(track, "outputchannelindex", lane["outputChannel"])

    addTimecode(seq, timebase, ntsc)

    et.indent(root, space="  ")
    body = et.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + body + "\n"


def writeXmeml(outPath: str, *args, **kwargs) -> str:
    content = buildXmeml(*args, **kwargs)
    with open(outPath, "w", encoding="utf-8") as fh:
        fh.write(content)
    return outPath
