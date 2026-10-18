"""2d tracking against a synthetic shaky rush with known truth, and how it lands in a build."""

import sys
import xml.etree.ElementTree as et
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tracking  # noqa: E402
from fixtures import makeTrackingRush, screenAt, trackingTruth  # noqa: E402

W, H = 640, 360


@pytest.fixture(scope="module")
def rush(tmp_path_factory):
    return makeTrackingRush(str(tmp_path_factory.mktemp("track") / "shaky desk.mp4"))


def testScreenCornersFollowThePerspectiveWithinAPixel(rush):
    result = tracking.trackQuad(rush, 0.0, 2.0, (screenAt(0.0) / [W, H]).tolist())
    assert len(result["frames"]) == 60 and not result["lost"]
    worst = max(np.abs(np.array(f["quad"]) * [W, H] - screenAt(f["t"])).max() for f in result["frames"])
    assert worst < 1.0


def testObjectBoxFollowsAMovingThing(rush):
    b = trackingTruth(0)["box"]
    result = tracking.trackBox(rush, 0.0, 2.0, [b[0] / W, b[1] / H, b[2] / W, b[3] / H])
    assert result["method"] == "features"
    worst = max(np.abs(np.array(f["box"]) * [W, H, W, H] - trackingTruth(f["t"])["box"]).max() for f in result["frames"])
    assert worst < 2.0


def testCameraCorrectionCancelsTheShake(rush):
    cam = tracking.cameraPath(rush, 0.0, 2.0, smoothness=0.8)
    shake = np.array([trackingTruth(f["t"])["shake"][:2] for f in cam["frames"]])
    corr = np.array([[f["dx"] * W, f["dy"] * H] for f in cam["frames"]])
    raw = np.abs(np.diff(shake, axis=0)).mean()
    left = np.abs(np.diff(shake - shake[0] + corr, axis=0)).mean()
    assert left < raw / 3
    assert 1.0 < cam["zoom"] < 1.2


def testTrackSheetDrawsTheTrack(rush, tmp_path):
    result = tracking.trackQuad(rush, 0.0, 1.0, (screenAt(0.0) / [W, H]).tolist())
    path = tracking.trackSheet(rush, result["frames"], "quad", str(tmp_path / "sheet.jpg"))
    from PIL import Image
    img = np.asarray(Image.open(path))
    assert img.shape[1] == 4 * 360 and (img[..., 0] > 200).sum() > 500


def testBuildWithScreenAttachAndStabilize(rush, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    import pipeline
    from PIL import Image
    poster = tmp_path / "new screen.png"
    Image.new("RGB", (400, 240), (0, 200, 120)).save(poster)
    b = trackingTruth(0.5)["box"]
    plan = {"name": "tracked", "format": "vertical",
            "screens": [{"quad": (screenAt(0.0) / [W, H]).round(5).tolist(), "from": 0.0, "to": 2.0, "media": str(poster)}],
            "segments": [{"start": 0.5, "end": 1.5, "zoom": {"scale": 1.2, "x": 0.4, "y": 0.6},
                          "attach": [{"text": "switch", "box": [b[0] / W, b[1] / H, b[2] / W, b[3] / H], "anchor": "above"}]},
                         {"start": 1.5, "end": 2.0, "stabilize": {"smoothness": 0.7}}]}
    built = pipeline.buildEdit(rush, plan)
    assert built["nestNames"] == {"comp": "tracked (chop comp)"} and built["matteEffects"] == []
    assert built["layers"]["attached"] == 1
    root = et.parse(built["xmlPath"]).getroot()
    nests = {n.find("name").text: n for n in root.iter("sequence") if n.find("media") is not None}
    compTracks = nests["tracked (chop comp)"].findall("./media/video/track")
    screenClips = compTracks[1].findall("clipitem")
    # the screen shows over the two used stretches, a still starts at its first frame each time
    assert [(c.find("start").text, c.find("in").text) for c in screenClips] == [("15", "0"), ("45", "0")]
    assert screenClips[0].find("file/pathurl").text.endswith(".png")
    v1 = nests["tracked"].findall("./media/video/track")[0].findall("clipitem")
    assert all(c.find("sequence") is not None for c in v1)

    pins = [j for j in built["effectJobs"] if j["effects"][0]["effect"] == "Corner Pin"]
    assert [(j["sequence"], j["track"], j["index"]) for j in pins] == [("comp", 1, 0), ("comp", 1, 1)]
    params = {p["name"]: p for p in pins[0]["effects"][0]["params"]}
    assert list(params) == ["Upper Left", "Upper Right", "Lower Left", "Lower Right"]
    ul = params["Upper Left"]["keys"]
    # key times are the still's own time: 0 at the start of its piece (source 0.5 s)
    assert len(ul) == 31 and ul[0][0] == pytest.approx(0.0, abs=0.02)
    assert np.abs(np.array(ul[0][1]) * [W, H] - screenAt(0.5)[0]).max() < 1.0
    assert np.abs(np.array(params["Lower Right"]["keys"][-1][1]) * [W, H] - screenAt(1.5)[2]).max() < 1.0

    stab = [j for j in built["effectJobs"] if j["effects"][0]["effect"] == "Transform"]
    assert [(j["sequence"], j["track"], j["index"]) for j in stab] == [("main", 0, 1)]
    names = [p["name"] for p in stab[0]["effects"][0]["params"]]
    assert names == ["Position", "Uniform Scale", "Scale Height", "Scale Width", "Rotation"]
    assert len(stab[0]["effects"][0]["params"][0]["keys"]) == 15

    label = [m for m in built["motions"] if m.get("keys")]
    assert len(label) == 1 and len(label[0]["keys"]) == 30
    # label sits above the object, in sequence space, following it to the right
    keys = label[0]["keys"]
    assert keys[-1][2] > keys[0][2] and all(k[1] == 100.0 for k in keys)
    sx, sy, _ = pipeline.sourceToSequence({"srcW": W, "srcH": H, "seqW": 1080, "seqH": 1920}, built_seg(plan), 0.5,
                                          (b[0] + b[2]) / 2 / W, (b[1] + b[3]) / 2 / H)
    assert keys[0][3] < sy and abs(keys[0][2] - sx) < 0.01


def built_seg(plan):
    import edl
    return edl.normalizePlan({"segments": [plan["segments"][0]]}, {"duration": 2.0, "fps": 30})["segments"][0]


def testAttachNeedsSpeedOneAndScreensNeedMattes(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    import edl
    norm = edl.normalizePlan({"segments": [{"start": 0, "end": 1, "speed": 2, "attach": [{"text": "x", "box": [0, 0, 1, 1]}]}],
                              "screens": [{"quad": [[0, 0], [1, 0]], "media": "a.png"}]}, {"duration": 2.0, "fps": 30})
    assert norm["segments"][0]["attach"] == [] and norm["screens"] == []
    assert any("speed 1" in w for w in norm["warnings"]) and any("screen 0" in w for w in norm["warnings"])


def testOverlappingLayersGetTheirOwnTracksAndJobsFollowTimeOrder(rush, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    import pipeline
    from PIL import Image
    poster = tmp_path / "p.png"
    Image.new("RGB", (100, 60), (200, 0, 0)).save(poster)
    quad = (screenAt(0.0) / [W, H]).round(5).tolist()
    b = trackingTruth(0.0)["box"]
    box = [b[0] / W, b[1] / H, b[2] / W, b[3] / H]
    plan = {"name": "busy", "screens": [{"quad": quad, "from": 1.0, "to": 1.5, "media": str(poster)},
                                        {"quad": quad, "from": 0.0, "to": 0.5, "media": str(poster)}],
            "segments": [{"start": 0.0, "end": 1.6, "attach": [{"text": "one", "box": box}, {"text": "two", "box": box, "anchor": "below"}]}]}
    built = pipeline.buildEdit(rush, plan)
    labels = [m for m in built["motions"] if m.get("keys")]
    assert sorted((m["track"], m["index"]) for m in labels) == [(1, 0), (2, 0)]
    pins = sorted((j["track"], j["index"], round(j["effects"][0]["params"][0]["keys"][0][1][0], 3)) for j in built["effectJobs"])
    # premiere's clip #0 on the screen track is the earlier screen, whatever the plan order
    assert [(t, i) for t, i, _ in pins] == [(1, 0), (1, 1)]
    root = et.parse(built["xmlPath"]).getroot()
    comp = next(n for n in root.iter("sequence") if n.findtext("name") == "busy (chop comp)")
    starts = [int(c.find("start").text) for c in comp.findall("./media/video/track")[1].findall("clipitem")]
    assert starts == sorted(starts)


def testStabilizedShotCarriesItsLabelAndSubject(rush, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    import pipeline
    from testEditor import FakeSegmenter
    # a label on the screen, which is part of the shaking scene
    q = screenAt(0.2)
    box = [q[:, 0].min() / W + 0.02, q[:, 1].min() / H + 0.04, q[:, 0].max() / W - 0.02, q[:, 1].max() / H - 0.04]
    base = {"start": 0.2, "end": 1.2, "attach": [{"text": "screen", "box": box, "anchor": "center"}],
            "title": {"text": "HI", "behind": True, "subject": {"box": [0.3, 0.3, 0.6, 0.7], "track": False}, "from": 0.1, "to": 0.6}}
    plain = pipeline.buildEdit(rush, {"name": "plain", "segments": [base]}, segmenter=FakeSegmenter())
    steady = pipeline.buildEdit(rush, {"name": "steady", "segments": [dict(base, stabilize={"smoothness": 0.8})]}, segmenter=FakeSegmenter())
    jobs = [(j["sequence"], j["track"], j["index"]) for j in steady["effectJobs"]]
    assert ("main", 0, 0) in jobs and ("main", 2, 0) in jobs
    plainKeys = next(m for m in plain["motions"] if m.get("keys"))["keys"]
    steadyKeys = next(m for m in steady["motions"] if m.get("keys"))["keys"]
    # the label shakes with the scene, then sits still once the shake is out
    jitter = lambda keys: np.abs(np.diff(np.array([[k[2], k[3]] for k in keys]), n=2, axis=0)).mean()
    assert jitter(steadyKeys) < jitter(plainKeys)


def testVideoReplacementIsConformedAndLooped(tmp_path):
    import av
    import pipeline
    from fixtures import makeRush
    clip = makeRush(str(tmp_path / "capture.mp4"))  # 7 s at 30 fps
    out = pipeline.conformReplacement(clip, 160, 90, 9.0, tmp_path / "screens")
    assert not out["still"] and out["frames"] == 270
    with av.open(out["path"]) as c:
        s = c.streams.video[0]
        assert (s.width, s.height) == (160, 90) and sum(1 for _ in c.decode(s)) == 270
    assert pipeline.conformReplacement(clip, 160, 90, 9.0, tmp_path / "screens")["path"] == out["path"]


def testASlowBestMatteDropsToFastAfterItsFirstFrames(monkeypatch):
    """the fine model can cost seconds a frame on a busy gpu: after a few measured frames mobilesam goes on alone."""
    import time
    import masks
    clock = {"now": 0.0}
    monkeypatch.setattr(time, "perf_counter", lambda: clock["now"])

    class Sam:
        def mask(self, rgb, box, previous=None):
            clock["now"] += 0.05
            return np.ones(rgb.shape[:2], np.float32), None

    class Fine:
        calls = 0
        session = None

        def alpha(self, rgb, box):
            Fine.calls += 1
            clock["now"] += 2.0
            return np.ones(rgb.shape[:2], np.float32)

    seg = object.__new__(masks.Segmenter)
    seg.sam, seg.fine, seg.quality, seg.gpu = Sam(), Fine(), "best", True
    seg.frames, seg.seconds, seg.warm, seg.downgraded = 0, 0.0, False, None
    monkeypatch.setattr(masks, "gateAlpha", lambda fine, sam, box: fine)
    monkeypatch.setattr(masks, "activeProvider", lambda session: "cuda")
    rgb = np.zeros((18, 32, 3), np.uint8)
    for _ in range(20):
        seg.mask(rgb, (0, 0, 32, 18))
    assert Fine.calls == 1 + masks.slowMatteFrames
    assert seg.quality == "fast" and seg.fine is None
    described = seg.describe()
    assert described["downgraded"] == {"from": "best", "afterFrames": masks.slowMatteFrames, "msPerFrame": 2050}


def testAQuickBestMatteKeepsItsFineModel(monkeypatch):
    import time
    import masks
    clock = {"now": 0.0}
    monkeypatch.setattr(time, "perf_counter", lambda: clock["now"])

    class Sam:
        def mask(self, rgb, box, previous=None):
            clock["now"] += 0.05
            return np.ones(rgb.shape[:2], np.float32), None

    class Fine:
        session = None

        def alpha(self, rgb, box):
            clock["now"] += 0.1
            return np.ones(rgb.shape[:2], np.float32)

    seg = object.__new__(masks.Segmenter)
    seg.sam, seg.fine, seg.quality, seg.gpu = Sam(), Fine(), "best", True
    seg.frames, seg.seconds, seg.warm, seg.downgraded = 0, 0.0, False, None
    monkeypatch.setattr(masks, "gateAlpha", lambda fine, sam, box: fine)
    monkeypatch.setattr(masks, "activeProvider", lambda session: "cuda")
    for _ in range(20):
        seg.mask(np.zeros((18, 32, 3), np.uint8), (0, 0, 32, 18))
    assert seg.quality == "best" and seg.fine is not None and "downgraded" not in seg.describe()
