"""reference breakdowns against a synthetic edit with known cuts, moves, flash and hits, plus the style memory."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import av
import numpy as np
import pytest

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "sidecar"))
sys.path.insert(0, str(root / "tests"))

import reference  # noqa: E402
from fixtures import makeReferenceVideo, referenceScene, refCuts, refFlash, refPunch  # noqa: E402


@pytest.fixture(scope="module")
def edit(tmp_path_factory):
    path = makeReferenceVideo(str(tmp_path_factory.mktemp("ref") / "reference edit.mp4"))
    return reference.breakdown(path)


def testHardCutsAreFoundToTheFrame(edit):
    hard = [s["start"] for s in edit["shots"][1:] if s["cut"] == "cut"]
    assert len(hard) == len(refCuts)
    assert np.abs(np.array(hard) - refCuts).max() <= 1 / 30 + 1e-3


def testPunchInReadsAsAZoomJumpNotANewShot(edit):
    punches = [s["start"] for s in edit["shots"] if s["cut"] == "punch"]
    assert len(punches) == 1 and abs(punches[0] - refPunch) <= 1 / 30 + 1e-3


def testFlashIsNotACut(edit):
    assert len(edit["flashes"]) == 1 and abs(edit["flashes"][0] - refFlash) < 0.04
    assert not any(abs(s["start"] - refFlash) < 0.2 for s in edit["shots"])


def testCameraMovesPerShot(edit):
    byStart = {round(s["start"], 1): s["camera"] for s in edit["shots"]}
    assert 25 <= byStart[2.0]["zoom"] <= 35  # slow zoom 1.0 to 1.3
    assert 7 <= byStart[3.2]["panX"] <= 14 and abs(byStart[3.2]["zoom"]) < 3  # camera pans right
    assert not byStart[4.0]["punches"] and abs(byStart[4.0]["zoom"]) < 3  # the punch is the next shot's start
    assert abs(byStart[0.0]["zoom"]) < 2 and abs(byStart[0.0]["panX"]) < 2
    assert "zoom +" in reference.cameraLabel(byStart[2.0]) and "pan right" in reference.cameraLabel(byStart[3.2])


def testCutsLandOnSoundHits(edit):
    sound = edit["sound"]
    assert sound["hasAudio"] and sound["cutsOnHits"] == 1.0
    assert sound["cutsOnHitsByChance"] < 0.5


def testRhythmNumbers(edit):
    r = edit["rhythm"]
    assert r["shots"] == 7 and r["firstCut"] == pytest.approx(1.0, abs=0.04)
    assert r["cutsInFirst3s"] == 3 and r["cutsPer5s"] == [6, 0]
    assert r["cutKinds"] == {"cut": 5, "punch": 1}
    assert r["shotSeconds"]["median"] == pytest.approx(0.8, abs=0.05)


def testShotTableAndSheet(edit, tmp_path):
    table = reference.shotTable(edit)
    assert table.count("\n#") == 7 and "hit" in table and "flash" in table
    sheet = reference.shotSheet(edit, tmp_path / "shots.jpg", 1, 4, cols=2)
    from PIL import Image
    image = Image.open(sheet["path"])
    assert sheet["shots"] == [1, 2, 3, 4] and image.size[0] == 2 * 256
    # cell 3 shows shot 3 (scene 3 palette), not a neighbour
    cellH = image.size[1] // 2
    cell = np.asarray(image.convert("RGB"))[cellH + 30:cellH + 60, 20:60].reshape(-1, 3).mean(0)
    scene = referenceScene(3)[150:300, 300:500].reshape(-1, 3).mean(0)
    assert np.abs(cell - scene).max() < 40


def testJumpCutOnTheSameFraming(tmp_path):
    """talking head: same background, the subject jumps position between takes."""
    import cv2
    rng = np.random.default_rng(9)
    room = cv2.GaussianBlur(rng.integers(120, 255, (180, 320, 3)).astype(np.uint8), (0, 0), 1.2)
    # dark shirt with some texture, a light wall behind
    person = cv2.GaussianBlur(rng.integers(10, 90, (130, 110, 3)).astype(np.uint8), (0, 0), 1.0)
    path = str(tmp_path / "talking head.mp4")
    with av.open(path, mode="w") as container:
        stream = container.add_stream("libx264", rate=30)
        stream.width, stream.height, stream.pix_fmt = 320, 180, "yuv420p"
        stream.options = {"crf": "14"}
        for i in range(60):
            img = room.copy()
            x = (40 if i < 30 else 150) + int(2 * np.sin(i / 3))  # small sway while talking
            img[40:170, x:x + 110] = person
            for packet in stream.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    data = reference.breakdown(path)
    assert [(round(s["start"], 2), s["cut"]) for s in data["shots"][1:]] == [(1.0, "jump")]


def testYoutubeRollingCaptionsAreFolded(tmp_path):
    vtt = tmp_path / "auto.en.vtt"
    vtt.write_text(
        "WEBVTT\nKind: captions\nLanguage: en\n\n"
        "00:00:00.000 --> 00:00:02.000 align:start position:0%\n \nso<00:00:00.500><c> today</c><00:00:01.000><c> we</c>\n\n"
        "00:00:02.000 --> 00:00:02.010 align:start position:0%\nso today we\n \n\n"
        "00:00:02.010 --> 00:00:04.000 align:start position:0%\nso today we\nare<00:00:02.500><c> testing</c>\n\n",
        encoding="utf-8")
    cues = reference.readVtt(str(vtt))
    assert [c["text"] for c in cues] == ["so today we", "are testing"]
    profile = reference.speechProfile(cues, 4.0)
    assert profile["wordsPerMinute"] == pytest.approx(75.0) and profile["opening"].startswith("so today")


@pytest.fixture()
def appData(monkeypatch):
    folder = Path(tempfile.mkdtemp(prefix="chopshopStyle"))
    monkeypatch.setenv("APPDATA", str(folder))
    return folder


def testRememberGoesUnderItsSection(appData):
    import style
    style.remember("captions two words max, lower third", "captions")
    style.remember("never whip pans", "never")
    text = style.stylePath().read_text(encoding="utf-8")
    captions = text.index("## Captions")
    assert captions < text.index("- captions two words max") < text.index("## Motion")
    assert text.index("## Never do") < text.index("- never whip pans")
    with pytest.raises(ValueError):
        style.remember("x", "colour science")


def testBreakdownsAreSavedAndListedByFormat(appData):
    import style
    style.saveBreakdown("JamFam face reveal", "# JamFam\n- what to steal: meme on every punchline", {"cutsPerMinute": 60},
                        "https://example.com/v", "gaming")
    style.saveBreakdown("optimum glass pad", "# optimum\n- what to steal: j-cuts into b-roll", None, None, "unboxing")
    listed = style.listBreakdowns("gaming")
    assert [b["name"] for b in listed] == ["JamFam_face_reveal"] and listed[0]["steal"] == "meme on every punchline"
    assert "j-cuts" in style.readBreakdown("optimum")
    assert len(style.getStyle()["breakdowns"]) == 2


def testToolsBreakDownAReference(appData, tmp_path):
    import mcpServer
    path = makeReferenceVideo(str(tmp_path / "ref tool.mp4"))
    out = asyncio.run(mcpServer.server.call_tool("breakdownReference", {"path": path, "waitSeconds": 60}))
    data = getattr(out, "structuredContent", None) or getattr(out, "structured_content", None)
    data = data.get("result", data) if data else json.loads(out.content[0].text)
    assert data["status"] == "done" and data["metrics"]["rhythm"]["shots"] == 7
    assert "what to steal" in data["card"] and "#6" in data["shotTable"]
    shots = asyncio.run(mcpServer.server.call_tool("referenceShots", {"path": path, "fromShot": 0, "count": 6}))
    kinds = [getattr(c, "type", None) for c in (shots.content if hasattr(shots, "content") else shots[0])]
    assert "image" in kinds
    saved = asyncio.run(mcpServer.server.call_tool("saveBreakdown", {"name": "synthetic", "card": data["card"],
                                                                     "path": path, "format": "test"}))
    assert not getattr(saved, "isError", False) and not getattr(saved, "is_error", False)
    meta = json.loads((Path(os.environ["APPDATA"]) / "chopshopMCP" / "style" / "breakdowns" / "synthetic.json").read_text())
    assert meta["metrics"]["rhythm"]["shots"] == 7 and meta["format"] == "test"
