"""effect presets: reading premiere's .prfpset graph and replaying it on clips."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import presets  # noqa: E402
from fixtures import makePresetFile  # noqa: E402


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    path = makePresetFile(str(tmp_path_factory.mktemp("presets") / "flash test.prfpset"))
    return presets.loadPack(path)


def testPackIsReadWithFoldersAnchorsAndLimits(library):
    assert [p["id"] for p in library] == ["flash test/Pop In", "flash test/Smooth Zoom Out", "flash test/Red Shadow",
                                          "flash test/Graded", "flash test/Voice Air"]
    pop, zoom, shadow, graded, voice = library
    assert pop["anchor"] == "in" and zoom["anchor"] == "out" and pop["duration"] == pytest.approx(0.117, abs=0.002)
    assert shadow["effects"][0]["params"][0] == {"index": 0, "name": "Shadow Color", "kind": "color", "value": [255, 255, 0, 0]}
    assert pop["effects"][0]["params"][2]["kind"] == "bool" and pop["effects"][0]["params"][1]["value"] == [0.5, 0.5]
    assert not pop["issues"] and graded["issues"] and voice["audio"] and voice["issues"]
    assert "Transform (Scale Height, Scale Width)" in presets.summary(pop)


def testFindByIdNameOrUniqueFragment(library):
    assert presets.find(library, "Pop In")["name"] == "Pop In"
    assert presets.find(library, "flash test/red shadow")["name"] == "Red Shadow"
    assert presets.find(library, "zoom")["name"] == "Smooth Zoom Out"
    with pytest.raises(ValueError, match="several"):
        presets.find(library, "o")
    with pytest.raises(ValueError, match="no preset"):
        presets.find(library, "teleport")


def testEaseFollowsSpeedAndInfluence():
    a = {"t": 0.0, "v": 100.0, "interp": "bezier", "outSpeed": 0.0, "outInfluence": 1 / 3}
    b = {"t": 1.0, "v": 200.0, "interp": "bezier", "inSpeed": 0.0, "inInfluence": 1 / 3}
    keys = [a, b]
    assert presets.valueAt(keys, 0.5) == pytest.approx(150.0, abs=0.01)
    assert presets.valueAt(keys, 0.25) < 125.0 < presets.valueAt(keys, 0.75) - 50
    assert not presets.segmentIsLinear(a, b)
    # speeds equal to the slope: a straight line
    c = dict(a, outSpeed=100.0)
    d = dict(b, inSpeed=100.0)
    assert presets.segmentIsLinear(c, d) and presets.valueAt([c, d], 0.3) == pytest.approx(130.0, abs=0.01)
    # a fast exit overshoots like after effects does
    e = dict(a, outSpeed=900.0, outInfluence=0.5)
    assert max(presets.valueAt([e, b], t / 20) for t in range(21)) > 200.0


def testLinearKeysStayFewEasedOnesGetAKeyPerFrame(library):
    pop, zoom = library[0], library[1]
    plan = presets.replayPlan(pop, clipIn=10.0, clipOut=14.0, fps=30)
    height = next(p for p in plan[0]["params"] if p["name"] == "Scale Height")
    assert [round(k[0], 3) for k in height["keys"]] == [10.0, 10.067, 10.117]
    assert [k[1] for k in height["keys"]] == [75.0, 110.0, 100.0]
    assert next(p for p in plan[0]["params"] if p["name"] == "Opacity") == {"name": "Opacity", "index": 5, "kind": "float", "value": 100.0}

    plan = presets.replayPlan(zoom, clipIn=10.0, clipOut=14.0, fps=30)
    keys = next(p for p in plan[0]["params"] if p["name"] == "Scale Height")["keys"]
    # anchored on the out point, one key per frame over the half second
    assert keys[0] == [13.5, 130.0] and keys[-1] == [14.0, 100.0] and 14 <= len(keys) <= 17
    values = [k[1] for k in keys]
    assert all(x >= y for x, y in zip(values, values[1:]))


def testScaleAnchorStretchesAndPresetsAreCutAtTheClipEdges(library):
    zoom = dict(library[1])
    zoom["effects"] = [dict(zoom["effects"][0], anchor="scale")]
    keys = next(p for p in presets.replayPlan(zoom, 0.0, 8.0, 30)[0]["params"] if p["name"] == "Scale Height")["keys"]
    # preset spans 4 s, the clip 8 s: the half second ramp becomes one second at the end
    assert keys[0][0] == pytest.approx(7.0) and keys[-1][0] == pytest.approx(8.0)
    # a preset longer than the clip is cut at the clip's edge with the value it has there
    pop = library[0]
    short = next(p for p in presets.replayPlan(pop, clipIn=0.0, clipOut=0.05, fps=30)[0]["params"] if p["name"] == "Scale Height")
    assert [k[0] for k in short["keys"]] == [0.0, 0.05] and short["keys"][1][1] == pytest.approx(75 + 35 * 0.05 / (2 / 30), abs=0.5)
    out = next(p for p in presets.replayPlan(library[1], clipIn=0.0, clipOut=0.25, fps=30)[0]["params"] if p["name"] == "Scale Height")
    assert out["keys"][0][0] == 0.0 and 100 < out["keys"][0][1] < 130 and out["keys"][-1] == [0.25, 100.0]


def testSamplingFollowsSequenceFramesAtAnySpeed(library):
    zoom = library[1]
    count = lambda speed: len(next(p for p in presets.replayPlan(zoom, 0.0, 4.0, 30, speed)[0]["params"]
                                   if p["name"] == "Scale Height")["keys"])
    # half speed: the half second ramp plays over a full second of the timeline, twice the frames
    assert count(0.5) > 1.7 * count(1.0) and count(2.0) < 0.7 * count(1.0)


def testClipPlacementIsKeptUnlessThePresetAnimatesIt(library):
    pop = dict(library[0])
    pop["effects"] = [dict(pop["effects"][0], intrinsic=True, displayName="Motion")]
    plan = presets.replayPlan(pop, 0.0, 2.0)
    assert [p["name"] for p in plan[0]["params"]] == ["Scale Height", "Scale Width"]
    assert len(presets.replayPlan(pop, 0.0, 2.0, keepPlacement=False)[0]["params"]) == 6


def testOpaqueParamsAreSkippedAndReported(library):
    plan = presets.replayPlan(library[3], 0.0, 2.0)
    assert plan[0]["skipped"] == ["Hue vs Sat"] and plan[0]["params"] == [{"name": "Exposure", "index": 0, "kind": "float", "value": 0.5}]


def testLibraryDedupesAndCaches(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))  # the real profile's presets stay out
    first = makePresetFile(str(tmp_path / "a.prfpset"))
    folder = tmp_path / "packs"
    folder.mkdir()
    makePresetFile(str(folder / "b.prfpset"))
    files = presets.libraryFiles([first, str(folder)])
    assert len(files) >= 2
    lib = presets.loadLibrary(files, tmp_path / "cache")
    assert len(lib) == 5
    assert list((tmp_path / "cache").glob("presets_*.json"))
