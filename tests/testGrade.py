"""matching shots: brightness and cast, the lumetri loop, and which pieces of the timeline are one shot."""

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))

import grade  # noqa: E402


def desk(seed: int = 1) -> np.ndarray:
    """a grey desk, a white box, a big blue pad and an orange cable: grey world alone would call it cold."""
    rng = np.random.default_rng(seed)
    img = np.empty((270, 480, 3))
    img[:] = (0.45, 0.45, 0.45)
    img[30:120, 40:200] = (0.85, 0.85, 0.85)
    img[100:260, 220:470] = (0.15, 0.3, 0.75)
    img[200:230, 20:200] = (0.85, 0.45, 0.15)
    return np.clip(img + rng.normal(0, 0.01, img.shape), 0, 1)


def lit(img: np.ndarray, gains=(1.0, 1.0, 1.0), level: float = 1.0) -> np.ndarray:
    return np.clip(img * np.array(gains) * level, 0, 1)


def lumetri(img: np.ndarray, values: dict) -> np.ndarray:
    """close to what premiere 25 measured, not equal: the loop must not depend on the exact slopes."""
    out = img * 2 ** (0.4 * values.get("Exposure", 0.0))
    out = np.where(out > 0.8, 0.8 + 0.2 * np.tanh((out - 0.8) / 0.2), out)
    t, g = values.get("Temperature", 0.0), values.get("Tint", 0.0)
    out = out * np.exp(np.array([0.0029 * t + 0.00144 * g, -0.00288 * g, -0.0029 * t + 0.00144 * g]))
    return np.clip(out, 0, 1)


def testCastIsReadOnTheGreyPixelsNotTheWholeFrame():
    neutral = grade.frameStats(desk())
    assert abs(neutral["warm"]) < 0.02 and abs(neutral["green"]) < 0.02
    band = grade.frameStats(desk(), np.ones(270 * 480, dtype=bool))
    assert band["warm"] < -0.1
    warm = grade.frameStats(lit(desk(), (1.1, 1.0, 0.85)))
    assert warm["warm"] == pytest.approx(math.log(1.1 / 0.85), abs=0.02)
    assert warm["green"] == pytest.approx(math.log(1.0 / math.sqrt(1.1 * 0.85)), abs=0.02)


def testTheSamePixelsAreMeasuredAfterACorrection():
    cast = lit(desk(), (1.1, 1.0, 0.85))
    pick = grade.greyPixels(cast)
    fixed = lit(cast, (1 / 1.1, 1.0, 1 / 0.85))
    assert abs(grade.frameStats(fixed, pick)["warm"]) < 0.02
    assert grade.frameStats(cast, pick)["warm"] > 0.2


def testADarkColdShotIsBroughtToTheReference():
    target = grade.frameStats(desk())
    shot = lit(desk(), (0.9, 1.0, 1.12), 0.6)
    start = {"Exposure": 0.0, "Temperature": 0.0, "Tint": 0.0}
    renders = []

    def render(values):
        renders.append(values)
        return [lumetri(shot, values or start)]

    done = grade.matchShot(render, start, target, passes=3)
    assert done["reached"], done
    assert done["values"]["Exposure"] > 0.5 and done["values"]["Temperature"] > 10
    assert math.log2(done["after"]["luma"] / target["luma"]) == pytest.approx(0, abs=0.04)
    assert done["after"]["warm"] == pytest.approx(target["warm"], abs=0.015)
    assert done["before"]["luma"] < 0.8 * target["luma"]
    assert renders[0] is None and len(renders) == done["passes"] + 1


def testAShotThatAlreadySitsIsLeftAlone():
    target = grade.frameStats(desk())
    start = {"Exposure": 0.3, "Temperature": -5.0, "Tint": 2.0}
    done = grade.matchShot(lambda values: [desk()], start, target, passes=3)
    assert done["reached"] and done["passes"] == 0 and done["values"] == start


def testTheLoopStopsAtTheSliderLimit():
    target = grade.frameStats(desk())
    shot = lit(desk(), level=0.03)
    done = grade.matchShot(lambda values: [lumetri(shot, values or {})], {"Exposure": 0.0, "Temperature": 0.0, "Tint": 0.0},
                           target, passes=4)
    assert not done["reached"] and done["values"]["Exposure"] == 4.0 and done["limited"] == ["Exposure"]


def testThroughEditsAreOneShot():
    clip = lambda i, s, e, a, b, m="rush.mp4": {"index": i, "start": s, "end": e, "inPoint": a, "outPoint": b, "mediaPath": m, "speed": 1}
    clips = [clip(0, 0, 3.317, 44.917, 48.233), clip(1, 3.317, 4.1, 48.233, 49.017), clip(2, 4.1, 29.083, 49.017, 74.0),
             clip(3, 29.083, 35.083, 129.0, 135.0), clip(4, 35.083, 36.0, 135.0, 135.917, "other.mp4"),
             clip(5, 37.0, 38.0, 135.917, 136.917, "other.mp4")]
    groups = grade.throughEdits(clips)
    assert [[c["index"] for c in g] for g in groups] == [[0, 1, 2], [3], [4], [5]]
    assert [grade.groupLabel(g) for g in groups] == ["V1 #0-#2", "V1 #3", "V1 #4", "V1 #5"]


def testTheSheetShowsTheReferenceThenEachShotBeforeAndAfter(tmp_path):
    from PIL import Image
    path = grade.sheet([("V1 #0 reference", desk(), None), ("V1 #3", lit(desk(), level=0.6), desk())], str(tmp_path / "grade.jpg"))
    img = Image.open(path)
    assert img.width == 2 * grade.thumbWidth + 3 * grade.sheetPad
    assert img.height == 2 * (grade.labelHeight + 270 * grade.thumbWidth // 480) + 3 * grade.sheetPad
