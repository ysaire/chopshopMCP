"""native premiere masks: the 2cin path block, and the aemask subcomponent grafted into a saved project."""

import base64
import gzip
import re
import struct
import sys
import xml.etree.ElementTree as et
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))

import maskGraft  # noqa: E402

extractPath = Path(__file__).resolve().parent / "data" / "projectExtract.xml"
compUid = "5e2ade44-f7d3-41fc-b287-df948f211e8f"   # chopshopMCP self-test (chop comp)
mainUid = "fc28b4af-ed01-413a-a157-d94ed5994e1c"   # chopshopMCP self-test
# a 4 point path drawn by hand in premiere, 144 bytes
handBlock = ("MmNpbgIAAAAAAAAABAAAAAEAAAC8u7s+QLIuP6kUvT7Bgy4/z2K6PsDgLj8BAAAAAAAAAM3Mfj/PlQc+zcx+P8+VBz7NzH4/z5UHPgEAAAAA"
            "AAAAmpl/P/VJfz+amX8/9Ul/P5qZfz/1SX8/AQAAAAAAAABVVdU+6ZN+P1VV1T7pk34/VVXVPumTfj8BAAAA")


@pytest.fixture(scope="module")
def extract():
    return extractPath.read_text(encoding="utf-8")


def testHandDrawnBlockRoundTrips():
    raw = base64.b64decode(handBlock)
    assert len(raw) == 144 and raw[:4] == b"2cin"
    vertices = maskGraft.decodePath(raw)
    assert len(vertices) == 4
    # second vertex sits near the top right corner with straight tangents (tangent = vertex)
    x, y, inX, inY, outX, outY = vertices[1]
    assert (round(x, 3), round(y, 3)) == (0.995, 0.132) and (inX, inY) == (x, y) == (outX, outY)
    assert maskGraft.encodePath(vertices) == raw


def testRectangleBlockHasTheDocumentedLayout():
    raw = maskGraft.encodePath(maskGraft.rectPath([0.05, 0.3, 0.35, 0.7]))
    assert len(raw) == 144
    assert struct.unpack("<4I", raw[4:20]) == (2, 0, 4, 1)
    # vertex, then 01000000, then 00000000 between vertices
    assert struct.unpack("<I", raw[44:48])[0] == 1 and struct.unpack("<I", raw[48:52])[0] == 0
    xs = [v[0] for v in maskGraft.decodePath(raw)]
    ys = [v[1] for v in maskGraft.decodePath(raw)]
    assert [round(v, 3) for v in xs] == [0.05, 0.35, 0.35, 0.05] and [round(v, 3) for v in ys] == [0.3, 0.3, 0.7, 0.7]
    assert len(maskGraft.encodePath(maskGraft.rectPath([0, 0, 1, 1]) + [(0.5, 0.5)])) == 176


def testEllipseIsFourBezierVertices():
    vertices = maskGraft.ellipsePath([0.2, 0.2, 0.6, 0.8])
    assert len(vertices) == 4
    top = vertices[0]
    assert (round(top[0], 3), round(top[1], 3)) == (0.4, 0.2)
    # handles leave the vertex horizontally, kappa of the half width
    assert round(top[2] - top[0], 4) == round(-0.5523 * 0.2, 4) and round(top[3], 3) == 0.2
    assert round(top[4] - top[0], 4) == round(0.5523 * 0.2, 4)


def testPathForShape():
    assert maskGraft.boxPath([0, 0, 1, 1], "rect")[1][:2] == (1.0, 0.0)
    assert maskGraft.boxPath([0, 0, 1, 1], "ellipse")[0][:2] == (0.5, 0.0)


def testProjectListsSequencesTracksClipsAndEffects(extract):
    project = maskGraft.Project(extract)
    names = {uid: name for uid, name in project.sequences()}
    assert names[compUid] == "chopshopMCP self-test (chop comp)" and names[mainUid] == "chopshopMCP self-test"
    tracks = project.videoTracks(project.sequence(compUid))
    assert len(tracks) == 3
    clips = project.clips(tracks[1])
    assert [project.clipName(c) for c in clips] == ["selftest rush"]
    assert [project.effectName(e) for e in project.effects(clips[0])] == ["Track Matte Key", "Gaussian Blur"]
    assert project.sequence("chopshopMCP self-test").get("ObjectUID") == mainUid
    assert project.nestsOf(project.sequence(mainUid)) == {compUid}


def testFindEffectByTrackIndexOrClipName(extract):
    project = maskGraft.Project(extract)
    blur = project.findEffect(compUid, track=1, index=0, effect="Gaussian Blur")
    assert blur.findtext("MatchName") == "AE.ADBE Gaussian Blur 2"
    assert project.findEffect(compUid, track=1, clip="selftest rush", effect="Gaussian Blur") is blur
    with pytest.raises(ValueError, match="no Mosaic on"):
        project.findEffect(compUid, track=1, index=0, effect="Mosaic")
    with pytest.raises(ValueError, match="no clip"):
        project.findEffect(compUid, track=1, index=3, effect="Gaussian Blur")
    with pytest.raises(ValueError, match="sequence not found"):
        project.findEffect("nope", track=0, index=0, effect="Gaussian Blur")


def maskOf(text: str, componentId: str):
    """the aemask subcomponents of a filter component in a patched project, as (component, params)."""
    root = et.fromstring(text)
    byId = {e.get("ObjectID"): e for e in root if e.get("ObjectID")}
    out = []
    for subRef in byId[componentId].findall("SubComponents/SubComponent"):
        mask = byId[subRef.get("ObjectRef")]
        params = [byId[p.get("ObjectRef")] for p in mask.findall("Component/Params/Param")]
        out.append((mask, params))
    return out


def testGraftAddsAFullMaskUnderTheEffect(extract):
    project = maskGraft.Project(extract)
    blur = project.findEffect(compUid, track=1, index=0, effect="Gaussian Blur")
    keys = [(0.5, maskGraft.rectPath([0.1, 0.1, 0.4, 0.4])), (1.0, maskGraft.rectPath([0.2, 0.1, 0.5, 0.4]))]
    patched = maskGraft.addMask(extract, blur.get("ObjectID"), keys, feather=30, expansion=4)
    masks = maskOf(patched, blur.get("ObjectID"))
    assert len(masks) == 1
    mask, params = masks[0]
    assert mask.findtext("MatchName") == "AE.ADBE AEMask" and mask.findtext("Component/DisplayName") == "Mask"
    assert mask.findtext("Component/InstanceName") == "1"
    realMask, real = maskOf(extract, "2586")[0]
    private = mask.find("PremiereFilterPrivateData")
    # premiere skips private data without a BinaryHash: same bytes and hash as its own mask
    assert private.text.strip() == realMask.find("PremiereFilterPrivateData").text.strip()
    assert private.get("BinaryHash") == realMask.find("PremiereFilterPrivateData").get("BinaryHash")
    # same 24 parameters as the mask premiere wrote itself (the v3 mosaic in the extract)
    shape = lambda p: (p.tag, p.get("ClassID"), p.findtext("ParameterID"), p.findtext("ParameterControlType"), p.findtext("Name"))
    assert [shape(p) for p in params] == [shape(p) for p in real]
    for mine, theirs in zip(params, real):
        stored, ref = mine.find("StartKeyframeValue"), theirs.find("StartKeyframeValue")
        if ref is not None and ref.get("BinaryHash"):
            assert (stored.text.strip(), stored.get("BinaryHash")) == (ref.text.strip(), ref.get("BinaryHash"))
    byName = {p.findtext("Name"): p for p in params}
    assert byName["Mask Feather"].findtext("StartKeyframe") == "-91445760000000000,30.,0,0,0,0,0,0"
    assert byName["Mask Expansion"].findtext("StartKeyframe") == "-91445760000000000,4.,0,0,0,0,0,0"
    assert byName["Mask Opacity"].findtext("StartKeyframe").split(",")[1] == "100."
    path = byName["Mask Path"]
    pairs = [p for p in path.findtext("Keyframes").split(";") if p]
    assert [int(p.split(",")[0]) for p in pairs] == [127008000000, 254016000000]
    first = base64.b64decode(pairs[0].split(",")[1])
    assert maskGraft.decodePath(first)[0][:2] == (pytest.approx(0.1), pytest.approx(0.1))
    assert path.findtext("StartKeyframeValue").strip() == pairs[0].split(",")[1]
    assert path.find("StartKeyframeValue").get("BinaryHash") is None
    ids = re.findall(r'ObjectID="(\d+)"', patched)
    assert len(ids) == len(set(ids)) and int(max(ids, key=int)) == 4611 + 25


def testALoneKeyIsRepeatedAFrameLater():
    # premiere ignores a mask path with a single keyframe
    pairs = maskGraft.keyframeText([(2.0, maskGraft.rectPath([0, 0, 0.5, 0.5]))])
    assert [t for t, _ in pairs] == [508032000000, 508032000000 + 8467200000] and pairs[0][1] == pairs[1][1]
    assert len(maskGraft.keyframeText([(0.0, [(0, 0), (1, 0), (1, 1)]), (1.0, [(0, 0), (1, 0), (1, 1)])])) == 2


def testSecondMaskOnTheSameEffectGetsTheNextSlot(extract):
    patched = maskGraft.addMask(extract, "2586", [(2.0, maskGraft.rectPath([0, 0, 0.5, 0.5]))], inverted=True)
    masks = maskOf(patched, "2586")
    assert [m.findtext("Component/InstanceName") for m, _ in masks] == ["1", "2"]
    root = et.fromstring(patched)
    assert root.find(".//VideoFilterComponent[@ObjectID='2586']/SubComponents/SubComponent[@Index='1']") is not None
    inverted = next(p for p in masks[1][1] if p.findtext("ParameterID") == "10")
    assert inverted.findtext("StartKeyframe") == "-91445760000000000,true,0,0,0,0,0,0"


def testUntouchedTextStaysByteIdentical(extract):
    patched = maskGraft.addMask(extract, "2586", [(0.0, maskGraft.rectPath([0, 0, 1, 1]))])
    marker = '<VideoFilterComponent ObjectID="2586"'
    assert patched.startswith(extract[:extract.index(marker)])
    assert patched.endswith("</PremiereData>\n")


def testRenameGivesNewUidsAndNamesEverywhere(extract):
    text, ids = maskGraft.renameSequences(extract, {mainUid: "hero", compUid: "hero (chop comp)"})
    assert set(ids) == {mainUid, compUid} and all(re.fullmatch(r"[0-9a-f-]{36}", v) for v in ids.values())
    assert mainUid not in text and compUid not in text
    root = et.fromstring(text)
    names = {s.get("ObjectUID"): s.findtext("Name") for s in root.iter("Sequence") if s.get("ObjectUID")}
    assert names == {ids[mainUid]: "hero", ids[compUid]: "hero (chop comp)"}
    # the nest clip in the main timeline is named after the nest
    assert "<Name>chopshopMCP self-test (chop comp)</Name>" not in text and "<Name>hero (chop comp)</Name>" in text
    assert text.count("<Name>hero (chop comp)</Name>") == extract.count("<Name>chopshopMCP self-test (chop comp)</Name>")


def testGraftProjectFileReadsAndWritesGzip(extract, tmp_path):
    src = tmp_path / "saved.prproj"
    with gzip.open(src, "wb") as fh:
        fh.write(extract.encode("utf-8"))
    dst = tmp_path / "grafted.prproj"
    report = maskGraft.graftProjectFile(str(src), str(dst), [
        {"sequence": compUid, "track": 1, "index": 0, "effect": "Gaussian Blur", "feather": 24,
         "keys": [[0.5, [[0.1, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]], [1.0, [[0.2, 0.1], [0.5, 0.1], [0.5, 0.4], [0.2, 0.4]]]]},
    ], renames={mainUid: "hero", compUid: "hero (chop comp)"})
    assert report["masks"] == 1 and report["path"] == str(dst)
    assert dst.read_bytes()[:2] == b"\x1f\x8b"
    with gzip.open(dst, "rb") as fh:
        text = fh.read().decode("utf-8")
    newComp = report["ids"][compUid]
    project = maskGraft.Project(text)
    blur = project.findEffect(newComp, track=1, index=0, effect="Gaussian Blur")
    assert len(maskOf(text, blur.get("ObjectID"))) == 1
    assert project.sequence(report["ids"][mainUid]).findtext("Name") == "hero"
    # a sequence can be named instead of its uid, as long as the name is unique
    report2 = maskGraft.graftProjectFile(str(src), str(tmp_path / "g2.prproj"), [
        {"sequence": "chopshopMCP self-test (chop comp)", "track": 1, "index": 0, "effect": "Gaussian Blur",
         "keys": [[0.0, [0, 0, 1, 1]]], "shape": "ellipse"}], renames={})
    assert report2["masks"] == 1 and report2["ids"] == {}


def testGraftRefusesAPlainXmlThatIsNotAProject(tmp_path):
    bad = tmp_path / "x.prproj"
    bad.write_text("<nope/>", encoding="utf-8")
    with pytest.raises(ValueError, match="not a premiere project"):
        maskGraft.graftProjectFile(str(bad), str(tmp_path / "y.prproj"), [], {})


def testProjectListsTheMasksOfAnEffectWithTheirDecodedKeys(extract):
    """the v3 mosaic carries a rectangle drawn by hand, keyed twice 1.3 s apart."""
    project = maskGraft.Project(extract)
    mosaic = project.byId["2586"]
    masks = project.masks(mosaic)
    assert len(masks) == 1
    mask = masks[0]
    assert mask["id"] == "3918" and mask["slot"] == 0 and mask["instance"] == "1"
    assert (mask["feather"], mask["opacity"], mask["expansion"], mask["inverted"]) == (10.0, 100.0, 0.0, False)
    assert [round(t, 4) for t, _v in mask["keys"]] == [164.4333, 165.7333]
    first, second = mask["keys"][0][1], mask["keys"][1][1]
    assert len(first) == 4 and len(second) == 4
    assert (round(first[0][0], 3), round(first[0][1], 3)) == (0.05, 0.3)
    assert (round(second[0][0], 3), round(second[0][1], 3)) == (0.65, 0.3)
    blur = project.findEffect(compUid, track=1, index=0, effect="Gaussian Blur")
    assert project.masks(blur) == []


def testClipMasksFindTheEffectsOfAClipThatCarryAMask(extract):
    """the extract has no masked clip in a sequence, so a clip here gives an empty list."""
    project = maskGraft.Project(extract)
    assert project.clipMasks(compUid, track=1, index=0) == []
    item = project.clipItem(compUid, track=1, index=0)
    assert project.clipName(item) == "selftest rush"
    with pytest.raises(ValueError, match="no clip"):
        project.clipItem(compUid, track=1, index=5)


def testAStaticMaskReadsItsPathFromTheStartValue(extract):
    """a mask never animated has no <Keyframes>, so its one key has no time."""
    static = re.sub(r"<Keyframes>41768697600000,.*?</Keyframes>\n", "", extract, count=1, flags=re.S)
    assert "<Keyframes>41768697600000" not in static
    mask = maskGraft.Project(static).masks(maskGraft.Project(static).byId["2586"])[0]
    assert [t for t, _v in mask["keys"]] == [None]
    assert (round(mask["keys"][0][1][0][0], 3), round(mask["keys"][0][1][0][1], 3)) == (0.05, 0.3)


def testKeyframeTextRoundTripsThroughParseKeyframes():
    """what addMask writes, the reader decodes: seconds within a tick, vertices within float32."""
    curved = [(0.5, 0.2, 0.4, 0.2, 0.6, 0.2), (0.8, 0.5, 0.8, 0.4, 0.8, 0.6), (0.5, 0.8), (0.2, 0.5, 0.2, 0.6, 0.2, 0.4),
              (0.3, 0.3), (0.35, 0.25), (0.45, 0.22)]
    keys = [(63.5, curved), (63.5 + 1 / 30, [(x + 0.01, y) for x, y, *_ in curved])]
    text = ";".join(f"{t},{b}" for t, b in maskGraft.keyframeText(keys)) + ";"
    back = maskGraft.parseKeyframes(text)
    assert [round(t, 6) for t, _v in back] == [63.5, round(63.5 + 1 / 30, 6)]
    assert len(back[0][1]) == 7
    for mine, theirs in zip(back[0][1], curved):
        assert mine == pytest.approx(maskGraft.fullVertex(theirs), abs=1e-6)
    assert back[1][1][2][:2] == pytest.approx((0.51, 0.8), abs=1e-6)


def testBezierPolylineFollowsTheTangents():
    """a path with curved tangents is sampled along its beziers; straight tangents give the vertices' edges."""
    ellipse = maskGraft.ellipsePath([0.2, 0.2, 0.6, 0.8])
    pts = maskGraft.bezierPolyline(ellipse, steps=8)
    assert len(pts) == 32 and pts[0] == pytest.approx((0.4, 0.2))
    # halfway along the first arc (top to right) the curve sits near 45 degrees on the ellipse
    cx, cy, rx, ry = 0.4, 0.5, 0.2, 0.3
    assert pts[4] == pytest.approx((cx + rx * 0.7071, cy - ry * 0.7071), abs=0.002)
    assert all(0.2 - 1e-9 <= x <= 0.6 + 1e-9 and 0.2 - 1e-9 <= y <= 0.8 + 1e-9 for x, y in pts)
    rect = maskGraft.bezierPolyline(maskGraft.rectPath([0, 0, 1, 0.5]), steps=2)
    assert [round(c, 9) for pt in rect for c in pt] == [0, 0, 0.5, 0, 1, 0, 1, 0.25, 1, 0.5, 0.5, 0.5, 0, 0.5, 0, 0.25]


def testReplaceMaskKeysRewritesThePathInPlace(extract):
    keys = [(164.5 + i / 30, maskGraft.rectPath([0.1 + i / 100, 0.1, 0.4 + i / 100, 0.4])) for i in range(3)]
    patched = maskGraft.replaceMaskKeys(extract, "3918", keys)
    mask, params = maskOf(patched, "2586")[0]
    assert mask.get("ObjectID") == "3918"
    path = next(p for p in params if p.findtext("Name") == "Mask Path")
    pairs = [p for p in path.findtext("Keyframes").split(";") if p]
    assert len(pairs) == 3
    assert [round(int(p.split(",")[0]) / maskGraft.ticksPerSecond, 4) for p in pairs] == [164.5, 164.5333, 164.5667]
    assert path.findtext("StartKeyframeValue").strip() == pairs[0].split(",")[1]
    assert maskGraft.decodePath(base64.b64decode(pairs[2].split(",")[1]))[0][:2] == (pytest.approx(0.12), pytest.approx(0.1))
    # nothing else moved: same objects, same ids, the rest of the file byte for byte
    assert re.findall(r'ObjectID="(\d+)"', patched) == re.findall(r'ObjectID="(\d+)"', extract)
    before = extract.index('<ArbVideoComponentParam ObjectID="4595"')
    assert patched[:before] == extract[:before]
    after = '<VideoComponentParam ObjectID="4596"'
    assert patched[patched.index(after):] == extract[extract.index(after):]
    read = maskGraft.Project(patched).masks(maskGraft.Project(patched).byId["2586"])[0]
    assert [round(t, 4) for t, _v in read["keys"]] == [164.5, 164.5333, 164.5667]


def testReplaceMaskKeysAnimatesAStaticMask(extract):
    static = re.sub(r"<Keyframes>41768697600000,.*?</Keyframes>\n", "", extract, count=1, flags=re.S)
    static = static.replace('<ParameterID>6</ParameterID>\n\t\t<ParameterControlType>22</ParameterControlType>\n\t\t<Name>Mask Path</Name>',
                            '<ParameterID>6</ParameterID>\n\t\t<IsTimeVarying>false</IsTimeVarying>\n\t\t<ParameterControlType>22</ParameterControlType>\n\t\t<Name>Mask Path</Name>', 1)
    patched = maskGraft.replaceMaskKeys(static, "3918", [(2.0, maskGraft.rectPath([0, 0, 0.5, 0.5]))])
    _mask, params = maskOf(patched, "2586")[0]
    path = next(p for p in params if p.findtext("Name") == "Mask Path")
    assert path.findtext("IsTimeVarying") == "true"
    pairs = [p for p in path.findtext("Keyframes").split(";") if p]
    assert len(pairs) == 2  # a lone key is repeated a frame later, as for a new mask
    with pytest.raises(ValueError, match="no mask 2586"):
        maskGraft.replaceMaskKeys(extract, "2586", [(2.0, maskGraft.rectPath([0, 0, 0.5, 0.5]))])


sharedDigest = "c7d38483-aa96-fa35-7b81-11a3000001bc"


def withTwin(extract: str, maskCarries: bool) -> tuple:
    """mask 3918's path and a twin sharing its blob by hash, as premiere saves one sketch on two clips."""
    start = extract.index('<ArbVideoComponentParam ObjectID="4595"')
    old = re.search(r'\t\t<StartKeyframeValue Encoding="base64">(.*?)</StartKeyframeValue>\n', extract[start:], re.S)
    body = old.group(1).strip()
    full = f'\t\t<StartKeyframeValue Encoding="base64" BinaryHash="{sharedDigest}">{body}\n\t\t</StartKeyframeValue>\n'
    empty = f'\t\t<StartKeyframeValue Encoding="base64" BinaryHash="{sharedDigest}"/>\n'
    text = extract[:start + old.start()] + (full if maskCarries else empty) + extract[start + old.end():]
    twin = ('\t<ArbVideoComponentParam ObjectID="99001" ClassID="313e54d4-6903-49ad-b0bf-8262cdd10f4e" Version="2">\n'
            '\t\t<ParameterID>6</ParameterID>\n\t\t<IsTimeVarying>false</IsTimeVarying>\n'
            '\t\t<ParameterControlType>22</ParameterControlType>\n\t\t<Name>Mask Path</Name>\n'
            '\t\t<StartKeyframePosition>-91445760000000000</StartKeyframePosition>\n'
            + (empty if maskCarries else full) + '\t</ArbVideoComponentParam>\n')
    anchor = '\t<VideoComponentParam ObjectID="4596"'
    return text.replace(anchor, twin + anchor, 1), body


def startValues(text: str, objectId: str) -> list:
    node = next(n for n in et.fromstring(text).iter("ArbVideoComponentParam") if n.get("ObjectID") == objectId)
    return [(v.get("BinaryHash"), (v.text or "").strip()) for v in node.findall("StartKeyframeValue")]


def testReplacingTheSketchKeepsTheBlobAnotherClipNamesByHash(extract):
    text, body = withTwin(extract, maskCarries=True)
    patched = maskGraft.replaceMaskKeys(text, "3918", [(164.5, maskGraft.rectPath([0.1, 0.1, 0.4, 0.4]))])
    assert startValues(patched, "99001") == [(sharedDigest, body)]
    assert len(re.findall(rf'BinaryHash="{sharedDigest}"', patched)) == 1


def testReplacingASketchThatOnlyNamesTheBlobLeavesOneStartValue(extract):
    text, body = withTwin(extract, maskCarries=False)
    keys = [(164.5, maskGraft.rectPath([0.1, 0.1, 0.4, 0.4]))]
    patched = maskGraft.replaceMaskKeys(text, "3918", keys)
    values = startValues(patched, "4595")
    assert len(values) == 1 and values[0][0] is None
    assert values[0][1] == maskGraft.keyframeText(keys)[0][1]
    assert startValues(patched, "99001") == [(sharedDigest, body)]


def testGraftProjectFileReplacesAMaskWhenAsked(extract, tmp_path):
    src = tmp_path / "saved.prproj"
    with gzip.open(src, "wb") as fh:
        fh.write(extract.encode("utf-8"))
    mainClip = maskGraft.Project(extract)
    # the mosaic 2586 sits on no clip of the extract: the graft takes the mask by its id, as the refiner does
    report = maskGraft.graftProjectFile(str(src), str(tmp_path / "g.prproj"), [
        {"mask": "3918", "keys": [[1.0, [[0.1, 0.1], [0.4, 0.1], [0.4, 0.4]]], [1.5, [[0.2, 0.1], [0.5, 0.1], [0.5, 0.4]]]]}],
        renames={compUid: "hero (chop comp)"})
    assert report["masks"] == 1 and report["replaced"] == 1
    with gzip.open(tmp_path / "g.prproj", "rb") as fh:
        text = fh.read().decode("utf-8")
    assert len(maskOf(text, "2586")) == 1
    project = maskGraft.Project(text)
    assert [round(t, 3) for t, _v in project.masks(project.byId["2586"])[0]["keys"]] == [1.0, 1.5]
    assert mainClip.sequence(compUid) is not None and report["ids"][compUid] in text
