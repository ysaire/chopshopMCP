"""motion graphics: hyperframes templates filled, timed, rendered with alpha, previewed and put on a free track."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))

import graphics  # noqa: E402

card = """<!doctype html>
<html data-composition-variables='[{"id":"name","type":"string","label":"Name","default":"Product"},
  {"id":"accent","type":"color","label":"Accent","default":"#ff5a36"}]'>
<head><meta name="viewport" content="width=1920, height=1080" /><meta name="description" content="a card" />
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script></head>
<body><div id="root" data-composition-id="main" data-start="0" data-duration="4" data-width="1920" data-height="1080">
<div id="card" class="clip" data-start="0" data-duration="4">x</div>
<div id="tag" class="clip" data-start="1" data-duration="2">y</div></div></body></html>"""


def testDeclaredVariablesSurviveAnApostrophe():
    filled = graphics.withValues(card, {"name": "Le pad d'origine", "accent": "#00aaff"})
    assert {v["id"]: v["default"] for v in graphics.declared(filled)} == {"name": "Le pad d'origine", "accent": "#00aaff"}
    assert "'Le pad d'" not in filled


def testAnUnknownVariableNamesTheRealOnes():
    with pytest.raises(ValueError, match="no variable title: name, accent"):
        graphics.withValues(card, {"title": "x"})


def testComposeFitsTheSequenceAndRunsOffline():
    html = graphics.compose(card, duration=6, width=1080, height=1920, fps=59.94)
    root = graphics.rootTag(html)
    assert 'data-width="1080"' in root and 'data-height="1920"' in root and 'data-duration="6"' in root
    assert 'data-fps="60000/1001"' in root
    assert 'id="card" class="clip" data-start="0" data-duration="6"' in html
    assert 'id="tag" class="clip" data-start="1" data-duration="2"' in html
    assert 'content="width=1080, height=1920"' in html
    assert '<script src="gsap.min.js"></script>' in html


def testFrameRatesHyperframesUnderstands():
    assert [graphics.fpsArg(f) for f in (60, 59.94, 29.97, 23.976, 25, 30)] == \
        ["60", "60000/1001", "30000/1001", "24000/1001", "25", "30"]


def testThePreviewCompositesInLinearLightLikePremiere():
    frame = np.full((2, 2, 3), 0.5)
    overlay = np.zeros((2, 2, 4))
    overlay[0, 0] = (0.05, 0.05, 0.05, 0.863)
    overlay[0, 1] = (1.0, 0.0, 0.0, 1.0)
    out = graphics.composite(frame, overlay)
    expected = ((0.5 ** 2.4) * 0.137 + (0.05 ** 2.4) * 0.863) ** (1 / 2.4)
    assert out[0, 0, 0] == pytest.approx(expected, abs=1e-3)
    assert out[0, 0, 0] > 0.5 * 0.137 + 0.05 * 0.863 + 0.1
    assert out[0, 1].tolist() == pytest.approx([1.0, 0.0, 0.0])
    assert out[1, 1].tolist() == pytest.approx([0.5, 0.5, 0.5])


def testAGraphicGoesOnTheFirstFreeTrackAboveTheShots():
    state = {"video": [{"name": "V1", "clips": [{"start": 0, "end": 60}]},
                       {"name": "V2", "clips": [{"start": 30, "end": 33}]},
                       {"name": "V3", "clips": []}]}
    assert graphics.freeTrack(state, 10, 14) == "V2"
    assert graphics.freeTrack(state, 32, 35) == "V3"
    assert graphics.freeTrack(state, 33, 36) == "V2"
    state["video"][2]["clips"].append({"start": 31, "end": 40})
    assert graphics.freeTrack(state, 32, 35) is None


def testEveryTemplateDescribesItselfAndComposes():
    found = graphics.listTemplates()
    assert {t["name"] for t in found} >= {"product", "specs", "chapter", "callout", "stat", "verdict"}
    for t in found:
        assert t["description"] and t["duration"] > 0 and t["variables"], t["name"]
        html = graphics.compose(graphics.templateText(t["name"]), duration=t["duration"] + 1, width=1920, height=1080, fps=60)
        assert f'data-duration="{t["duration"] + 1:g}"' in graphics.rootTag(html), t["name"]
        assert "cdn.jsdelivr" not in html, t["name"]


@pytest.mark.skipif(graphics.ready() is not None, reason="hyperframes is not installed in motion/")
def testATemplateRendersToProResWithAlpha(tmp_path):
    html = graphics.compose(graphics.withValues(graphics.templateText("product"), {"name": "Test"}),
                          duration=1, width=320, height=180, fps=30)
    out = graphics.render(graphics.stage(html, tmp_path / "stage"), tmp_path / "product.mov", 30)
    probe = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_entries", "stream=codec_name,profile,pix_fmt,width",
                            "-of", "json", str(out)], capture_output=True, text=True, check=True)
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["codec_name"] == "prores" and stream["profile"] == "4444" and stream["pix_fmt"].startswith("yuva")
    assert stream["width"] == 320


def testTheHouseAccentFillsTemplatesThatTakeOne():
    text, duration, name = graphics.prepare("product", None, {"name": "X"}, accent="#00c2ff")
    assert {v["id"]: v["default"] for v in graphics.declared(text)}["accent"] == "#00c2ff"
    assert duration == 4 and name == "product"
    text, _, _ = graphics.prepare("product", None, {"accent": "#ffffff"}, accent="#00c2ff")
    assert {v["id"]: v["default"] for v in graphics.declared(text)}["accent"] == "#ffffff"
    text, duration, name = graphics.prepare(None, card, None, accent="#00c2ff")
    assert duration == 4 and name == "graphic"
    with pytest.raises(ValueError, match="a template or html, not both"):
        graphics.prepare("product", card, None)
    with pytest.raises(ValueError, match="a template or html"):
        graphics.prepare(None, None, None)
