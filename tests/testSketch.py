"""refining a mask sketched by hand: the range it covers, the sketch as a bitmap, the keys that replace it."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))

import maskGraft  # noqa: E402
import masks  # noqa: E402
import sketch  # noqa: E402

rect = maskGraft.rectPath([0.2, 0.2, 0.8, 0.8])
clip = {"start": 10.0, "end": 70.0, "inPoint": 5.0, "outPoint": 65.0, "speed": 1}


def testSourceRangeMapsTheMarksThroughTheClip():
    """marks are sequence seconds; premiere reports a sped clip's in and out points divided by its speed."""
    assert sketch.sourceRange(clip, 12.0, 14.5, [], 30) == (7.0, 9.5)
    fast = dict(clip, end=40.0, outPoint=35.0, speed=2)
    assert sketch.sourceRange(fast, 12.0, 14.5, [], 30) == (14.0, 19.0)
    assert sketch.sourceSpan(fast) == (10.0, 70.0)
    # marks past the clip stop at it
    assert sketch.sourceRange(clip, 8.0, 80.0, [], 30) == (5.0, 65.0)
    with pytest.raises(ValueError, match="outside"):
        sketch.sourceRange(clip, 80.0, 90.0, [], 30)


def testSourceRangeFallsBackToTheSketchKeys():
    """without marks, the sketch's first to last key plus a frame; a single key needs the marks."""
    keys = [(63.5, rect), (64.5, rect)]
    a, b = sketch.sourceRange(clip, None, None, keys, 30)
    assert a == 63.5 and b == pytest.approx(64.5 + 1 / 30)
    assert sketch.sourceRange(clip, None, None, [(4.0, rect), (66.0, rect)], 30) == (5.0, 65.0)
    with pytest.raises(ValueError, match="In and Out"):
        sketch.sourceRange(clip, None, None, [(None, rect)], 30)
    with pytest.raises(ValueError, match="In and Out"):
        sketch.sourceRange(clip, None, None, [(63.5, rect)], 30)


def testRasterizeFillsTheSketch():
    binary = sketch.rasterize(maskGraft.rectPath([0.25, 0.25, 0.75, 0.75]), 400, 200)
    assert binary.shape == (200, 400) and binary.dtype == bool
    assert binary[100, 200] and not binary[20, 20] and not binary[100, 310]
    ellipse = sketch.rasterize(maskGraft.ellipsePath([0.25, 0.25, 0.75, 0.75]), 400, 200)
    # the box's corner stays outside the ellipse, whose area is pi/4 of the box
    assert ellipse[100, 200] and not ellipse[54, 104]
    assert 0.74 < ellipse.sum() / binary.sum() < 0.83


def testSketchPromptsStartAtTheRangeAndFollowTheKeysInsideIt():
    """each later key inside the range re-prompts on its own frame; a sketch never animated prompts once."""
    keys = [(2.0, "a"), (2.5, "b"), (3.0, "c"), (4.0, "d")]
    assert sketch.sketchPrompts(keys, 2.2, 3.5) == [(2.2, "a"), (2.5, "b"), (3.0, "c")]
    assert sketch.sketchPrompts(keys, 1.0, 2.2) == [(1.0, "a"), (2.0, "a")]
    assert sketch.sketchPrompts([(None, "s")], 2.2, 3.5) == [(2.2, "s")]


def testRefinedKeysKeepTheSketchOutsideTheRangeAndParkWhereNothingIsDrawn():
    poly = masks.boxPolygon([0.3, 0.3, 0.6, 0.6])
    polys = [(2.7 + i / 30, poly) for i in range(3)]
    sketched = [(2.5, rect), (2.75, rect), (3.4, rect)]
    keys = sketch.refinedKeys(sketched, polys, 2.7, 2.8, 2.3, 3.5, 30)
    assert [round(t, 4) for t, _v in keys] == [2.5, 2.7, 2.7333, 2.7667, 3.4]
    assert all(len(v) == len(poly) for _t, v in keys)
    # the kept sketch keys are the user's shape, resampled to the polygons' vertex count
    xs = [x for x, _y in keys[0][1]]
    assert min(xs) == pytest.approx(0.2) and max(xs) == pytest.approx(0.8)
    # a sketch never animated is replaced wholesale, and the mask is parked on the clip's other frames
    keys = sketch.refinedKeys([(None, rect)], polys, 2.7, 2.8, 2.3, 3.5, 30)
    assert [round(t, 4) for t, _v in keys] == [2.3, 2.6667, 2.7, 2.7333, 2.7667, 2.8, 3.4667]
    assert all(x < 0 and y < 0 for x, y in keys[0][1]) and len(keys[0][1]) == len(poly)


def testPickSketchTakesTheLastMaskOfTheOnlyMaskedEffect():
    mosaic = {"effect": "Mosaic", "matchName": "AE.ADBE Mosaic", "componentId": "1",
              "masks": [{"id": "a", "slot": 0}, {"id": "b", "slot": 1}]}
    blur = {"effect": "Gaussian Blur", "matchName": "AE.ADBE Gaussian Blur 2", "componentId": "2", "masks": [{"id": "c", "slot": 0}]}
    fx, mask = sketch.pickSketch([mosaic], None)
    assert fx["effect"] == "Mosaic" and mask["id"] == "b"
    with pytest.raises(ValueError, match="Mosaic, Gaussian Blur"):
        sketch.pickSketch([mosaic, blur], None)
    assert sketch.pickSketch([mosaic, blur], "Gaussian Blur")[1]["id"] == "c"
    assert sketch.pickSketch([mosaic, blur], "mosaic")[1]["id"] == "b"
    with pytest.raises(ValueError, match="no mask"):
        sketch.pickSketch([], None)
    with pytest.raises(ValueError, match="no mask on the Gaussian Blur"):
        sketch.pickSketch([mosaic], "Gaussian Blur")
