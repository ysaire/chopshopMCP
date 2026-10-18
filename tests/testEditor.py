"""claude-as-editor stack: perception on voiceless footage, sheets, shot log, plans, speed and zoom."""

import os
import sys
import tempfile
import xml.etree.ElementTree as et
from pathlib import Path

import pytest

editorRoot = Path(tempfile.mkdtemp(prefix="chopshopMCPEditor"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import edl  # noqa: E402
import perceive  # noqa: E402
import pipeline  # noqa: E402
import sheets  # noqa: E402
import shotLog  # noqa: E402
import xmeml  # noqa: E402
from config import mediaCacheDir  # noqa: E402
from fixtures import makeVoicelessRush  # noqa: E402


@pytest.fixture(scope="module")
def voiceless():
    os.environ["APPDATA"] = str(editorRoot / "appdata")
    path = makeVoicelessRush(str(editorRoot / "desk asmr.mp4"))
    return {"path": path, "perception": pipeline.getPerception(path, {"voiceTrack": 1})}


def testBeatsFollowWhatHappensWithoutAnyVoice(voiceless):
    beats = voiceless["perception"]["beats"]
    states = [b["state"] for b in beats]
    assert states[0] == "idle" and beats[0]["end"] <= 4.5
    moving = [b for b in beats if 4.0 <= b["start"] < 8.0]
    assert moving and all(b["state"] == "active" for b in moving)
    clicks = [b for b in beats if b["start"] >= 7.9]
    assert clicks[0]["cut"] and clicks[0]["onsetsPerSec"] > 2
    assert voiceless["perception"]["stats"]["speechRatio"] < 0.05


def testOverviewFoldsIdleAndShowsTheLog(voiceless):
    cache = mediaCacheDir(voiceless["path"])
    shotLog.save(cache, [{"start": 8.0, "end": 12.0, "description": "sound test", "interest": 9, "tags": ["Payoff"]}])
    text = perceive.formatOverview(voiceless["perception"], shotLog.load(cache))
    assert "idle" in text and "logged 9/10: sound test" in text
    assert shotLog.load(cache)[0]["tags"] == ["payoff"]


def testShotLogMergesByRange(tmp_path):
    shotLog.save(tmp_path, [{"start": 1, "end": 2, "description": "a", "interest": 3}])
    shotLog.save(tmp_path, [{"start": 1, "end": 2, "description": "b", "interest": 12}, {"start": 0, "end": 1, "description": "c"}])
    entries = shotLog.load(tmp_path)
    assert [e["description"] for e in entries] == ["c", "b"]
    assert entries[1]["interest"] == 10


def testContactSheetGrid(voiceless):
    sheet = sheets.contactSheet(voiceless["perception"], mediaCacheDir(voiceless["path"]), 0, 12, every=1.0, cols=4)
    from PIL import Image
    img = Image.open(sheet["path"])
    cells = len(sheet["cells"])
    assert 10 <= cells <= 12
    assert img.size[0] == 4 * 320 and img.size[1] == ((cells + 3) // 4) * (180 + sheets.labelHeight)
    assert sheet["cells"][0]["beat"] == 0


def testPlanNormalization():
    info = {"duration": 10.0, "fps": 30.0}
    norm = edl.normalizePlan({"segments": [
        {"start": -1, "end": 2},
        {"start": 3, "end": 3.01},
        {"start": 4, "end": 8, "speed": 50, "audio": "keep"},
        {"start": 8, "end": 9, "zoom": {"scale": 9, "x": 2}},
    ]}, info)
    segs = norm["segments"]
    assert segs[0]["start"] == 0.0 and len(segs) == 3
    assert segs[1]["speed"] == edl.speedMax and segs[1]["audio"] == "mute"
    assert segs[2]["zoom"]["from"] == {"scale": edl.scaleMax, "x": 1.0, "y": 0.5}
    assert any("2 frames" in w for w in norm["warnings"]) and any("muted" in w for w in norm["warnings"])
    with pytest.raises(ValueError):
        edl.normalizePlan({"segments": []}, info)


def testMotionNeverShowsBlackEdges():
    view = edl.normalizeView({"scale": 2.0, "x": 1.0, "y": 0.5})
    m = edl.motionFor(view, 1920, 1080, 1920, 1080)
    assert m["scalePct"] == 200 and m["posX"] == 0.0 and m["posY"] == 0.5
    # landscape rush into a vertical sequence fills the height
    m = edl.motionFor(edl.normalizeView({"x": 0.8}), 1920, 1080, 1080, 1920)
    assert round(m["scalePct"], 1) == 177.8
    half = m["scalePct"] / 100 * 1920 / 1080 / 2
    assert 1 - half <= m["posX"] <= half and m["posX"] < 0.5


def testSpeedSegmentsInXml():
    info = {"duration": 10.0, "fps": 30.0, "hasVideo": True, "hasAudio": True, "width": 1920, "height": 1080,
            "audioStreams": [{"channels": 2, "sampleRate": 48000}]}
    segments = edl.normalizePlan({"segments": [{"start": 0, "end": 4}, {"start": 4, "end": 8, "speed": 4}]}, info)["segments"]
    root = et.fromstring(xmeml.buildXmeml("C:/r.mp4", segments, info, "plan").encode())
    video = root.findall("./sequence/media/video/track/clipitem")
    assert [int(v.find("end").text) - int(v.find("start").text) for v in video] == [120, 30]
    assert video[1].find("filter/effect/effectid").text == "timeremap"
    speedValue = [p for p in video[1].iter("parameter") if p.find("parameterid").text == "speed"][0].find("value").text
    assert float(speedValue) == 400
    # the timelapse is muted: one audio clip per lane, and no dangling links
    assert len(root.findall("./sequence/media/audio/track/clipitem")) == 2
    ids = {c.get("id") for c in root.iter("clipitem")}
    assert all(ln.find("linkclipref").text in ids for ln in root.iter("link"))
    assert len(video[1].findall("link")) == 1


def testBuildEditVerticalWithZoom(voiceless):
    plan = {"name": "desk | flash", "format": "vertical", "segments": [
        {"start": 8.0, "end": 10.0, "zoom": {"scale": 1.3, "x": 0.5, "y": 0.5}},
        {"start": 4.0, "end": 8.0, "speed": 2},
        {"start": 10.0, "end": 12.0, "zoom": {"from": {"scale": 1.0}, "to": {"scale": 1.2}}},
    ]}
    built = pipeline.buildEdit(voiceless["path"], plan)
    assert built["format"] == [1080, 1920]
    assert [round(t["output"][1] - t["output"][0], 2) for t in built["timeline"]] == [2.0, 2.0, 2.0]
    assert [m["index"] for m in built["motions"]] == [0, 1, 2]
    fill = max(1080 / 320, 1920 / 180)  # fixture is 320x180
    assert round(built["motions"][0]["from"]["scalePct"], 1) == round(100 * fill * 1.3, 1)
    assert built["motions"][2]["to"] is not None
    root = et.parse(built["xmlPath"]).getroot()
    assert root.find("./sequence/media/video/format/samplecharacteristics/height").text == "1920"


def testSrt():
    srt = edl.captionsToSrt([{"start": 61.5, "end": 63.25, "text": "first clack"}, {"start": 0, "end": 1, "text": " "}])
    assert srt == "1\n00:01:01,500 --> 00:01:03,250\nfirst clack\n"


class FakeSegmenter:
    """box in, box-shaped alpha out: keeps unit tests fast and offline."""

    def __init__(self, quality="auto"):
        self.quality = "fast"

    def mask(self, rgb, boxPx, previous=None):
        import numpy as np
        alpha = np.zeros(rgb.shape[:2], dtype=np.float32)
        x0, y0, x1, y1 = boxPx
        alpha[y0:y1, x0:x1] = 1.0
        return alpha, np.zeros((1, 1, 256, 256), dtype=np.float32)


def testZoneMathAndBlur():
    import masks
    import numpy as np
    zone = masks.normalizeZone({"keys": [{"t": 0, "box": [0.1, 0.1, 0.3, 0.3]}, {"t": 10, "box": [0.5, 0.1, 0.7, 0.3]}]}, 20)
    assert masks.boxAt(zone, 5.0)[0] == pytest.approx(0.3)
    assert masks.normalizeBox([0.9, 0.9, 0.2, 0.2]) == [0.2, 0.2, 0.9, 0.9]
    rng = np.random.default_rng(1)
    img = rng.integers(0, 255, (200, 400, 3), dtype=np.uint8)
    before = img.copy()
    masks.blurRegion(img, (100, 50, 300, 150), masks.normalizeZone({"box": [0, 0, 1, 1]}, 1))
    assert img[90:110, 190:210].std() < before[90:110, 190:210].std() / 3
    assert (img[:40] == before[:40]).all()


def testBlurredSegmentIsBakedAndAudioStaysOnTheSource(voiceless):
    plan = {"name": "private", "masks": "baked",
            "blur": [{"box": [0.0, 0.3, 0.5, 0.7], "from": 4.0, "to": 8.0, "label": "screen"}],
            "segments": [{"start": 1.0, "end": 3.0}, {"start": 5.0, "end": 7.0}]}
    built = pipeline.buildEdit(voiceless["path"], plan)
    assert built["layers"]["blurredSegments"] == 1
    root = et.parse(built["xmlPath"]).getroot()
    video = root.findall("./sequence/media/video/track")[0].findall("clipitem")
    assert video[0].find("file/pathurl") is not None and video[0].find("file/pathurl").text.endswith("asmr.mp4")
    baked = video[1].find("file/pathurl").text
    assert "baked_" in baked and video[1].find("in").text == "0"
    # sound for both segments still comes from the original rush
    assert all(a.find("file").get("id") == "file-1" for a in root.findall("./sequence/media/audio/track/clipitem"))
    import av
    bakedPath = next((Path(mediaCacheDir(voiceless["path"])) / "renders").glob("baked_*.mp4"))
    with av.open(str(bakedPath)) as c:
        assert sum(1 for _ in c.decode(video=0)) == 60


def testTitleBehindSubjectStacksThreeTracks(voiceless, monkeypatch):
    import masks
    monkeypatch.setattr(masks, "Segmenter", FakeSegmenter)
    plan = {"name": "hero", "format": "vertical", "masks": "baked", "segments": [
        {"start": 4.0, "end": 6.0, "zoom": {"scale": 1.2, "x": 0.5, "y": 0.5},
         "title": {"text": "KEYBOARD 75", "behind": True, "from": 0.5, "to": 1.5, "y": 0.4,
                   "subject": {"box": [0.2, 0.3, 0.6, 0.7], "track": False}}},
        {"start": 8.0, "end": 9.0, "speed": 2, "title": {"text": "fast", "behind": True, "subject": {"box": [0, 0, 1, 1]}}},
    ]}
    built = pipeline.buildEdit(voiceless["path"], plan)
    assert built["layers"] == {"titles": 2, "cutouts": 1, "blurredSegments": 0}
    assert any("speed 1" in w for w in built["warnings"])
    root = et.parse(built["xmlPath"]).getroot()
    tracks = root.findall("./sequence/media/video/track")
    assert len(tracks) == 3
    title = tracks[1].findall("clipitem")[0]
    cutout = tracks[2].findall("clipitem")[0]
    assert title.find("file/pathurl").text.endswith(".png")
    assert cutout.find("file/pathurl").text.endswith(".mov")
    # cutout sits exactly under the title, half a second into the segment, at 30 fps
    assert (title.find("start").text, title.find("end").text) == ("15", "45")
    assert (cutout.find("start").text, cutout.find("end").text) == ("15", "45")
    v3 = [m for m in built["motions"] if m["track"] == 2]
    v1 = [m for m in built["motions"] if m["track"] == 0 and m["index"] == 0]
    assert v3 and v3[0]["from"] == v1[0]["from"]
    import av
    cutPath = next((Path(mediaCacheDir(voiceless["path"])) / "renders").glob("cutout_*.mov"))
    with av.open(str(cutPath)) as c:
        frame = next(c.decode(video=0)).to_ndarray(format="rgba")
    assert frame[..., 3][100, 100] == 255 and frame[..., 3][10, 10] == 0


def testPreviewShowsTheFramedResult(voiceless, monkeypatch, tmp_path):
    import masks
    from PIL import Image
    monkeypatch.setattr(masks, "Segmenter", FakeSegmenter)
    title = masks.normalizeTitle({"text": "HI", "behind": True, "subject": {"box": [0.4, 0.2, 0.6, 0.8]}}, 3.0)
    path = masks.previewFrame(voiceless["path"], 5.0, [], title, str(tmp_path / "p.jpg"), seqSize=(1080, 1920))
    w, h = Image.open(path).size
    assert abs(w / h - 1080 / 1920) < 0.02


def testRealCutoutModel(voiceless):
    """opt-in: downloads mobilesam (45 mb) and runs it on the synthetic square."""
    if os.environ.get("CHOPSHOP_REAL_MODELS") != "1":
        pytest.skip("set CHOPSHOP_REAL_MODELS=1 to run the real segmentation model")
    import av
    import masks
    meta = masks.renderSegment(voiceless["path"], 5.0, 5.5, Path(mediaCacheDir(voiceless["path"])) / "real",
                               cutout={"box": [0.2, 0.28, 0.5, 0.72], "track": True}, cutoutFps=10, bake=False)
    with av.open(meta["cutout"]) as c:
        alpha = next(c.decode(video=0)).to_ndarray(format="rgba")[..., 3]
    assert alpha[70:110, 85:135].mean() > 200 and alpha[10:40].mean() < 20


def matteXml(built):
    root = et.parse(built["xmlPath"]).getroot()
    nests = {n.find("name").text: n for n in root.iter("sequence") if n.find("media") is not None}
    return root, nests


def tracksOf(seq):
    return seq.findall("./media/video/track")


def testZoneSpansMergeCloseIntervalsPerStyle():
    import pipeline as pl
    zones = [{"from": 1.0, "to": 3.0, "style": "blur"}, {"from": 3.5, "to": 4.0, "style": "blur"},
             {"from": 0.0, "to": 9.0, "style": "pixelate"}]
    segments = [{"start": 0.0, "end": 2.0}, {"start": 3.2, "end": 6.0}, {"start": 8.0, "end": 10.0}]
    assert pl.zoneSpans(zones, segments, "blur") == [[1.0, 2.0], [3.5, 4.0]]
    assert pl.zoneSpans(zones, segments, "blur", gap=2.0) == [[1.0, 4.0]]
    assert pl.zoneSpans(zones, segments, "pixelate") == [[0.0, 2.0], [3.2, 6.0], [8.0, 9.0]]


def testSpedNestClipCarriesInAndOutDividedByTheSpeed(voiceless):
    """premiere multiplies a nested clip's in and out by its speed on import: undivided, it plays past the nest (black)."""
    plan = {"name": "lapse", "masks": "matte",
            "blur": [{"box": [0.0, 0.0, 0.2, 0.5], "from": 0.0, "to": 12.0, "style": "pixelate"}],
            "segments": [{"start": 8.0, "end": 10.0, "speed": 2}]}
    built = pipeline.buildEdit(voiceless["path"], plan)
    _root, nests = matteXml(built)
    clip = tracksOf(nests["lapse"])[0].find("clipitem")
    assert clip.find("sequence") is not None
    assert (clip.find("start").text, clip.find("end").text) == ("0", "30")
    assert (clip.find("in").text, clip.find("out").text) == ("120", "150")
    speed = [p for p in clip.iter("parameter") if p.find("parameterid").text == "speed"][0]
    assert float(speed.find("value").text) == 200
    # the comp nest itself keeps the duplicate and the matte at source time
    comp = tracksOf(nests["lapse (chop comp)"])
    assert comp[1].find("clipitem/in").text == "240" and comp[2].find("clipitem/start").text == "240"


def testMatteModeKeysTheOriginalInsideNests(voiceless):
    """blur + mosaic + title behind the subject: only mattes rendered, premiere does the compositing."""
    plan = {"name": "hero", "format": "vertical", "masks": "matte",
            "blur": [{"box": [0.0, 0.3, 0.5, 0.7], "from": 4.0, "to": 8.0, "label": "screen"},
                     {"box": [0.6, 0.1, 0.9, 0.3], "from": 1.0, "to": 2.0, "style": "pixelate", "label": "plate"}],
            "segments": [{"start": 1.0, "end": 3.0},
                         {"start": 4.0, "end": 6.0, "zoom": {"scale": 1.2, "x": 0.5, "y": 0.5},
                          "title": {"text": "KEYBOARD 75", "behind": True, "from": 0.5, "to": 1.5,
                                    "subject": {"box": [0.2, 0.3, 0.6, 0.7], "track": False}}},
                         {"start": 9.0, "end": 10.0, "speed": 2}]}
    built = pipeline.buildEdit(voiceless["path"], plan, segmenter=FakeSegmenter())
    assert built["maskMode"] == "matte"
    assert built["layers"] == {"titles": 1, "cutouts": 1, "blurredSegments": 2, "mattes": 3}
    root, nests = matteXml(built)
    assert set(nests) == {"hero", "hero (chop comp)", "hero (chop subject)"}

    # main: v1 plays the comp nest where zones show, the plain source elsewhere, sound always from the source
    v1 = tracksOf(nests["hero"])[0].findall("clipitem")
    assert [c.find("sequence") is not None for c in v1] == [True, True, False]
    assert all(a.find("file").get("id") == "file-1" for a in root.findall("./sequence/media/audio/track/clipitem"))
    assert v1[0].find("in").text == "30" and v1[0].find("sequence").get("id") == v1[1].find("sequence").get("id")

    # comp nest at source size: original, blurred duplicate, its matte, mosaic duplicate, its matte
    # matte tracks stay on: the track matte key hides the matte itself and reads black from a track that is off
    comp = nests["hero (chop comp)"]
    assert comp.find("./media/video/format/samplecharacteristics/width").text == "320"
    tracks = tracksOf(comp)
    assert [t.find("enabled").text for t in tracks] == ["TRUE", "TRUE", "TRUE", "TRUE", "TRUE"]
    dup = tracks[1].find("clipitem")
    matte = tracks[2].find("clipitem")
    assert (dup.find("start").text, dup.find("in").text, dup.find("end").text) == ("120", "120", "180")
    assert dup.find("file").get("id") == "file-1" and dup.find("filter/effect/effectid").text == "Gaussian Blur"
    # same shape as premiere 25's own export of a gaussian blur
    effect = dup.find("filter/effect")
    assert [effect.find(k).text for k in ("name", "effectcategory", "effecttype", "mediatype", "pproBypass")] == [
        "Gaussian Blur", "Blur", "motion", "video", "false"]
    param = effect.find("parameter")
    assert param.get("authoringApp") == "PremierePro" and param.find("parameterid").text == "radius"
    assert param.find("valuemax").text == "100" and 0 < float(param.find("value").text) <= 100
    assert (matte.find("start").text, matte.find("in").text, matte.find("out").text) == ("120", "0", "60")
    assert matte.find("file/pathurl").text.endswith(".mov") and matte.find("alphatype").text == "straight"

    # subject nest: the comp (so the blur shows on the subject too) keyed by the subject matte
    subject = tracksOf(nests["hero (chop subject)"])
    assert subject[0].find("clipitem/sequence").get("id") == v1[0].find("sequence").get("id")
    assert subject[1].find("enabled").text == "TRUE"
    lane = tracksOf(nests["hero"])[2].find("clipitem")
    assert (lane.find("start").text, lane.find("end").text, lane.find("in").text) == ("75", "105", "135")

    # what the host must key: blur then matte on v2, mosaic then matte on v4, subject on v1
    comp, subj = built["matteEffects"]
    assert [(e["track"], e["effect"]) for e in comp["effects"]] == [
        (1, "Gaussian Blur"), (1, "Track Matte Key"), (3, "Mosaic"), (3, "Track Matte Key")]
    assert [e["params"][0].get("matteTrack") for e in comp["effects"] if e["effect"] == "Track Matte Key"] == [3, 5]
    assert subj["effects"] == [{"track": 0, "index": 0, "effect": "Track Matte Key",
                                "params": [{"name": "Matte", "index": 0, "matteTrack": 2}]}]
    # the subject layer moves exactly like v1
    v3 = [m for m in built["motions"] if m["track"] == 2]
    assert v3 and v3[0]["from"] == [m for m in built["motions"] if m["track"] == 0][1]["from"]

    ids = [c.get("id") for c in root.iter("clipitem")]
    assert len(ids) == len(set(ids)) and all(ln.find("linkclipref").text in ids for ln in root.iter("link"))


def testTrackZonesFollowTheMovingSquare(voiceless):
    import masks
    zone = masks.normalizeZone({"box": [20 / 320, 60 / 180, 80 / 320, 120 / 180], "from": 4.0, "to": 6.0, "track": True}, 12.0)
    tracked = masks.trackZones(voiceless["path"], 4.0, 6.0, [zone])
    assert tracked["fps"] == 30 and (tracked["width"], tracked["height"]) == (320, 180)
    assert len(tracked["frames"]) == 60 and tracked["frames"][0]["index"] == 120
    first, last = tracked["frames"][0]["boxes"][0][1], tracked["frames"][-1]["boxes"][0][1]
    assert first[0] == pytest.approx(20 / 320, abs=0.01)
    # by t = 6 the square's left edge is at 0.4375: the box followed it and never shrank below the plan
    assert last[0] <= 0.44 and last[2] >= 0.5
    assert last[2] - last[0] >= 60 / 320 - 1e-3 and last[3] - last[1] >= 60 / 180 - 1e-3
    assert tracked["lostTracks"] == []
    assert masks.followedBox((10, 10, 20, 16), (0, 0, 30, 30), 320, 180) == (0, -2, 30, 28)


def testOutlinePolygonHugsTheObject():
    """the hull grown by a margin, a fixed point count clockwise from the top, so premiere interpolates vertex by vertex."""
    import masks
    import numpy as np
    alpha = np.zeros((180, 320), dtype=np.float32)
    # a diagonal bar: a blade, with a hand-sized blob at one end
    for i in range(80):
        alpha[40 + i, 100 + i:104 + i] = 1.0
    alpha[110:140, 170:200] = 1.0
    poly = masks.outlinePolygon(alpha, (90, 30, 210, 150), 320, 180, margin=0.0)
    assert len(poly) == masks.outlinePoints and all(len(p) == 2 for p in poly)
    xs, ys = [p[0] * 320 for p in poly], [p[1] * 180 for p in poly]
    assert min(ys) == pytest.approx(40, abs=2) and max(ys) == pytest.approx(140, abs=2)
    assert min(xs) == pytest.approx(100, abs=2) and max(xs) == pytest.approx(200, abs=2)
    assert ys[0] == min(ys)
    assert masks.signedArea(poly) > 0
    grown = masks.outlinePolygon(alpha, (90, 30, 210, 150), 320, 180, margin=0.1)
    assert min(p[0] for p in grown) * 320 < 100 - 8 and max(p[1] for p in grown) * 180 > 140 + 8
    assert masks.outlinePolygon(np.zeros((180, 320), dtype=np.float32), (0, 0, 50, 50), 320, 180) is None
    # a box becomes the same kind of polygon, so a failed frame keeps the vertex count
    rect = masks.boxPolygon([0.1, 0.2, 0.5, 0.8])
    assert len(rect) == masks.outlinePoints and rect[0] == [0.1, 0.2] and masks.signedArea(rect) > 0
    assert len(masks.offScreenPolygon(masks.outlinePoints)) == masks.outlinePoints


def testOutlinePromptPicksTheMaskThatContinuesThePrevious():
    """overlap with the previous mask wins, a candidate filling its box loses, otherwise confidence decides."""
    import masks
    import numpy as np
    knife = np.zeros((180, 320), dtype=np.float32)
    knife[60:120, 100:110] = 1.0
    moved = np.roll(knife, 4, axis=1)
    pad = np.zeros((180, 320), dtype=np.float32)
    pad[40:140, 80:200] = 1.0
    box = (80, 40, 200, 140)
    previous = knife > 0.5
    assert masks.pickCandidate([(pad, box, 0.99), (moved, box, 0.5)], previous) == 1
    assert masks.pickCandidate([(moved, box, 0.5), (pad, box, 0.99)], None) == 0
    assert masks.pickCandidate([(moved, box, 0.4), (knife, box, 0.6)], None) == 1
    assert masks.binaryBox(previous, 0.0, 320, 180) == (100, 60, 110, 120)
    assert masks.binaryBox(np.zeros((180, 320), dtype=bool), 0.1, 320, 180) is None
    assert masks.maskIou(previous, moved) == pytest.approx(6 / 14)


def testTrackZonesOutlineFollowsTheSegmentedShape(voiceless):
    import masks
    zone = masks.normalizeZone({"box": [20 / 320, 60 / 180, 80 / 320, 120 / 180], "from": 4.0, "to": 5.0, "track": True}, 12.0)
    assert zone["shape"] == "outline"  # tracked zones hug the object unless told otherwise
    assert masks.normalizeZone({"box": [0, 0, 1, 1], "track": True, "shape": "rect"}, 1)["shape"] == "rect"
    assert masks.normalizeZone({"box": [0, 0, 1, 1]}, 1)["shape"] == "rect"
    tracked = masks.trackZones(voiceless["path"], 4.0, 5.0, [zone], segmenter=FakeSegmenter())
    assert len(tracked["frames"]) == 30
    first = dict((zi, shape) for zi, shape in tracked["frames"][0]["shapes"])[0]
    assert len(first) == masks.outlinePoints
    # the fake segmenter fills the box: the polygon is the box grown by the margin
    xs, ys = [p[0] for p in first], [p[1] for p in first]
    assert min(xs) < 20 / 320 <= min(xs) + 0.03 and max(xs) > 80 / 320 and min(ys) < 60 / 180 and max(ys) > 120 / 180
    last = dict((zi, shape) for zi, shape in tracked["frames"][-1]["shapes"])[0]
    assert min(p[0] for p in last) > min(xs) + 0.1  # it moved right with the square


class FakeVideoBackend:
    """segments by brightness inside the crop it is given: the geometry around it is what the tests check."""

    def __init__(self):
        self.calls = []
        self.opened = 0

    def open(self):
        self.opened += 1
        return {}

    def step(self, session, crop, box=None, point=None):
        import numpy as np
        self.calls.append((crop.shape[:2], box, point))
        return (crop[..., 0] > 128).astype(np.float32)


def brightSquare(x: int, y: int = 150, size: int = 60, width: int = 640, height: int = 360):
    import numpy as np
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[y:y + size, x:x + size] = 240
    return rgb


def testCropWindowZoomsOnTheObjectInsideTheFrame():
    """a window twice the object's size, at least 512 px, never past the frame."""
    import masks
    assert masks.cropWindow((800, 400, 1000, 700), 1920, 1080) == (600, 250, 1200, 850)
    assert masks.cropWindow((1800, 900, 1900, 1050), 1920, 1080) == (1408, 568, 1920, 1080)
    assert masks.cropWindow((20, 60, 80, 120), 320, 180) == (0, 0, 320, 180)
    # the window stays put, moves when the object nears an edge, zooms in when the object got much smaller
    window = (600, 250, 1200, 850)
    assert masks.nextWindow(window, (830, 420, 1030, 720), 1920, 1080) == window
    assert masks.nextWindow(window, (1000, 420, 1180, 720), 1920, 1080) == masks.cropWindow((1000, 420, 1180, 720), 1920, 1080)
    wide = (300, 0, 1300, 1000)
    assert masks.nextWindow(wide, (780, 450, 880, 550), 1920, 1080) == masks.cropWindow((780, 450, 880, 550), 1920, 1080)


def testVideoSegmenterPromptsOnceThenPropagates():
    """sam 2 style: the box prompts the first frame only, in crop coordinates; the memory follows after that."""
    import masks
    backend = FakeVideoBackend()
    seg = masks.VideoSegmenter(backend)
    state = None
    for i in range(25):
        alpha, state = seg.outline(brightSquare(300 + 10 * i), (300, 150, 360, 210), state)
        assert alpha.shape == (360, 640)
        assert alpha[180, 330 + 10 * i] > 0.5 and alpha[180, 250 + 10 * i] == 0 and alpha[100, 330 + 10 * i] == 0
        assert state["box"] == (300 + 10 * i, 150, 360 + 10 * i, 210)
    # one session, the box translated into the first crop, no box afterwards
    assert backend.opened == 1
    (shape, box, point), rest = backend.calls[0], backend.calls[1:]
    assert shape == (360, 512) and box == (226, 150, 286, 210) and point is None
    assert all(b is None and p is None for _s, b, p in rest)
    # the window moved with the object: the last crop still holds it whole
    assert state["window"][0] > 74 and state["window"][2] >= 600


def testVideoSegmenterRepromptsFromTheTrackerWhenTheObjectIsLost():
    """two empty masks in a row restart the session from the tracker's box on the next frame."""
    import masks
    backend = FakeVideoBackend()
    seg = masks.VideoSegmenter(backend, patience=2)
    alpha, state = seg.outline(brightSquare(300), (300, 150, 360, 210), None)
    dark = brightSquare(300) * 0
    for _ in range(2):
        alpha, state = seg.outline(dark, (300, 150, 360, 210), state)
        assert alpha.max() == 0
    assert backend.opened == 1 and state["box"] == (300, 150, 360, 210)
    alpha, state = seg.outline(brightSquare(100), (100, 150, 160, 210), state)
    assert backend.opened == 2 and backend.calls[-1][1] is not None
    assert alpha[180, 130] > 0.5 and state["box"] == (100, 150, 160, 210)


def testOutlineSegmenterFallsBackToMobileSam(monkeypatch):
    """sam 2 when torch and transformers load, mobilesam alone otherwise."""
    import masks

    class Stub:
        def __init__(self, quality="fast"):
            self.quality = quality

    monkeypatch.setattr(masks, "Segmenter", Stub)
    monkeypatch.setattr(masks, "Sam2Backend", FakeVideoBackend)
    seg = masks.outlineSegmenter()
    assert isinstance(seg, masks.VideoSegmenter) and seg.describe()["quality"] == "video"

    def broken():
        raise ImportError("no torch")

    monkeypatch.setattr(masks, "Sam2Backend", broken)
    assert isinstance(masks.outlineSegmenter(), Stub) and masks.outlineSegmenter().quality == "fast"


def testOutlineZonesUseTheVideoSegmenterByDefault(voiceless, monkeypatch):
    """outline zones default to masks.outlineSegmenter; a caller's factory is asked for the outline quality."""
    import masks
    fake = FakeSegmenter()
    monkeypatch.setattr(masks, "outlineSegmenter", lambda quality="fast": fake)
    assert pipeline.zoneSegmenter({"masks": masks, "onProgress": lambda *a: None}) is fake
    asked = []
    ctx = {"masks": masks, "onProgress": lambda *a: None, "segmenter": lambda quality: asked.append(quality) or fake}
    assert pipeline.zoneSegmenter(ctx) is fake and asked == ["outline"]
    zone = masks.normalizeZone({"box": [20 / 320, 60 / 180, 80 / 320, 120 / 180], "from": 4.0, "to": 4.1, "track": True}, 12.0)
    assert masks.trackZones(voiceless["path"], 4.0, 4.1, [zone])["frames"][0]["shapes"]


def testRealVideoSegmenter(voiceless):
    """opt-in: sam 2 (hiera tiny) follows the synthetic square through its memory, no tracker."""
    if os.environ.get("CHOPSHOP_REAL_MODELS") != "1":
        pytest.skip("set CHOPSHOP_REAL_MODELS=1 to run the real segmentation model")
    import masks
    seg = masks.VideoSegmenter(masks.Sam2Backend())
    state = None
    for _i, _t, rgb, _w, _h, _rate in masks.iterFrames(voiceless["path"], 4.0, 4.5, True):
        alpha, state = seg.outline(rgb, (20, 60, 80, 120), state)
    assert alpha[90, 78] > 0.5 and alpha[90, 28] < 0.5 and seg.describe()["quality"] == "video"


def testNativeMasksSitOnTheMainClipsWithoutANest(voiceless):
    """the clip carries the effect itself, its mask parked off screen on the frames outside the zone."""
    plan = {"name": "hero", "masks": "native",
            "blur": [{"box": [20 / 320, 60 / 180, 80 / 320, 120 / 180], "from": 4.0, "to": 6.0, "track": True, "shape": "rect", "label": "knife"},
                     {"box": [0.6, 0.1, 0.9, 0.3], "from": 1.0, "to": 2.0, "style": "pixelate", "shape": "ellipse", "label": "plate"}],
            "segments": [{"start": 1.0, "end": 3.0}, {"start": 4.0, "end": 6.0}]}
    built = pipeline.buildEdit(voiceless["path"], plan)
    assert built["maskMode"] == "native"
    assert built["sequenceName"] == "hero (chop stage)" and built["finalName"] == "hero"
    assert built["layers"] == {"titles": 0, "cutouts": 0, "blurredSegments": 2, "mattes": 0, "masks": 2}
    root, nests = matteXml(built)
    assert set(nests) == {"hero (chop stage)"}
    v1 = tracksOf(nests["hero (chop stage)"])[0].findall("clipitem")
    assert [c.find("sequence") is not None for c in v1] == [False, False]
    assert v1[0].find("filter") is None and v1[1].find("filter/effect/effectid").text == "Gaussian Blur"
    assert not [p for p in root.iter("pathurl") if p.text.endswith(".mov")]
    assert built["matteEffects"] == []
    mosaic, = [j for j in built["effectJobs"] if j["effects"][0]["effect"] == "Mosaic"]
    assert (mosaic["sequence"], mosaic["track"], mosaic["index"]) == ("main", 0, 0)
    plate, knife = built["maskGrafts"]
    assert (knife["sequence"], knife["track"], knife["index"], knife["effect"], knife["shape"]) == ("main", 0, 1, "Gaussian Blur", "rect")
    assert knife["feather"] == 5 and knife["label"] == "knife"
    assert len(knife["keys"]) == 60 and knife["keys"][0][0] == pytest.approx(4.0) and knife["keys"][-1][0] == pytest.approx(6.0 - 1 / 30)
    assert knife["keys"][-1][1][0] > 0.35
    assert (plate["sequence"], plate["track"], plate["index"], plate["effect"], plate["shape"]) == ("main", 0, 0, "Mosaic", "ellipse")
    box = [0.6, 0.1, 0.9, 0.3]
    assert plate["keys"] == [[1.0, box], [round(2.0 - 1 / 30, 5), box], [2.0, pipeline.offScreenBox], [round(3.0 - 1 / 30, 5), pipeline.offScreenBox]]
    assert knife["expansion"] == 2
    # a plan without zones in the edit needs no stage
    plain = pipeline.buildEdit(voiceless["path"], {"name": "plain", "masks": "native", "segments": [{"start": 9.0, "end": 10.0}]})
    assert plain["sequenceName"] == "plain" and plain["maskGrafts"] == []


def testNativeMaskKeysStayInSourceSecondsOnASpedUpClip(voiceless):
    """premiere multiplies any clip's in and out by its speed on import, so the writer divides them."""
    plan = {"name": "fast", "masks": "native",
            "blur": [{"box": [0.1, 0.1, 0.5, 0.5], "from": 7.0, "to": 10.0, "label": "screen"}],
            "segments": [{"start": 8.0, "end": 10.0, "speed": 2.0}]}
    built = pipeline.buildEdit(voiceless["path"], plan)
    screen, = built["maskGrafts"]
    assert [k[0] for k in screen["keys"]] == [8.0, round(10.0 - 1 / 30, 5)]
    _root, nests = matteXml(built)
    clip = tracksOf(nests["fast (chop stage)"])[0].find("clipitem")
    assert [clip.find(k).text for k in ("start", "end", "in", "out")] == ["0", "30", "120", "150"]
    assert clip.find("pproTicksIn").text == str(120 * 254016000000 // 30)


def testNativeMasksReachTheSubjectNest(voiceless):
    """the subject nest's fill carries the same effect and mask, so the blur shows on the subject too."""
    plan = {"name": "hero", "masks": "native",
            "blur": [{"box": [0.0, 0.3, 0.5, 0.7], "from": 4.0, "to": 8.0, "label": "screen"}],
            "segments": [{"start": 4.0, "end": 6.0,
                          "title": {"text": "KEYBOARD 75", "behind": True, "from": 0.5, "to": 1.5,
                                    "subject": {"box": [0.2, 0.3, 0.6, 0.7], "track": False}}}]}
    built = pipeline.buildEdit(voiceless["path"], plan, segmenter=FakeSegmenter())
    _root, nests = matteXml(built)
    assert set(nests) == {"hero (chop stage)", "hero (chop stage) (chop subject)"}
    fill = tracksOf(nests["hero (chop stage) (chop subject)"])[0].find("clipitem")
    assert fill.find("sequence") is None and fill.find("filter/effect/effectid").text == "Gaussian Blur"
    main, subject = built["maskGrafts"]
    assert (main["sequence"], main["track"], main["index"]) == ("main", 0, 0)
    assert (subject["sequence"], subject["track"], subject["index"]) == ("subject", 0, 0)
    assert [k[0] for k in subject["keys"]] == [4.5, round(5.5 - 1 / 30, 5)]
    assert built["layers"]["masks"] == 2 and built["layers"]["cutouts"] == 1


def testNativeZonesOfOneStretchShareTheClipAndParkOutsideTheirTime(voiceless):
    plan = {"name": "two", "masks": "native",
            "blur": [{"box": [0.1, 0.1, 0.5, 0.5], "from": 4.0, "to": 6.0, "label": "face"},
                     {"box": [0.6, 0.6, 0.9, 0.9], "from": 4.5, "to": 5.0, "style": "pixelate", "label": "plate"}],
            "segments": [{"start": 4.0, "end": 6.0}]}
    built = pipeline.buildEdit(voiceless["path"], plan)
    _root, nests = matteXml(built)
    v1 = tracksOf(nests["two (chop stage)"])[0].findall("clipitem")
    assert len(v1) == 1 and v1[0].find("filter/effect/effectid").text == "Gaussian Blur"
    assert built["matteEffects"] == []
    assert [(j["sequence"], j["track"], j["index"], [e["effect"] for e in j["effects"]]) for j in built["effectJobs"]] == [("main", 0, 0, ["Gaussian Blur", "Mosaic"])]
    face, plate = built["maskGrafts"]
    assert [k[0] for k in face["keys"]] == [4.0, round(6.0 - 1 / 30, 5)]
    assert [k[0] for k in plate["keys"]] == [4.0, round(4.5 - 1 / 30, 5), 4.5, round(5.0 - 1 / 30, 5), 5.0, round(6.0 - 1 / 30, 5)]
    assert [k[1] for k in plate["keys"]][::2] == [pipeline.offScreenBox, [0.6, 0.6, 0.9, 0.9], pipeline.offScreenBox]
    assert pipeline.mergeSpans([[1.0, 2.0], [2.5, 3.0], [5.0, 6.0]]) == [[1.0, 3.0], [5.0, 6.0]]


def testNativeOutlineZoneWritesPolygonKeysOfOneSize(voiceless):
    """one polygon per frame, parked off screen with the same vertex count."""
    import masks
    plan = {"name": "edge", "masks": "native",
            "blur": [{"box": [20 / 320, 60 / 180, 80 / 320, 120 / 180], "from": 4.5, "to": 5.0, "track": True,
                      "style": "pixelate", "label": "knife"},
                     {"box": [0.6, 0.1, 0.9, 0.3], "from": 4.0, "to": 6.0, "label": "screen"}],
            "segments": [{"start": 4.0, "end": 6.0}]}
    built = pipeline.buildEdit(voiceless["path"], plan, segmenter=FakeSegmenter())
    knife, screen = built["maskGrafts"]
    assert knife["shape"] == "outline" and screen["shape"] == "rect"
    counts = {len(k[1]) for k in knife["keys"]}
    assert counts == {masks.outlinePoints}
    # parked before 4.5 and after 5.0, polygons in between, every frame
    times = [k[0] for k in knife["keys"]]
    assert times[:2] == [4.0, round(4.5 - 1 / 30, 5)] and times[2] == 4.5 and times[-2:] == [5.0, round(6.0 - 1 / 30, 5)]
    assert len(times) == 4 + 15
    assert knife["keys"][0][1] == masks.offScreenPolygon(masks.outlinePoints)
    assert min(p[0] for p in knife["keys"][2][1]) < 20 / 320


def testMatteFileLinesUpWithTheSource(voiceless, tmp_path):
    import av
    import masks
    zone = masks.normalizeZone({"box": [0.0, 0.3, 0.5, 0.7], "from": 4.0, "to": 8.0}, 12)
    meta = masks.renderMatte(voiceless["path"], 4.0, 6.0, tmp_path, zones=[zone])
    assert meta["frames"] == 60 and (meta["width"], meta["height"]) == (320, 180)
    with av.open(meta["path"]) as c:
        assert c.streams.video[0].codec_context.name == "qtrle"
        frames = [f.to_ndarray(format="rgba") for f in c.decode(video=0)]
    assert len(frames) == 60
    alpha = frames[0][..., 3]
    assert alpha[90, 80] == 255 and alpha[90, 300] == 0 and alpha[10, 80] == 0
    assert (frames[0][..., :3] == 255).all()
    # cached on the second call
    assert masks.renderMatte(voiceless["path"], 4.0, 6.0, tmp_path, zones=[zone])["path"] == meta["path"]


def testSubjectMatteFollowsTheSegmenter(voiceless, tmp_path):
    import av
    import masks
    meta = masks.renderMatte(voiceless["path"], 5.0, 5.5, tmp_path, subject={"box": [0.25, 0.25, 0.5, 0.75], "track": False},
                             segmenter=FakeSegmenter())
    with av.open(meta["path"]) as c:
        alpha = next(c.decode(video=0)).to_ndarray(format="rgba")[..., 3]
    assert alpha[90, 120] > 200 and alpha[10, 10] == 0 and alpha[90, 250] == 0


def testGateKeepsFineEdgesOnTheObjectOnly():
    import masks
    import numpy as np
    sam = np.zeros((200, 300), np.float32)
    sam[50:150, 50:150] = 1.0
    fine = np.zeros_like(sam)
    fine[52:148, 52:148] = 0.9          # soft edge on the object
    fine[90:110, 90:110] = 0.0          # hole birefnet left in a flat area
    fine[60:80, 220:280] = 1.0          # background object birefnet also picked
    out = masks.gateAlpha(fine, sam, (50, 50, 150, 150))
    assert out[60:80, 220:280].max() == 0.0
    assert out[100, 100] == 1.0
    assert abs(out[52, 100] - 0.9) < 0.05


def testFineModelCropsTheBoxAndPastesBack():
    import masks
    import numpy as np
    seen = {}

    class Session:
        def run(self, outputs, feeds):
            seen["x"] = feeds["input_image"]
            return [np.full((1, 1, 1024, 1024), 8.0, np.float32)]

    fine = masks.FineModel.__new__(masks.FineModel)
    fine.session, fine.inputName = Session(), "input_image"
    rgb = np.full((400, 600, 3), 128, np.uint8)
    alpha = fine.alpha(rgb, (100, 100, 300, 300))
    assert seen["x"].shape == (1, 3, 1024, 1024) and abs(float(seen["x"][0, 0, 0, 0]) - (0.502 - 0.485) / 0.229) < 0.01
    # box plus 15% margin, nothing outside
    assert alpha[70, 70] > 0.99 and alpha[69, 69] == 0 and alpha[330, 330] == 0 and alpha[329, 329] > 0.99


def testSegmenterQualityFollowsTheGpu(monkeypatch):
    import masks

    class Stub:
        def __init__(self, providers, gpu=True):
            self.providers = providers
            self.encoder = self.session = self

        def get_providers(self):
            return self.providers

    monkeypatch.setattr(masks, "SamModel", Stub)
    monkeypatch.setattr(masks, "FineModel", Stub)
    monkeypatch.setattr(masks, "onnxProviders", lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])
    seg = masks.Segmenter("auto")
    assert seg.quality == "best" and seg.describe() == {"quality": "best", "provider": "directml", "gpu": True}
    monkeypatch.setattr(masks, "onnxProviders", lambda: ["CPUExecutionProvider"])
    assert masks.Segmenter("auto").quality == "fast" and masks.Segmenter("auto").fine is None
    assert masks.Segmenter("best").quality == "best"


def testRealSubjectMatte(voiceless, tmp_path):
    """opt-in: real mobilesam (fast quality) through the matte renderer."""
    if os.environ.get("CHOPSHOP_REAL_MODELS") != "1":
        pytest.skip("set CHOPSHOP_REAL_MODELS=1 to run the real segmentation model")
    import av
    import masks
    meta = masks.renderMatte(voiceless["path"], 5.0, 5.3, tmp_path, subject={"box": [0.2, 0.28, 0.5, 0.72], "track": True},
                             segmenter=masks.Segmenter("fast"))
    with av.open(meta["path"]) as c:
        alpha = next(c.decode(video=0)).to_ndarray(format="rgba")[..., 3]
    assert alpha[70:110, 85:135].mean() > 200 and alpha[10:40].mean() < 20


def testStoryboardShowsEverySegmentFramed(voiceless, tmp_path):
    import storyboard
    from PIL import Image
    info = pipeline.getProbe(voiceless["path"])
    plan = {"format": "vertical", "segments": [{"start": 8.0, "end": 10.0, "note": "hook"},
                                                {"start": 4.0, "end": 8.0, "speed": 4},
                                                {"start": 1.0, "end": 2.0, "zoom": {"scale": 1.5}, "title": {"text": "GO"}}]}
    board = storyboard.storyboard(voiceless["path"], plan, info, tmp_path)
    assert board["segments"] == 3 and board["format"] == [1080, 1920] and board["duration"] == pytest.approx(4.0, abs=0.05)
    img = Image.open(board["path"])
    assert img.size == (3 * 240, 240 * 1920 // 1080 + 34)


def testTitlePngIsReusedWhenPremiereHoldsIt(tmp_path):
    """keyed by content: premiere keeps a referenced png locked and windows refuses a rewrite."""
    import stat
    import masks
    title = masks.normalizeTitle({"text": "KEYBOARD"}, 3.0)
    ctx = {"masks": masks, "seqW": 320, "seqH": 180, "renderDir": tmp_path / "renders"}
    first = pipeline.titlePng(ctx, title)
    os.chmod(first, stat.S_IREAD)
    try:
        assert pipeline.titlePng(ctx, title) == first
    finally:
        os.chmod(first, stat.S_IREAD | stat.S_IWRITE)


def testNativeMasksAreTheDefaultMode(voiceless):
    plan = {"name": "quiet", "blur": [{"box": [0.6, 0.1, 0.9, 0.3], "from": 1.0, "to": 2.0, "label": "plate"}],
            "segments": [{"start": 1.0, "end": 3.0}]}
    built = pipeline.buildEdit(voiceless["path"], plan)
    assert built["maskMode"] == "native" and built["sequenceName"] == "quiet (chop stage)"
    assert pipeline.buildEdit(voiceless["path"], dict(plan, masks="nonsense"))["maskMode"] == "native"


def sketchOf(x0: int, y0: int, x1: int, y1: int, width: int = 640, height: int = 360):
    import numpy as np
    binary = np.zeros((height, width), dtype=bool)
    binary[y0:y1, x0:x1] = True
    return binary


def testVideoSegmenterPromptsWithTheSketchsBoxAndInnerPointAndKeepsTheResultInsideTheSketch():
    """sam 2 tracks a mask prompt as the thing itself, and a loose box alone picks the background."""
    import masks
    backend = FakeVideoBackend()
    seg = masks.VideoSegmenter(backend)
    loose = sketchOf(280, 130, 380, 230)
    first = brightSquare(300)
    first[150:210, 480:540] = 240  # a bright thing outside the sketch
    alpha, state = seg.outline(first, (0, 0, 640, 360), None, mask=loose)
    assert backend.opened == 1
    (shape, box, point), = backend.calls
    # the window is cut around the sketch (x from 74); box and point in the window's own pixels
    assert shape == (360, 512) and box == (206, 130, 306, 230)
    assert abs(point[0] - 256) <= 1 and abs(point[1] - 180) <= 1
    assert alpha[180, 330] > 0.5 and alpha[180, 510] == 0 and state["box"] == (300, 150, 360, 210)
    for i in range(1, 4):
        alpha, state = seg.outline(brightSquare(300 + 10 * i), (0, 0, 640, 360), state)
    assert all(b is None and p is None for _s, b, p in backend.calls[1:])
    # a second sketch later on: same session, its box and point prompt that frame
    alpha, state = seg.outline(brightSquare(340), (0, 0, 640, 360), state, mask=sketchOf(320, 130, 420, 230))
    assert backend.opened == 1 and backend.calls[-1][1] is not None and backend.calls[-1][2] is not None
    assert state["box"] == (340, 150, 400, 210)


def testSketchGateGrowsTheSketchByATenth():
    import masks
    gate = masks.sketchGate(sketchOf(280, 130, 380, 230))
    assert gate[180, 330] and gate[180, 385] and not gate[180, 400] and not gate[100, 330]


def testSketchPointIsTheDeepestPointInsideTheSketch():
    """on a sketch drawn around a thing, the point farthest from its edges is on the thing."""
    import masks
    x, y = masks.sketchPoint(sketchOf(280, 130, 380, 230))
    assert abs(x - 330) <= 1 and abs(y - 180) <= 1
    # a blade sketched with a fat handle: the point sits in the handle, never on the thin blade's edge
    knife = sketchOf(100, 170, 400, 190) | sketchOf(380, 150, 460, 210)
    x, y = masks.sketchPoint(knife)
    assert 380 <= x < 460 and 150 <= y < 210


def testMobileSamOutlineTakesASketchAsAMaskPrompt():
    """without sam 2, the sketch's box prompts mobilesam together with the sketch as the low-res mask."""
    import masks
    import numpy as np

    class FakeSam:
        def __init__(self):
            self.prompts = []

        def embed(self, rgb):
            return "emb", 1.0

        def decode(self, embedding, scale, H, W, boxPx, previous=None, point=None):
            # answers the box with 20 px to spare, like a model that bleeds onto the background
            self.prompts.append((boxPx, previous, point))
            alpha = np.zeros((H, W), dtype=np.float32)
            x0, y0, x1, y1 = boxPx
            alpha[y0 - 20:y1 + 20, x0 - 20:x1 + 20] = 1.0
            return alpha, np.zeros((1, 1, 256, 256), np.float32), 0.9

    seg = object.__new__(masks.Segmenter)
    seg.sam = FakeSam()
    low = masks.lowResLogits(sketchOf(280, 130, 380, 230))
    assert low.shape == (1, 1, 256, 256) and low[0, 0, 128, 128] > 0 and low[0, 0, 10, 10] < 0
    alpha, state = seg.outline(brightSquare(300), (0, 0, 640, 360), None, mask=sketchOf(280, 130, 380, 230))
    boxes = [b for b, _p, _pt in seg.sam.prompts]
    assert (280, 130, 380, 230) in boxes and (0, 0, 640, 360) not in boxes
    assert any(p is not None and p[0, 0, 128, 128] > 0 for _b, p, _pt in seg.sam.prompts)
    assert all(abs(pt[0] - 330) <= 1 and abs(pt[1] - 180) <= 1 for _b, _p, pt in seg.sam.prompts)
    assert alpha[180, 330] > 0.5 and state["box"] == (280, 130, 380, 230)
    # the fake answers the whole box: what lies outside the grown sketch is dropped
    wide = seg.outline(brightSquare(300), (0, 0, 640, 360), None, mask=sketchOf(300, 150, 360, 210))[0]
    assert wide[180, 330] > 0.5 and wide[180, 372] == 0
