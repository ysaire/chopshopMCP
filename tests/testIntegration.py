"""claude (mcp tools) -> python bridge client -> node bridge -> host.jsx on a fake premiere."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as et
from pathlib import Path

import numpy as np
import pytest

root = Path(__file__).resolve().parents[1]
appData = Path(tempfile.mkdtemp(prefix="chopshopMCPInt"))
os.environ["APPDATA"] = str(appData)
sys.path.insert(0, str(root / "sidecar"))
sys.path.insert(0, str(root / "tests"))

import ears  # noqa: E402
import graphics  # noqa: E402
import mcpServer  # noqa: E402
import pipeline  # noqa: E402
from fixtures import bursts, makeRush  # noqa: E402


@pytest.fixture(scope="module")
def world():
    os.environ["APPDATA"] = str(appData)  # other test modules may have moved it
    rush = makeRush(str(appData / "rush.mp4"))
    bridgeFile = appData / "chopshopMCP" / "bridge.json"
    proc = subprocess.Popen(
        ["node", str(root / "tests" / "fakePremiereServer.js"), str(bridgeFile), rush],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    ready = json.loads(proc.stdout.readline())
    assert ready["ready"]

    def send(line):
        proc.stdin.write(line + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    yield {"rush": rush, "state": lambda: send("?"), "send": send}
    proc.terminate()
    proc.wait(timeout=5)


def fakeTranscript():
    words = []
    for (start, end), text in zip(bursts, ["alors le", "alors le truc", "euh"]):
        parts = text.split()
        step = (end - start) / len(parts)
        for i, w in enumerate(parts):
            words.append({"text": w, "start": start + i * step, "end": start + (i + 1) * step, "prob": 0.9})
    return {"language": "fr", "duration": 7.0, "words": words, "utterances": ears.buildUtterances(words)}


def call(tool, args):
    result = asyncio.run(mcpServer.server.call_tool(tool, args))
    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        raise AssertionError(str(result))
    return result


def structured(result):
    data = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if data is not None:
        return data.get("result", data)
    return json.loads(result.content[0].text)


def testContextThroughBridge(world):
    ctx = structured(call("premiereContext", {}))
    assert ctx["media"][0]["path"] == world["rush"]
    assert ctx["media"][0]["inSequence"] is True
    assert ctx["media"][0]["kind"] == "video" and ctx["media"][0]["duration"] == pytest.approx(7.0, abs=0.05)
    assert ctx["plugin"]["version"] == "1.2.1" and Path(ctx["plugin"]["sidecar"]).name == "sidecar"
    assert "warning" not in ctx["plugin"]


def testContextWarnsWhenTheServerRunsOlderCodeThanTheDisk(world, monkeypatch):
    monkeypatch.setattr(mcpServer, "startedAt", 0.0)
    ctx = structured(call("premiereContext", {}))
    assert "restart Claude Desktop" in ctx["plugin"]["warning"]


def testBuildRoughCutLandsInPremiere(world, monkeypatch):
    monkeypatch.setattr(pipeline, "getTranscript", lambda *a, **k: fakeTranscript())
    out = structured(call("buildRoughCut", {"mediaPath": world["rush"], "dropIds": [0], "sequenceName": "rush clean"}))
    assert out["sequence"]["sequenceName"] == "rush clean"
    state = world["state"]()
    assert state["imported"] and state["opened"]
    xmlRoot = et.parse(state["imported"][-1]).getroot()
    # inherited the vertical 29.97 sequence from the fake premiere
    sc = xmlRoot.find("./sequence/media/video/format/samplecharacteristics")
    assert (sc.find("width").text, sc.find("height").text) == ("1080", "1920")
    assert xmlRoot.find("./sequence/rate/ntsc").text == "TRUE"
    assert len(xmlRoot.findall("./sequence/media/video/track/clipitem")) == 1


def testSequenceFrameShowsWhatPremiereRenders(world):
    world["send"]("activate Rush seq")
    out = call("sequenceFrame", {"time": 2.0})
    assert "Rush seq" in out.content[0].text and "00;00;02;00" in out.content[0].text
    assert out.content[1].type == "image"


def testMarkTakesDropsMarkers(world, monkeypatch):
    world["send"]("activate Rush seq")  # the last build left its own sequence open
    monkeypatch.setattr(pipeline, "getTranscript", lambda *a, **k: fakeTranscript())
    assert structured(call("transcribeMedia", {"mediaPath": world["rush"]}))["status"] == "done"
    out = structured(call("markTakes", {"mediaPath": world["rush"], "drops": [{"id": 0, "reason": "falseStart"}]}))
    assert out["created"] == 1
    marker = world["state"]()["markers"][-1]
    assert marker["name"] == "[chop] falseStart"
    assert abs(marker["start"] - 0.5) < 1e-6
    structured(call("clearReviewMarkers", {}))
    assert world["state"]()["markers"] == []


def testClaudeEditsAVoicelessRush(world):
    """the whole editor loop through mcp, on footage with no speech at all."""
    world["send"]("activate Rush seq")  # the last build left its own sequence open
    analysed = structured(call("analyzeRush", {"mediaPath": world["rush"], "waitSeconds": 60}))
    assert analysed["status"] == "done" and analysed["audioTracks"] == 1
    overview = call("rushOverview", {"mediaPath": world["rush"]}).content[0].text
    assert overview.startswith("rush 00:07")
    sheet = call("viewSheet", {"mediaPath": world["rush"], "start": 0, "end": 7})
    assert [c.type for c in sheet.content] == ["text", "image"]
    whole = sheet.content[0].text
    assert "stops at" not in whole
    # 28 cells asked for, 20 fit: the answer says where it stopped and where to go on
    tight = call("viewSheet", {"mediaPath": world["rush"], "start": 0, "end": 7, "every": 0.25}).content[0].text
    assert "stops at 4.8 s of 7.0 (20 cells max): call again from 5.0" in tight
    assert "thumbnails exist every 1.0 s: lookAt for a precise frame" in tight
    structured(call("logShots", {"mediaPath": world["rush"], "entries": [
        {"start": 2.3, "end": 3.5, "description": "best burst", "interest": 8, "tags": ["payoff"]}]}))
    assert "best burst" in call("getShotLog", {"mediaPath": world["rush"]}).content[0].text
    assert "Hook" in call("getPlaybook", {"format": "core"}).content[0].text

    built = structured(call("buildEdit", {"mediaPath": world["rush"], "plan": {
        "name": "rush | flash", "segments": [
            {"start": 2.3, "end": 3.5, "zoom": {"scale": 1.3, "x": 0.4, "y": 0.5}},
            {"start": 0.5, "end": 1.5},
            {"start": 5.0, "end": 6.0, "speed": 2},
        ]}}))
    assert built["sequenceName"] == "rush | flash"
    # the fake premiere sequence is 1080x1920, the rush sits on it, so the edit inherits that frame
    assert built["format"] == [1080, 1920] and built["motion"]["applied"] == 3
    assert [t["speed"] for t in built["timeline"]] == [1.0, 1.0, 2.0]

    captions = structured(call("addCaptions", {"mediaPath": world["rush"], "sequenceId": built["sequenceId"],
                                               "captions": [{"start": 0, "end": 1.2, "text": "listen"}]}))
    assert captions["created"] is True
    reframed = structured(call("reframeVertical", {"sequenceId": built["sequenceId"]}))
    assert reframed["sequenceId"].endswith("-reframed")


def testMasksThroughMcp(world, monkeypatch):
    """blur zone + title behind the subject, built through mcp into the fake premiere."""
    world["send"]("activate Rush seq")  # the last build left its own sequence open
    import masks
    from testEditor import FakeSegmenter
    monkeypatch.setattr(masks, "Segmenter", FakeSegmenter)
    rush = world["rush"]
    gridded = call("lookAt", {"mediaPath": rush, "times": [1.0], "grid": True})
    assert gridded.content[0].type == "image"
    preview = call("previewFrame", {"mediaPath": rush, "time": 1.0, "format": "vertical",
                                    "blur": [{"box": [0.1, 0.1, 0.4, 0.4], "label": "screen"}],
                                    "title": {"text": "BOOM", "behind": True, "subject": {"box": [0.3, 0.3, 0.7, 0.7]}}})
    assert "screen" in preview.content[0].text and preview.content[1].type == "image"

    plan = {"name": "rush | masked", "masks": "matte",
            "blur": [{"box": [0.1, 0.1, 0.4, 0.4], "from": 0.0, "to": 2.0, "label": "screen"}],
            "segments": [{"start": 0.5, "end": 1.5, "title": {"text": "BOOM", "behind": True, "from": 0.2, "to": 0.8,
                                                               "subject": {"box": [0.3, 0.3, 0.7, 0.7], "track": False}}},
                         {"start": 2.3, "end": 3.5}]}
    result = structured(call("buildEdit", {"mediaPath": rush, "plan": plan, "waitSeconds": 120}))
    assert result["status"] == "done" and result["maskMode"] == "matte", result.get("warnings")
    assert result["layers"] == {"titles": 1, "cutouts": 1, "blurredSegments": 1, "mattes": 2}
    report = result["matteReport"]
    assert report["ok"] is True and [n["applied"] for n in report["nests"]] == [2, 1]
    # v1 fill on 2 clips + the subject nest mirrored on v3
    assert result["motion"]["applied"] == 3 and result["motion"]["issues"] == []
    state = world["state"]()
    root = et.parse(state["imported"][-1]).getroot()
    assert len(root.findall("./sequence/media/video/track")) == 3
    # premiere got both nests, keyed them, and the edit is the open sequence again
    assert {"rush | masked (chop comp)", "rush | masked (chop subject)"} <= set(state["sequences"])
    assert [a["effect"] for a in state["qeAdded"]] == ["Track Matte Key", "Track Matte Key"]
    assert state["active"] == "rush | masked"


def testNativeMasksThroughMcp(world):
    """stage built and keyed, project saved, masks grafted into a copy, final sequence imported from it, stage deleted."""
    import gzip
    import maskGraft
    world["send"]("activate Rush seq")
    rush = world["rush"]
    plan = {"name": "rush | native", "masks": "native",
            "blur": [{"box": [0.1, 0.1, 0.4, 0.4], "from": 0.0, "to": 2.0, "label": "screen"},
                     {"box": [0.6, 0.6, 0.9, 0.9], "from": 2.5, "to": 3.0, "style": "pixelate", "shape": "ellipse", "label": "plate"}],
            "segments": [{"start": 0.5, "end": 1.5}, {"start": 2.3, "end": 3.5}]}
    result = structured(call("buildEdit", {"mediaPath": rush, "plan": plan, "waitSeconds": 120}))
    assert result["status"] == "done" and result["maskMode"] == "native", result.get("warnings")
    assert result["sequenceName"] == "rush | native" and result["layers"]["masks"] == 2
    report = result["maskReport"]
    assert report["ok"] is True and report["masks"] == 2, report
    assert report["projectCopy"].endswith(".prproj") and report["stageLeft"] == []
    assert report["stageDeleted"] == ["rush | native (chop stage)"]
    state = world["state"]()
    assert state["imported"][-1] == report["projectCopy"]
    assert state["active"] == "rush | native" and not [s for s in state["sequences"] if s.startswith("rush | native (")]
    # the mosaic went in through qe on the stage before the save, so the copy carries it
    assert [a["effect"] for a in state["qeAdded"] if a["sequence"] == "rush | native (chop stage)"] == ["Mosaic"]
    # no nest: each v1 clip carries its zone's effect, one mask under it
    with gzip.open(report["projectCopy"], "rb") as fh:
        project = maskGraft.Project(fh.read().decode("utf-8"))
    main = project.sequence("rush | native")
    blur = project.findEffect(main.get("ObjectUID"), track=0, index=0, effect="Gaussian Blur")
    mosaic = project.findEffect(main.get("ObjectUID"), track=0, index=1, effect="Mosaic")
    assert [len(e.findall("SubComponents/SubComponent")) for e in (blur, mosaic)] == [1, 1]
    mask = project.byId[blur.find("SubComponents/SubComponent").get("ObjectRef")]
    params = [project.byId[p.get("ObjectRef")] for p in mask.findall("Component/Params/Param")]
    path = next(p for p in params if p.findtext("Name") == "Mask Path")
    # the first key sits on the clip's first frame, half a second in at the rush's own frame rate
    firstTick = int(path.findtext("Keyframes").split(",")[0])
    assert firstTick == pytest.approx(0.5 * maskGraft.ticksPerSecond, abs=maskGraft.ticksPerSecond / 24)
    # the final sequence is what later tools see
    frame = call("sequenceFrame", {"time": 1.0, "sequenceId": result["sequenceId"]})
    assert "rush | native" in frame.content[0].text


def testRefusedMatteKeysFallBackToBakedMasks(world, monkeypatch):
    import masks
    from testEditor import FakeSegmenter
    monkeypatch.setattr(masks, "Segmenter", FakeSegmenter)
    world["send"]("refuseMatte on")
    try:
        plan = {"name": "rush | stubborn", "masks": "matte", "blur": [{"box": [0.1, 0.1, 0.4, 0.4], "from": 0.0, "to": 2.0}],
                "segments": [{"start": 0.5, "end": 1.5}]}
        result = structured(call("buildEdit", {"mediaPath": world["rush"], "plan": plan, "waitSeconds": 120}))
    finally:
        world["send"]("refuseMatte off")
    assert result["maskMode"] == "baked" and result["sequenceName"] == "rush | stubborn (baked)"
    assert result["matteReport"]["ok"] is False
    assert any("refused the track matte keys" in w for w in result["warnings"])
    root = et.parse(world["state"]()["imported"][-1]).getroot()
    assert "baked_" in root.find("./sequence/media/video/track/clipitem/file/pathurl").text


def testPresetsThroughMcp(world, tmp_path, monkeypatch):
    """claude lists the user's presets, replays one on a clip, then tweaks a value."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))  # the real profile's presets stay out
    from fixtures import makePresetFile
    from config import configPath
    world["send"]("activate Rush seq")
    pack = makePresetFile(str(tmp_path / "flash test.prfpset"))
    configPath().write_text(json.dumps({"presetPaths": [pack]}), encoding="utf-8")
    try:
        listing = call("listPresets", {"query": "zoom"}).content[0].text
        assert listing.startswith("1 of 5 presets") and "flash test/Smooth Zoom Out | video | 0.50s from the out" in listing
        described = structured(call("describePreset", {"preset": "Pop In"}))
        height = next(p for p in described["effects"][0]["params"] if p["name"] == "Scale Height")
        assert height["keys"][0] == [0.0, 75.0]

        applied = structured(call("applyPreset", {"preset": "Smooth Zoom Out", "targets": [{"track": "V1", "index": 0}]}))
        assert applied["report"]["ok"] is True, applied
        assert applied["report"]["clips"][0]["effects"][0]["keys"] >= 14
        effects = structured(call("clipEffects", {"targets": [{"track": "V1", "at": 1.0}]}))
        transform = next(c for c in effects["clips"][0]["components"] if c["name"] == "Transform")
        scale = transform["properties"][3]
        assert scale["animated"] and scale["lastKey"] == pytest.approx(7.0)

        changed = structured(call("setClipEffect", {"effect": "Motion", "values": {"Scale": 130},
                                                    "keys": {"Position": [[0, [0.5, 0.5]], [1, [0.4, 0.5]]]},
                                                    "targets": [{"track": "V1", "index": 0}]}))
        assert changed["report"]["ok"] is True, changed
        motion = next(c for c in structured(call("clipEffects", {"targets": [{"track": "V1", "index": 0}]}))["clips"][0]["components"]
                      if c["name"] == "Motion")
        assert motion["properties"][1]["value"] == 130 and motion["properties"][0]["keys"] == 2
        with pytest.raises(Exception, match="audio presets"):
            call("applyPreset", {"preset": "Voice Air", "targets": [{"track": "A1", "index": 0}]})
    finally:
        configPath().unlink()


def testCaptionsAnimatedWithAPresetThroughMcp(world, tmp_path):
    from fixtures import makePresetFile
    from config import configPath
    world["send"]("activate Rush seq")
    pack = makePresetFile(str(tmp_path / "flash test.prfpset"))
    configPath().write_text(json.dumps({"presetPaths": [pack], "captionStyles": {
        "mine": {"base": "pop", "preset": "Pop In", "color": "#FFE600"}}}), encoding="utf-8")
    try:
        styles = structured(call("listCaptionStyles", {}))
        assert styles["styles"]["mine"]["preset"] == "Pop In"
        plan = {"name": "rush | captioned", "segments": [{"start": 0.5, "end": 1.5}, {"start": 2.3, "end": 3.5}],
                "captions": {"style": "mine", "cues": [{"start": 0.1, "end": 0.9, "text": "first"},
                                                       {"start": 1.2, "end": 2.0, "text": "second one"}]}}
        built = structured(call("buildEdit", {"mediaPath": world["rush"], "plan": plan, "waitSeconds": 120}))
        assert built["layers"]["captions"] == 2
        assert built["presetReport"] == [{"preset": "flash test/Pop In", "clips": 2, "ok": True}], built
        # v1 fill on 2 clips + 2 captions placed by motion
        assert built["motion"]["applied"] == 4
        added = [a for a in world["state"]()["qeAdded"] if a["sequence"] == "rush | captioned"]
        assert [(a["track"], a["index"], a["effect"]) for a in added] == [(1, 0, "Transform"), (1, 1, "Transform")]
    finally:
        configPath().unlink()


def testVoiceOverBothWaysThroughMcp(world, tmp_path, monkeypatch):
    """voice first: layout then build with ducking. edit first: slots on the built sequence."""
    from testVoiceOver import fakeVoice
    from fixtures import makeRush as makeAudio
    world["send"]("activate Rush seq")
    voice = makeAudio(str(tmp_path / "vo take.m4a"), withVideo=False)
    monkeypatch.setattr(pipeline, "getVoiceTranscript", lambda *a, **k: fakeVoice())
    realTranscript = pipeline.getTranscript
    monkeypatch.setattr(pipeline, "getTranscript", lambda path, cfg, onProgress=None: fakeVoice() if path == voice else realTranscript(path, cfg, onProgress))

    layout = structured(call("layoutVoiceOver", {"path": voice, "dropIds": [0]}))
    assert layout["status"] == "done" and len(layout["pieces"]) == 2
    plan = {"name": "rush | voiced", "segments": [{"start": 0.5, "end": 1.5}, {"start": 2.3, "end": 3.5}, {"start": 5.0, "end": 6.0}],
            "voiceOver": layout["planEntry"]}
    built = structured(call("buildEdit", {"mediaPath": world["rush"], "plan": plan, "waitSeconds": 120}))
    assert built["voiceOver"]["pieces"][0]["at"] == 0.0
    assert built["duckReport"]["clips"] >= 1 and built["duckReport"]["keys"] >= 4, built
    voiceClips = structured(call("timelineClips", {"track": "A2", "sequenceId": built["sequenceId"]}))
    assert [os.path.normpath(c["mediaPath"]) for c in voiceClips["clips"]] == [os.path.normpath(voice)] * 2

    slots = structured(call("voiceOverSlots", {"sequenceId": built["sequenceId"]}))
    assert slots["sequence"] == "rush | voiced" and isinstance(slots["slots"], list)
    with pytest.raises(Exception, match="no chopshopMCP build"):
        call("voiceOverSlots", {"sequenceId": "nope"})


def testTrackingThroughMcp(world, tmp_path):
    """check a track by eye, then build with a replaced screen and a stabilized shot."""
    from fixtures import makeTrackingRush, screenAt
    from PIL import Image
    world["send"]("activate Rush seq")
    shaky = makeTrackingRush(str(tmp_path / "shaky.mp4"))
    quad = (screenAt(0.0) / [640, 360]).round(5).tolist()
    sheet = call("checkTracking", {"mediaPath": shaky, "start": 0.0, "end": 1.0, "quad": quad})
    assert sheet.content[0].text.startswith("quad tracked over 30 frames, lost on 0") and sheet.content[1].type == "image"
    poster = tmp_path / "poster.png"
    Image.new("RGB", (300, 200), (10, 120, 250)).save(poster)
    plan = {"name": "shaky | fixed", "screens": [{"quad": quad, "from": 0.0, "to": 2.0, "media": str(poster)}],
            "segments": [{"start": 0.2, "end": 1.2}, {"start": 1.2, "end": 2.0, "stabilize": True}]}
    built = structured(call("buildEdit", {"mediaPath": shaky, "plan": plan, "waitSeconds": 120}))
    effects = {(r["sequence"], r["effects"]): r for r in built["effectReport"]}
    assert effects[("comp", "Corner Pin")]["ok"] is True, built["effectReport"]
    assert effects[("main", "Transform")]["ok"] is True, built["effectReport"]
    added = [(a["sequence"], a["effect"]) for a in world["state"]()["qeAdded"] if a["sequence"].startswith("shaky")]
    assert ("shaky | fixed (chop comp)", "Corner Pin") in added and ("shaky | fixed", "Transform") in added


def testSeveralFilesThroughMcp(world, tmp_path):
    """a second camera, a meme over a shot with its sound ducking the rush, a whoosh turned down, a j cut."""
    from fixtures import makeColorClip, makeSfx
    world["send"]("activate Rush seq")
    cam2 = makeColorClip(str(tmp_path / "cam2.mp4"), audio="mono")
    whoosh = makeSfx(str(tmp_path / "whoosh.wav"))
    plan = {"name": "rush | multi", "sources": {"cam2": cam2}, "segments": [
        {"start": 0.5, "end": 1.5},
        {"media": "cam2", "start": 0.5, "end": 1.5, "jcut": 0.2},
        {"start": 2.3, "end": 3.5, "cover": [
            {"media": whoosh, "volume": -6},
            {"media": "cam2", "start": 2.0, "duration": 0.6, "from": 0.3, "audio": "duck", "box": [0.55, 0.05, 0.95, 0.45]}]}]}
    built = structured(call("buildEdit", {"mediaPath": world["rush"], "plan": plan, "waitSeconds": 120}))
    reports = {r["kind"]: r for r in built["audioReport"]}
    assert reports["duck"]["clips"] >= 1 and not reports["duck"]["issues"], built
    assert reports["volume"]["clips"] == 1 and not reports["volume"]["issues"], built
    # the whoosh and the meme overlap: two cover tracks
    assert [g["tracks"] for g in built["audioTracks"]] == [["A1"], ["A2"], ["A3"], ["A4"]]
    second = structured(call("timelineClips", {"track": "A2", "sequenceId": built["sequenceId"]}))
    assert [os.path.normpath(c["mediaPath"]) for c in second["clips"]] == [os.path.normpath(cam2)]
    assert abs(second["clips"][0]["start"] - 0.8) < 0.04  # heard 0.2 s before its picture
    covers = [structured(call("timelineClips", {"track": t, "sequenceId": built["sequenceId"]}))["clips"] for t in ("A3", "A4")]
    assert [[os.path.normpath(c["mediaPath"]) for c in clips] for clips in covers] == [[os.path.normpath(whoosh)], [os.path.normpath(cam2)]]
    pip = structured(call("timelineClips", {"track": "V2", "sequenceId": built["sequenceId"]}))
    assert [os.path.normpath(c["mediaPath"]) for c in pip["clips"]] == [os.path.normpath(cam2)]


def testLibraryThroughMcp(tmp_path, monkeypatch):
    from fixtures import makeSfx
    folder = tmp_path / "library"
    folder.mkdir()
    makeSfx(str(folder / "vine boom.wav"))
    monkeypatch.setattr(pipeline, "getTranscript", lambda path, cfg, onProgress=None: {"words": [{"text": "boom"}]})
    done = structured(call("indexLibrary", {"folder": str(folder), "waitSeconds": 60}))
    assert done["status"] == "done" and done["clips"] == 1 and done["withWords"] == 1
    call("tagLibrary", {"folder": str(folder), "entries": [{"id": 0, "description": "deep bass hit after a punchline", "tags": ["hit"]}]})
    result = call("searchLibrary", {"query": "bass hit", "folder": str(folder)})
    data = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    found = data["result"] if data else [json.loads(c.text) for c in result.content]
    assert found[0]["path"].endswith("vine boom.wav")
    sheet = call("librarySheet", {"folder": str(folder)})
    assert "deep bass hit" in sheet.content[0].text and sheet.content[1].type == "image"


def testSharedOutlineSegmenterIsBuiltOnce(monkeypatch):
    import masks
    made = []
    monkeypatch.setattr(masks, "outlineSegmenter", lambda quality="fast": made.append(1) or object())
    mcpServer.segmenters.pop("outline", None)
    first = mcpServer.sharedSegmenter("outline")
    assert mcpServer.sharedSegmenter("outline") is first and made == [1]


def testOutlineZoneThroughMcpReportsTheSegmenter(world, monkeypatch):
    """the report names the model that ran, with a warning when it is not the video model."""
    import masks
    from testEditor import FakeSegmenter

    class Described(FakeSegmenter):
        def describe(self):
            return {"quality": "fast", "provider": "fake", "gpu": False}

    monkeypatch.setattr(masks, "outlineSegmenter", lambda quality="fast": Described())
    mcpServer.segmenters.pop("outline", None)
    world["send"]("activate Rush seq")
    plan = {"name": "rush | outlined", "masks": "native",
            "blur": [{"box": [0.1, 0.3, 0.3, 0.7], "from": 1.0, "to": 1.5, "track": True, "label": "thing"}],
            "segments": [{"start": 1.0, "end": 1.5}]}
    try:
        result = structured(call("buildEdit", {"mediaPath": world["rush"], "plan": plan, "waitSeconds": 120}))
    finally:
        mcpServer.segmenters.pop("outline", None)
    assert result["status"] == "done" and result["layers"]["masks"] == 1, result.get("warnings")
    assert result["outlines"] == {"quality": "fast", "provider": "fake", "gpu": False}
    assert any("mobilesam" in w for w in result["warnings"])


def testRefineMaskThroughMcp(world, monkeypatch):
    """the sketch prompts the segmenter, its keys are rewritten in a project copy, the sequence swapped under its name."""
    import gzip
    import maskGraft
    import masks
    import numpy as np

    class EchoOutliner:
        """gives back the sketch it was prompted with: what the refiner does with it is what the test checks."""

        def __init__(self):
            self.prompts, self.frames = 0, 0

        def outline(self, rgb, boxPx, state=None, mask=None):
            state = dict(state or {})
            if mask is not None:
                self.prompts += 1
                state["binary"] = mask.copy()
                state["box"] = masks.binaryBox(mask, 0.0, rgb.shape[1], rgb.shape[0])
            self.frames += 1
            return state["binary"].astype(np.float32), state

        def describe(self):
            return {"quality": "video", "provider": "fake", "gpu": False}

    echo = EchoOutliner()
    monkeypatch.setattr(masks, "outlineSegmenter", lambda quality="fast": echo)
    mcpServer.segmenters.pop("outline", None)
    world["send"]("activate Rush seq")
    plan = {"name": "rush | sketched", "masks": "native",
            "blur": [{"box": [0.3, 0.3, 0.7, 0.7], "from": 2.5, "to": 3.5, "style": "pixelate", "shape": "rect", "label": "thing"}],
            "segments": [{"start": 2.3, "end": 3.5}]}
    built = structured(call("buildEdit", {"mediaPath": world["rush"], "plan": plan, "waitSeconds": 120}))
    assert built["status"] == "done" and built["maskReport"]["ok"], built.get("warnings")
    world["send"]("select V1 0")
    world["send"]("marks 0.4 1.0")
    try:
        result = structured(call("refineMask", {"waitSeconds": 120}))
    finally:
        mcpServer.segmenters.pop("outline", None)
    assert result["status"] == "done" and result["ok"] is True, result
    assert result["sequenceName"] == "rush | sketched" and result["sequenceId"] != built["sequenceId"]
    assert result["effect"] == "Mosaic" and result["mask"]["slot"] == 0
    assert result["range"]["in"] == pytest.approx(2.7) and result["range"]["out"] == pytest.approx(3.3)
    assert result["frames"] == 18 and echo.frames == 18 and echo.prompts == 1
    assert result["outlines"]["provider"] == "fake" and result["stageDeleted"] == ["rush | sketched"]
    state = world["state"]()
    assert state["active"] == "rush | sketched" and state["sequences"].count("rush | sketched") == 1
    with gzip.open(result["projectCopy"], "rb") as fh:
        project = maskGraft.Project(fh.read().decode("utf-8"))
    masked = project.clipMasks(result["sequenceId"], 0, 0)
    assert [m["effect"] for m in masked] == ["Mosaic"] and len(masked[0]["masks"]) == 1
    keys = masked[0]["masks"][0]["keys"]
    inside = [(t, v) for t, v in keys if 2.7 - 1e-3 <= t < 3.3 - 1e-3]
    assert len(inside) == 18 and all(len(v) == masks.outlinePoints for _t, v in inside)
    # the echo returned the sketch itself: the refined polygon hugs the 0.3-0.7 box (grown by 2%)
    xs = [v[0] for v in inside[0][1]]
    assert min(xs) == pytest.approx(0.3, abs=0.03) and max(xs) == pytest.approx(0.7, abs=0.03)
    # the build's own keys outside the stretch stay: parked before 2.5, the box at 2.5 and on the last frame
    before = [round(t, 2) for t, _v in keys if t < 2.7 - 1e-3]
    assert before == [2.3, 2.47, 2.5] and [round(t, 2) for t, _v in keys if t >= 3.3 - 1e-3] == [3.47]


def presetRoots(tmp_path, monkeypatch):
    import review
    folder = tmp_path / "Adobe" / "Adobe Premiere Pro 2025" / "MediaIO" / "systempresets" / review.h264Folder
    folder.mkdir(parents=True)
    for name in review.presetNames.values():
        (folder / name).write_text("<epr/>")
    monkeypatch.setattr(review, "defaultRoots", [str(tmp_path / "Adobe")])
    return folder


def testWatchEditRendersTheSequenceAndWatchesIt(world, tmp_path, monkeypatch):
    """rendered through premiere's exporter with the low bitrate preset; the fake hands back the rush."""
    import review
    folder = presetRoots(tmp_path, monkeypatch)
    world["send"]("activate Rush seq")
    result = call("watchEdit", {"waitSeconds": 120})
    text = result.content[0].text
    assert "2 shots" in text  # the rush turns from red to blue at 3 s
    assert "silent 00:03.5-00:05.0" in text
    assert "LUFS" in text
    images = [c for c in result.content if getattr(c, "type", "") == "image"]
    assert len(images) == 1
    render = next(line for line in text.splitlines() if line.startswith("file ")).split("file ", 1)[1]
    assert Path(render).is_file() and Path(render).parent.name == "renders"
    assert str(folder / review.presetNames["review"]) in text
    # a second look renders again instead of answering from the first
    again = call("watchEdit", {"waitSeconds": 120}).content[0].text
    assert next(line for line in again.splitlines() if line.startswith("file ")) != f"file {render}"
    assert not Path(render).exists()


def testExportEditWritesTheMasterNextToTheProjectWithoutOverwriting(world, tmp_path, monkeypatch):
    import review
    folder = presetRoots(tmp_path, monkeypatch)
    world["send"]("activate Rush seq")
    first = call("exportEdit", {"waitSeconds": 120}).content[0].text
    path = Path(first.splitlines()[0].split("master ", 1)[1])
    assert path.is_file() and path.parent.name == "exports" and path.name == "Rush seq.mp4"
    assert str(folder / review.presetNames["master"]) in first and "2 shots" in first
    second = call("exportEdit", {"waitSeconds": 120}).content[0].text
    other = Path(second.splitlines()[0].split("master ", 1)[1])
    assert other != path and other.name == "Rush seq 2.mp4" and path.is_file()


def testClaudeReadsAndEditsTheSequenceInPlace(world):
    """removes one shot and lengthens another, picture and sound together, without a rebuild."""
    world["send"]("activate Live seq")
    read = structured(call("readEdit", {}))
    assert read["name"] == "Live seq" and read["duration"] == pytest.approx(5.0)
    assert read["tracks"][0].startswith("V1: #0 0.00-2.00 [0.00-2.00] rush.mp4 +A1 #0")
    done = structured(call("editSequence", {"operations": [{"op": "remove", "clip": "V1 #1"}, {"op": "trim", "clip": "V1 #2", "out": 6.5}]}))
    assert [r["ok"] for r in done["results"]] == [True, True]
    assert done["tracks"][0] == "V1: #0 0.00-2.00 [0.00-2.00] rush.mp4 +A1 #0; #1 2.00-3.50 [5.00-6.50] rush.mp4 +A1 #1"
    assert done["tracks"][1].startswith("A1: #0 0.00-2.00") and "#1 2.00-3.50 [5.00-6.50]" in done["tracks"][1]


def testClaudeDissolvesACutAndSeesItInTheTracks(world):
    world["send"]("activate Live seq")
    done = structured(call("editSequence", {"operations": [{"op": "transition", "clip": "V1 #1"}]}))
    assert done["results"][0]["ok"] and done["results"][0]["audio"] == ["A1 #1"]
    assert done["tracks"][0].endswith("| transitions Cross Dissolve 1.75-2.25")
    assert done["tracks"][1].endswith("| transitions Constant Power 1.75-2.25")


def testMatchShotsLeavesShotsThatAlreadySitWithTheReference(world):
    world["send"]("activate Live seq")
    out = call("matchShots", {"waitSeconds": 60})
    lines = out.content[0].text.splitlines()
    assert lines[0].startswith("reference V1 #") and len(lines) >= 2
    assert all("sits with the reference" in line for line in lines[1:])
    assert out.content[1].type == "image"
    with pytest.raises(Exception, match="no clip V1 #9 on V1"):
        call("matchShots", {"reference": "V1 #9", "waitSeconds": 60})


def fakeHyperframes(monkeypatch):
    seen = {}

    def render(folder, out, fps):
        seen["html"] = (Path(folder) / "index.html").read_text(encoding="utf-8")
        seen["kit"] = sorted(p.name for p in Path(folder).iterdir())
        seen["fps"] = fps
        Path(out).write_bytes(b"prores 4444")
        return Path(out)

    def snapshot(folder, times):
        seen["times"] = list(times)
        frame = np.zeros((1080, 1920, 4))
        frame[900:1000, 100:700] = (0.05, 0.05, 0.05, 1.0)
        return [frame for _ in times]

    gsap = Path(tempfile.mkdtemp()) / "gsap.min.js"
    gsap.write_text("window.gsap = {};", encoding="utf-8")
    monkeypatch.setattr(graphics, "ready", lambda: None)
    monkeypatch.setattr(graphics, "gsapScript", lambda: gsap)
    monkeypatch.setattr(graphics, "render", render)
    monkeypatch.setattr(graphics, "snapshot", snapshot)
    return seen


def testAddGraphicRendersForTheSequenceAndLaysItOnAFreeTrack(world, monkeypatch):
    seen = fakeHyperframes(monkeypatch)
    world["send"]("activate Live seq")
    out = call("addGraphic", {"template": "product", "values": {"name": "Keyboard 75"}, "at": 1.0, "duration": 2.5,
                              "waitSeconds": 60})
    text = out.content[0].text
    assert text.startswith("product.mov on V2 1.00-3.50 of Live seq")
    assert 'data-duration="2.5"' in seen["html"] and "Keyboard 75" in seen["html"] and seen["fps"] == 60
    assert {"index.html", "gsap.min.js", "chop.css", "chop.js"} <= set(seen["kit"])
    path = Path(text.splitlines()[1].split("file ", 1)[1])
    assert path.is_file() and path.parent.name == "chopshop graphics"
    tracks = structured(call("readEdit", {}))["tracks"]
    assert any(line.startswith("V2: #0 1.00-3.50 [0.00-2.50] product.mov") for line in tracks)
    assert not any("product.mov" in line for line in tracks if line.startswith("A"))
    over = call("addGraphic", {"template": "stat", "at": 2.0, "waitSeconds": 60}).content[0].text
    assert over.startswith("stat.mov on V3 2.00-6.00 of Live seq, a new track")


def testPreviewGraphicShowsItOverTheShotBeforeAnyRender(world, monkeypatch):
    seen = fakeHyperframes(monkeypatch)
    world["send"]("activate Live seq")
    out = call("previewGraphic", {"template": "chapter", "values": {"title": "Unboxing"}, "at": 0.5, "waitSeconds": 60})
    assert "chapter, 2.50 s at 1920x1080" in out.content[0].text and "linear light" in out.content[0].text
    assert len(seen["times"]) == 3 and seen["times"][-1] < 2.5
    assert out.content[1].type == "image"
    with pytest.raises(Exception, match="no variable headline: index, title, subtitle, accent"):
        call("previewGraphic", {"template": "chapter", "values": {"headline": "x"}, "waitSeconds": 60})


def testEditSequenceCanWorkOnACopy(world):
    world["send"]("activate Live seq")
    before = structured(call("readEdit", {"sequenceId": "live-1"}))["tracks"]
    done = structured(call("editSequence", {"operations": [{"op": "remove", "clip": "V1 #0"}], "copyAs": "Live seq v2"}))
    assert done["name"] == "Live seq v2" and done["sequenceId"] != "live-1"
    assert "Live seq v2" in world["state"]()["sequences"]
    assert structured(call("readEdit", {"sequenceId": "live-1"}))["tracks"] == before
    assert len(done["tracks"][0].split(";")) == len(before[0].split(";")) - 1
