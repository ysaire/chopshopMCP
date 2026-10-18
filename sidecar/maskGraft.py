"""native premiere masks (the pen masks of an effect) grafted into a saved copy of the .prproj, then brought back
with app.project.importSequences: neither the extendscript dom nor qe can write a mask path.

a mask is an AE.ADBE AEMask filter component listed in the SubComponents of the effect it limits. its path is a
2cin block: magic, u32 (2, 0, vertex count, 1 = closed), then per vertex six float32 normalized to the frame
(vertex, in tangent, out tangent) and u32 1, with u32 0 between vertices. keyframes are media ticks.
"""

import base64
import gzip
import re
import struct
import uuid
import xml.etree.ElementTree as et
from xml.sax.saxutils import escape

ticksPerSecond = 254016000000
startPosition = "-91445760000000000"
kappa = 0.5522847498  # bezier handle for a quarter circle
classIds = {
    "filter": "d10da199-beea-4dd1-b941-ed3a78766d50",
    "bool": "cc12343e-f113-4d3b-ae05-b287db77d461",
    "float": "a4ff2d6e-7ac2-44f8-9d52-17d9ca50e542",
    "int": "6e02e8bb-2569-46b2-8ab1-4ab11c43e9c8",
    "arb": "313e54d4-6903-49ad-b0bf-8262cdd10f4e",
}
matchNames = {
    "Gaussian Blur": ("AE.ADBE Gaussian Blur 2", "AE.ADBE Gaussian Blur"),
    "Mosaic": ("AE.ADBE Mosaic",),
    "Track Matte Key": ("AE.ADBE Legacy Key Track Matte",),
}
# private data premiere 25 writes on a mask it drew, verbatim with its BinaryHash: the hash keys premiere's blob
# store, and private data without it is not read at all
maskPrivateData = ("a2NpbgEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIDl+f//////AQAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAD"
                   "AAAAAAAAAA8AAAAAAAAA")
maskPrivateHash = "1ed21f21-e32e-e9d2-7c73-a9590000006c"
blobHashes = {"AAAAAAAAAAAAAAAAAAAAAA==": "036b9652-ba57-8e23-9514-59270000001c", "AgAAAAAAAAA=": "5b3d1e1b-2b68-8c23-e1d2-827a00000014"}
# the 24 parameters of an AEMask component, in premiere's order: kind, parameter id, control type, name, value, bounds
maskParams = [
    ("bool", 1, 11, None, "false", ("false", "false")),
    ("bool", 2, 16, None, "false", ("false", "true")),
    ("bool", 3, 16, None, "false", ("false", "true")),
    ("bool", 4, 16, None, "false", ("false", "true")),
    ("bool", 14, 16, None, "false", ("false", "true")),
    ("bool", 15, 16, None, "false", ("false", "true")),
    ("bool", 5, 12, None, "false", ("false", "false")),
    ("path", 6, 22, "Mask Path", None, None),
    ("bool", 19, 11, None, "false", ("false", "false")),
    ("bool", 20, 16, None, "false", ("false", "true")),
    ("bool", 21, 16, None, "false", ("false", "true")),
    ("bool", 22, 12, None, "false", ("false", "false")),
    ("arb", 23, 22, "Mask Path Edit", None, None),
    ("int", 24, 7, "Mask Selection Mode", "0", ("0", "1")),
    ("float", 7, 8, "Mask Feather", "feather", ("0", "5000")),
    ("float", 8, 8, "Mask Opacity", "opacity", ("0", "100")),
    ("float", 9, 8, "Mask Expansion", "expansion", ("-5000", "5000")),
    ("bool", 10, 4, None, "inverted", ("false", "true")),
    ("float", 11, 8, None, "2.", ("0", "3")),
    ("float", 12, 8, None, "0.", ("0", "4294967296")),
    ("float", 13, 8, None, "0.5", ("0", "3.4028234663852886e+38")),
    ("float", 16, 8, "Active Path Index", "0.", ("0", "100")),
    ("arb", 17, 22, "Additional Paths", "AAAAAAAAAAAAAAAAAAAAAA==", None),
    ("arb", 18, 22, "Selection Data Param", "AgAAAAAAAAA=", None),
]
uiBounds = {7: ("", "300"), 9: ("-300", "300")}


def fullVertex(v) -> tuple:
    """(x, y) or (x, y, inX, inY, outX, outY); a bare point has straight tangents."""
    if len(v) == 2:
        x, y = float(v[0]), float(v[1])
        return (x, y, x, y, x, y)
    return tuple(float(c) for c in v[:6])


def encodePath(vertices: list) -> bytes:
    out = [b"2cin", struct.pack("<4I", 2, 0, len(vertices), 1)]
    for i, v in enumerate(vertices):
        out.append(struct.pack("<6f", *fullVertex(v)))
        out.append(struct.pack("<I", 1))
        if i < len(vertices) - 1:
            out.append(struct.pack("<I", 0))
    return b"".join(out)


def decodePath(raw: bytes) -> list:
    if raw[:4] != b"2cin":
        raise ValueError("not a 2cin mask path")
    _two, _zero, count, _closed = struct.unpack("<4I", raw[4:20])
    vertices, pos = [], 20
    for i in range(count):
        vertices.append(struct.unpack("<6f", raw[pos:pos + 24]))
        pos += 28 + (4 if i < count - 1 else 0)
    return vertices


def parseKeyframes(text: str) -> list:
    """the <Keyframes> text of a Mask Path, "ticks,base64;...", as [(seconds, vertices)]."""
    out = []
    for pair in (text or "").split(";"):
        if not pair.strip():
            continue
        ticks, block = pair.split(",", 1)
        out.append((int(ticks) / ticksPerSecond, decodePath(base64.b64decode(block.strip()))))
    return out


def bezierPolyline(vertices: list, steps: int = 8) -> list:
    """the closed path sampled along its beziers, steps points per segment, so curved handles rasterize as drawn."""
    full = [fullVertex(v) for v in vertices]
    out = []
    for i, (x0, y0, _ix, _iy, ox, oy) in enumerate(full):
        x3, y3, ix3, iy3, _ox3, _oy3 = full[(i + 1) % len(full)]
        for s in range(steps):
            t = s / steps
            u = 1 - t
            out.append((u * u * u * x0 + 3 * u * u * t * ox + 3 * u * t * t * ix3 + t * t * t * x3,
                        u * u * u * y0 + 3 * u * u * t * oy + 3 * u * t * t * iy3 + t * t * t * y3))
    return out


def rectPath(box) -> list:
    x0, y0, x1, y1 = [float(v) for v in box]
    return [fullVertex(p) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]


def ellipsePath(box) -> list:
    x0, y0, x1, y1 = [float(v) for v in box]
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    kx, ky = kappa * rx, kappa * ry
    return [
        (cx, y0, cx - kx, y0, cx + kx, y0),
        (x1, cy, x1, cy - ky, x1, cy + ky),
        (cx, y1, cx + kx, y1, cx - kx, y1),
        (x0, cy, x0, cy + ky, x0, cy - ky),
    ]


def boxPath(box, shape: str = "rect") -> list:
    return ellipsePath(box) if shape == "ellipse" else rectPath(box)


def isBox(value) -> bool:
    return len(value) == 4 and all(isinstance(c, (int, float)) for c in value)


class Project:
    """the object graph of a decompressed .prproj, enough to find an effect on a clip of a sequence."""

    def __init__(self, text: str):
        self.text = text
        try:
            self.root = et.fromstring(text)
        except et.ParseError as err:
            raise ValueError(f"not a premiere project: {err}")
        if self.root.tag != "PremiereData":
            raise ValueError("not a premiere project (no PremiereData root)")
        self.byId, self.byUid = {}, {}
        for el in self.root:
            if el.get("ObjectID"):
                self.byId[el.get("ObjectID")] = el
            if el.get("ObjectUID"):
                self.byUid[el.get("ObjectUID")] = el

    def ref(self, node):
        return self.byId.get(node.get("ObjectRef")) if node is not None else None

    def uref(self, node):
        return self.byUid.get(node.get("ObjectURef")) if node is not None else None

    def sequences(self) -> list:
        return [(el.get("ObjectUID"), el.findtext("Name") or "") for el in self.root.iter("Sequence") if el.get("ObjectUID")]

    def sequence(self, key: str):
        found = self.byUid.get(key)
        if found is not None and found.tag == "Sequence":
            return found
        named = [self.byUid[uid] for uid, name in self.sequences() if name == key]
        if len(named) > 1:
            raise ValueError(f"several sequences named {key!r}, use the sequence id")
        if not named:
            raise ValueError(f"sequence not found: {key}")
        return named[0]

    def videoTracks(self, seq) -> list:
        for group in seq.findall("TrackGroups/TrackGroup"):
            node = self.ref(group.find("Second"))
            if node is None or node.tag != "VideoTrackGroup":
                continue
            tracks = sorted(node.findall("TrackGroup/Tracks/Track"), key=lambda t: int(t.get("Index") or 0))
            return [t for t in (self.uref(tr) for tr in tracks) if t is not None]
        return []

    def clips(self, track) -> list:
        items = [self.ref(ti) for ti in track.findall("ClipTrack/ClipItems/TrackItems/TrackItem")]
        items = [i for i in items if i is not None]
        return sorted(items, key=lambda i: int(i.findtext("ClipTrackItem/TrackItem/Start") or 0))

    def subClip(self, item):
        return self.ref(item.find("ClipTrackItem/SubClip"))

    def clipName(self, item) -> str:
        sub = self.subClip(item)
        return (sub.findtext("Name") or "") if sub is not None else ""

    def effects(self, item) -> list:
        chain = self.ref(item.find("ClipTrackItem/ComponentOwner/Components"))
        if chain is None:
            return []
        comps = [self.ref(c) for c in chain.findall("ComponentChain/Components/Component")]
        return [c for c in comps if c is not None and c.tag == "VideoFilterComponent"]

    @staticmethod
    def effectName(comp) -> str:
        return comp.findtext("Component/DisplayName") or comp.findtext("MatchName") or ""

    @classmethod
    def matches(cls, comp, effect: str) -> bool:
        match = comp.findtext("MatchName") or ""
        return match in matchNames.get(effect, ()) or match == effect or cls.effectName(comp) == effect

    def nestsOf(self, seq) -> set:
        """uids of the sequences played by clips of this sequence."""
        out = set()
        for track in self.videoTracks(seq):
            for item in self.clips(track):
                sub = self.subClip(item)
                clip = self.ref(sub.find("Clip")) if sub is not None else None
                source = self.ref(clip.find("Clip/Source")) if clip is not None else None
                if source is not None and source.tag == "VideoSequenceSource":
                    nest = source.find("SequenceSource/Sequence")
                    if nest is not None and nest.get("ObjectURef"):
                        out.add(nest.get("ObjectURef"))
        return out

    def clipItem(self, sequence: str, track: int, index: int | None = None, clip: str | None = None):
        seq = self.sequence(sequence)
        name = seq.findtext("Name") or sequence
        tracks = self.videoTracks(seq)
        if track >= len(tracks):
            raise ValueError(f"no track V{track + 1} in {name!r} ({len(tracks)} video tracks)")
        items = self.clips(tracks[track])
        if clip is not None:
            items = [i for i in items if self.clipName(i) == clip]
        pick = index or 0
        if pick >= len(items):
            what = f"named {clip!r}" if clip is not None else f"#{pick}"
            raise ValueError(f"no clip {what} on V{track + 1} of {name!r} ({len(items)} clips)")
        return items[pick]

    def findEffect(self, sequence: str, track: int, index: int | None = None, clip: str | None = None,
                   effect: str = "Gaussian Blur"):
        item = self.clipItem(sequence, track, index, clip)
        for comp in self.effects(item):
            if self.matches(comp, effect):
                return comp
        name = self.sequence(sequence).findtext("Name") or sequence
        have = ", ".join(self.effectName(c) for c in self.effects(item)) or "none"
        raise ValueError(f"no {effect} on V{track + 1} #{index or 0} of {name!r} (effects: {have})")

    def masks(self, comp) -> list:
        """the AEMask subcomponents of a filter component: [{id, slot, instance, feather, opacity, expansion,
        inverted, keys: [(seconds, vertices)]}]. a mask never animated has one key at no time (None)."""
        out = []
        for sub in comp.findall("SubComponents/SubComponent"):
            mask = self.ref(sub)
            if mask is None or mask.findtext("MatchName") != "AE.ADBE AEMask":
                continue
            params = {}
            for ref in mask.findall("Component/Params/Param"):
                node = self.ref(ref)
                if node is not None:
                    params[node.findtext("ParameterID")] = node

            def value(pid: str, default: str) -> str:
                node = params.get(pid)
                start = node.findtext("StartKeyframe") if node is not None else None
                return start.split(",")[1] if start and "," in start else default

            path = params.get("6")
            keys = parseKeyframes(path.findtext("Keyframes")) if path is not None else []
            if not keys and path is not None and (path.findtext("StartKeyframeValue") or "").strip():
                keys = [(None, decodePath(base64.b64decode(path.findtext("StartKeyframeValue").strip())))]
            out.append({"id": mask.get("ObjectID"), "slot": int(sub.get("Index") or len(out)),
                        "instance": mask.findtext("Component/InstanceName") or "",
                        "feather": float(value("7", "10")), "opacity": float(value("8", "100")),
                        "expansion": float(value("9", "0")), "inverted": value("10", "false") == "true", "keys": keys})
        return out

    def clipMasks(self, sequence: str, track: int, index: int | None = None, clip: str | None = None) -> list:
        """the effects of a clip that carry a mask: [{effect, matchName, componentId, masks}]."""
        out = []
        for comp in self.effects(self.clipItem(sequence, track, index, clip)):
            found = self.masks(comp)
            if found:
                out.append({"effect": self.effectName(comp), "matchName": comp.findtext("MatchName") or "",
                            "componentId": comp.get("ObjectID"), "masks": found})
        return out

    def nextObjectId(self) -> int:
        return max((int(k) for k in self.byId if k.isdigit()), default=0) + 1


def floatText(value) -> str:
    value = float(value)
    return f"{int(value)}." if value.is_integer() else repr(value)


def paramXml(objectId: int, spec: tuple, values: dict, keys: list) -> str:
    kind, pid, control, name, value, bounds = spec
    cls = classIds["arb" if kind in ("arb", "path") else kind]
    if kind == "path":
        pairs = "".join(f"{ticks},{b64};" for ticks, b64 in keys)
        return (f'\t<ArbVideoComponentParam ObjectID="{objectId}" ClassID="{cls}" Version="2">\n'
                f"\t\t<ParameterID>{pid}</ParameterID>\n"
                f"\t\t<ParameterControlType>{control}</ParameterControlType>\n"
                f"\t\t<Name>{name}</Name>\n"
                f"\t\t<StartKeyframePosition>{startPosition}</StartKeyframePosition>\n"
                f"\t\t<Keyframes>{pairs}</Keyframes>\n"
                f'\t\t<StartKeyframeValue Encoding="base64">{keys[0][1]}\n'
                f"\t\t</StartKeyframeValue>\n"
                f"\t</ArbVideoComponentParam>\n")
    if kind == "arb":
        stored = (f'\t\t<StartKeyframeValue Encoding="base64" BinaryHash="{blobHashes[value]}">{value}\n'
                  f"\t\t</StartKeyframeValue>\n") if value else ""
        return (f'\t<ArbVideoComponentParam ObjectID="{objectId}" ClassID="{cls}" Version="2">\n'
                f"\t\t<ParameterID>{pid}</ParameterID>\n"
                f"\t\t<IsTimeVarying>false</IsTimeVarying>\n"
                f"\t\t<ParameterControlType>{control}</ParameterControlType>\n"
                f"\t\t<Name>{name}</Name>\n"
                f"\t\t<StartKeyframePosition>{startPosition}</StartKeyframePosition>\n"
                f"{stored}"
                f"\t</ArbVideoComponentParam>\n")
    if value in values:
        raw = values[value]
        value = ("true" if raw else "false") if kind == "bool" else floatText(raw)
    lowUi, highUi = uiBounds.get(pid, ("", ""))
    lines = [f'\t<VideoComponentParam ObjectID="{objectId}" ClassID="{cls}" Version="9">']
    if lowUi:
        lines.append(f"\t\t<LowerUIBound>{lowUi}</LowerUIBound>")
    if highUi:
        lines.append(f"\t\t<UpperUIBound>{highUi}</UpperUIBound>")
    lines.append(f"\t\t<ParameterID>{pid}</ParameterID>")
    lines.append("\t\t<IsTimeVarying>false</IsTimeVarying>")
    if kind == "int":
        lines.append("\t\t<DiscontinuousInterpolate>true</DiscontinuousInterpolate>")
    lines.append(f"\t\t<ParameterControlType>{control}</ParameterControlType>")
    if name:
        lines.append(f"\t\t<Name>{name}</Name>")
    lines.append(f"\t\t<StartKeyframe>{startPosition},{value},0,0,0,0,0,0</StartKeyframe>")
    lines.append(f"\t\t<LowerBound>{bounds[0]}</LowerBound>")
    lines.append(f"\t\t<UpperBound>{bounds[1]}</UpperBound>")
    lines.append("\t</VideoComponentParam>\n")
    return "\n".join(lines)


def maskXml(maskId: int, paramIds: list, slot: int) -> str:
    params = "".join(f'\t\t\t\t<Param Index="{i}" ObjectRef="{pid}"/>\n' for i, pid in enumerate(paramIds))
    return (f'\t<VideoFilterComponent ObjectID="{maskId}" ClassID="{classIds["filter"]}" Version="9">\n'
            f'\t\t<Component Version="6">\n'
            f'\t\t\t<Params Version="1">\n{params}\t\t\t</Params>\n'
            f"\t\t\t<ID>{slot}</ID>\n"
            f"\t\t\t<Bypass>false</Bypass>\n"
            f"\t\t\t<DisplayName>Mask</DisplayName>\n"
            f"\t\t\t<Intrinsic>false</Intrinsic>\n"
            f"\t\t\t<InstanceName>{slot + 1}</InstanceName>\n"
            f"\t\t\t<ArchivedType>0</ArchivedType>\n"
            f"\t\t</Component>\n"
            f'\t\t<PremiereFilterPrivateData Encoding="base64" BinaryHash="{maskPrivateHash}">{maskPrivateData}\n'
            f"\t\t</PremiereFilterPrivateData>\n"
            f"\t\t<MatchName>AE.ADBE AEMask</MatchName>\n"
            f"\t\t<VideoFilterType>2</VideoFilterType>\n"
            f"\t</VideoFilterComponent>\n")


def keyframeText(keys: list) -> list:
    """[(seconds, vertices)] -> [(ticks, base64)] sorted, one per tick. premiere ignores a path with a single
    keyframe, so a lone key is repeated a frame later."""
    byTick = {}
    for seconds, vertices in keys:
        byTick[int(round(float(seconds) * ticksPerSecond))] = base64.b64encode(encodePath(vertices)).decode("ascii")
    if not byTick:
        raise ValueError("a mask needs at least one keyframe")
    if len(byTick) == 1:
        (tick, block), = byTick.items()
        byTick[tick + ticksPerSecond // 30] = block
    return sorted(byTick.items())


def addMask(text: str, componentId: str, keys: list, feather: float = 10.0, opacity: float = 100.0,
            expansion: float = 0.0, inverted: bool = False) -> str:
    """one AEMask subcomponent on a filter component, keys = [(media seconds, vertices)]. the rest of the
    text stays byte for byte, the new objects go at the end like premiere's own writer."""
    project = Project(text)
    comp = project.byId.get(str(componentId))
    if comp is None or comp.tag != "VideoFilterComponent":
        raise ValueError(f"no filter component {componentId}")
    slot = len(comp.findall("SubComponents/SubComponent"))
    first = project.nextObjectId()
    paramIds = [first + i for i in range(len(maskParams))]
    maskId = first + len(maskParams)
    values = {"feather": feather, "opacity": opacity, "expansion": expansion, "inverted": inverted}
    frames = keyframeText(keys)
    objects = "".join(paramXml(pid, spec, values, frames) for pid, spec in zip(paramIds, maskParams))
    objects += maskXml(maskId, paramIds, slot)

    pattern = re.compile(rf'<VideoFilterComponent ObjectID="{comp.get("ObjectID")}".*?</VideoFilterComponent>', re.S)
    found = pattern.search(text)
    block = found.group(0)
    line = f'\t\t\t<SubComponent Index="{slot}" ObjectRef="{maskId}"/>\n'
    if "<SubComponents" in block:
        block = block.replace("\t\t</SubComponents>", line + "\t\t</SubComponents>", 1)
    else:
        block = block.replace("\t\t</Component>\n", '\t\t</Component>\n\t\t<SubComponents Version="1">\n' + line + "\t\t</SubComponents>\n", 1)
    text = text[:found.start()] + block + text[found.end():]
    end = text.rindex("</PremiereData>")
    return text[:end] + objects + text[end:]


def replaceMaskKeys(text: str, maskId: str, keys: list) -> str:
    """keys = [(media seconds, vertices)] on an existing mask: only its Mask Path parameter changes, in place."""
    project = Project(text)
    mask = project.byId.get(str(maskId))
    if mask is None or mask.findtext("MatchName") != "AE.ADBE AEMask":
        raise ValueError(f"no mask {maskId}")
    pathId = None
    for ref in mask.findall("Component/Params/Param"):
        node = project.ref(ref)
        if node is not None and node.findtext("ParameterID") == "6":
            pathId = node.get("ObjectID")
    if pathId is None:
        raise ValueError(f"mask {maskId} has no Mask Path parameter")
    frames = keyframeText(keys)
    pairs = "".join(f"{ticks},{b64};" for ticks, b64 in frames)
    keyLine = f"\t\t<Keyframes>{pairs}</Keyframes>\n"
    startLine = f'\t\t<StartKeyframeValue Encoding="base64">{frames[0][1]}\n\t\t</StartKeyframeValue>\n'
    pattern = re.compile(rf'<ArbVideoComponentParam ObjectID="{pathId}".*?</ArbVideoComponentParam>', re.S)
    found = pattern.search(text)
    block = found.group(0)
    block = block.replace("<IsTimeVarying>false</IsTimeVarying>", "<IsTimeVarying>true</IsTimeVarying>", 1)
    oldStart = re.search(r"\t\t<StartKeyframeValue(?:[^>]*/>|[^>]*>.*?</StartKeyframeValue>)\n", block, re.S)
    if oldStart:
        block = block[:oldStart.start()] + block[oldStart.end():]
    if "<Keyframes>" in block:
        block = re.sub(r"\t\t<Keyframes>.*?</Keyframes>\n", lambda _m: keyLine + startLine, block, count=1, flags=re.S)
    else:
        block = block.replace("\t</ArbVideoComponentParam>", keyLine + startLine + "\t</ArbVideoComponentParam>", 1)
    text = text[:found.start()] + block + text[found.end():]
    return keepSharedBlob(text, oldStart.group(0)) if oldStart else text


def keepSharedBlob(text: str, removed: str) -> str:
    """premiere saves each blob once and other tags with the same BinaryHash are empty: when the tag carrying the
    data goes, the data moves to the first empty one, or importSequences brings back nothing."""
    carried = re.search(r'BinaryHash="([0-9a-f-]+)">(.*?)</', removed, re.S)
    if not carried or not carried.group(2).strip():
        return text
    digest, body = carried.group(1), carried.group(2).strip()
    if re.search(rf'BinaryHash="{digest}">\s*\S', text):
        return text
    return re.sub(rf'<(\w+)( Encoding="base64" BinaryHash="{digest}")/>',
                  lambda m: f"<{m.group(1)}{m.group(2)}>{body}\n\t\t</{m.group(1)}>", text, count=1)


def renameSequences(text: str, renames: dict) -> tuple:
    """new ObjectUID and name for each sequence, everywhere the file refers to them. returns (text, {old: new uid})."""
    project = Project(text)
    ids = {}
    for uid, newName in renames.items():
        seq = project.byUid.get(uid)
        if seq is None or seq.tag != "Sequence":
            raise ValueError(f"sequence not found: {uid}")
        oldName = seq.findtext("Name") or ""
        newUid = str(uuid.uuid4())
        # attributes and text alike (the open sequence list keeps the guid as text), never inside a longer id
        text = re.sub(rf'(?<=["\'>]){re.escape(uid)}(?=["\'<])', newUid, text)
        if oldName and oldName != newName:
            text = text.replace(f"<Name>{escape(oldName)}</Name>", f"<Name>{escape(newName)}</Name>")
        ids[uid] = newUid
    return text, ids


def readProject(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8")
    if "<PremiereData" not in text[:400]:
        raise ValueError(f"not a premiere project: {path}")
    return text


def writeProject(path: str, text: str) -> str:
    with open(path, "wb") as fh:
        fh.write(gzip.compress(text.encode("utf-8")))
    return path


def graftProjectFile(src: str, dst: str, grafts: list, renames: dict | None = None) -> dict:
    """grafts: [{sequence (uid or unique name), track (0-based), index | clip, effect, keys: [[media seconds,
    box or vertices]], shape?, feather?, opacity?, expansion?, inverted?}], or {mask: object id, keys} to replace
    the keys of a mask already there. renames: {sequence uid: new name}, each gets a fresh uid so premiere
    imports it beside the original. returns {path, masks, replaced, ids: {old: new}}."""
    text = readProject(src)
    project = Project(text)
    targets = []
    for g in grafts:
        if g.get("mask") is not None:
            targets.append((None, g))
            continue
        comp = project.findEffect(g["sequence"], int(g["track"]), index=g.get("index"), clip=g.get("clip"),
                                  effect=g.get("effect", "Gaussian Blur"))
        targets.append((comp.get("ObjectID"), g))
    replaced = 0
    for componentId, g in targets:
        shape = g.get("shape", "rect")
        keys = [(t, boxPath(v, shape) if isBox(v) else v) for t, v in g["keys"]]
        if componentId is None:
            text = replaceMaskKeys(text, str(g["mask"]), keys)
            replaced += 1
            continue
        text = addMask(text, componentId, keys, feather=float(g.get("feather", 10.0)), opacity=float(g.get("opacity", 100.0)),
                       expansion=float(g.get("expansion", 0.0)), inverted=bool(g.get("inverted", False)))
    text, ids = renameSequences(text, renames or {})
    writeProject(dst, text)
    return {"path": dst, "masks": len(targets), "replaced": replaced, "ids": ids}
