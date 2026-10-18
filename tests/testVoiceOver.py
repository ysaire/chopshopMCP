"""voice over: cleaned and laid on the edit (voice first) or placed in slots (edit first), rush ducked under it."""

import sys
import xml.etree.ElementTree as et
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ears  # noqa: E402
from fixtures import bursts, makeRush, makeVoicelessRush  # noqa: E402


def fakeVoice():
    """three spoken bursts of the fixture, the first one a retake."""
    words = []
    for (start, end), text in zip(bursts, ["bon alors", "voici le clavier", "écoutez ce son"]):
        parts = text.split()
        step = (end - start) / len(parts)
        for i, w in enumerate(parts):
            words.append({"text": w, "start": round(start + i * step, 3), "end": round(start + (i + 1) * step - 0.02, 3), "prob": 0.9})
    return {"language": "fr", "words": words, "utterances": ears.buildUtterances(words, maxGap=0.4)}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    import pipeline
    monkeypatch.setattr(pipeline, "getVoiceTranscript", lambda *a, **k: fakeVoice())
    voice = makeRush(str(tmp_path / "voice over.m4a"), withVideo=False)
    rush = makeVoicelessRush(str(tmp_path / "desk.mp4"))
    return {"pipeline": pipeline, "voice": voice, "rush": rush}


def testVoiceFirstDropsRetakesAndLaysPiecesBackToBack(world):
    layout = world["pipeline"].voiceOverLayout(world["voice"], {"dropIds": [0], "gapMs": 200}, {})
    pieces = layout["pieces"]
    assert len(pieces) == 2 and pieces[0]["at"] == 0.0
    assert pieces[0]["text"] == "voici le clavier" and pieces[1]["text"] == "écoutez ce son"
    first = pieces[0]["end"] - pieces[0]["start"]
    assert pieces[1]["at"] == pytest.approx(first + 0.2, abs=0.002)
    assert layout["words"][0]["text"] == "voici" and layout["words"][0]["start"] < 0.2
    assert layout["length"] == pytest.approx(pieces[1]["at"] + pieces[1]["end"] - pieces[1]["start"], abs=0.002)


def testEditFirstPlacesUtterancesInSlots(world):
    layout = world["pipeline"].voiceOverLayout(world["voice"], {"pieces": [{"ids": [2], "at": 1.0}, {"ids": [1], "at": 5.0}]}, {})
    assert [p["at"] for p in layout["pieces"]] == [1.0, 5.0]
    assert layout["pieces"][0]["text"] == "écoutez ce son"
    assert layout["pieces"][0]["start"] == pytest.approx(5.0 - 0.12, abs=0.001)


def testDuckRangesMergeAndSlotsAvoidSpeech():
    import pipeline
    assert pipeline.duckRanges([{"at": 0.0, "start": 1, "end": 2}, {"at": 1.3, "start": 5, "end": 6}, {"at": 4.0, "start": 0, "end": 1}]) == [[0.0, 2.3], [4.0, 5.0]]
    timeline = [{"source": [0, 4], "output": [0, 4], "speed": 1.0, "note": "unboxing"},
                {"source": [10, 12], "output": [4, 6], "speed": 1.0, "note": "talk"},
                {"source": [20, 40], "output": [6, 8.5], "speed": 8.0, "note": "timelapse"},
                {"source": [50, 51], "output": [8.5, 9.5], "speed": 1.0, "note": "short"}]
    perception = {"beats": [{"start": 9.5, "end": 12.5, "state": "speech"}]}
    slots = pipeline.voiceOverSlots(timeline, perception)
    assert [(s["start"], s["end"], s["maxWords"]) for s in slots] == [(0, 4, 10), (6, 9.5, 8)]
    assert slots[1]["onScreen"] == "timelapse; short"


def testBuildEditPutsTheVoiceUnderTheShotsWithCaptions(world):
    plan = {"name": "voiced", "segments": [{"start": 4.0, "end": 6.0}, {"start": 8.0, "end": 10.0}],
            "voiceOver": {"path": world["voice"], "dropIds": [0], "duck": -12},
            "captions": {"style": "pop"}}
    built = world["pipeline"].buildEdit(world["rush"], plan)
    assert built["voiceOver"]["pieces"][0]["at"] == 0.0 and built["duck"]["db"] == -12
    assert built["duck"]["ranges"][0][0] == 0.0 and built["duck"]["tracks"] == 1
    # captions follow the voice, not the (voiceless) rush
    assert built["layers"]["captions"] >= 3
    root = et.parse(built["xmlPath"]).getroot()
    tracks = root.findall("./sequence/media/audio/track")
    voiceTracks = [t for t in tracks if t.find("clipitem/name") is not None and t.find("clipitem/name").text == "voice over.m4a"]
    assert len(voiceTracks) == 2  # stereo voice exploded like premiere does
    items = voiceTracks[0].findall("clipitem")
    assert [i.find("start").text for i in items][0] == "0" and len(items) == 2
    ids = {c.get("id") for c in root.iter("clipitem")}
    assert all(ln.find("linkclipref").text in ids for ln in root.iter("link"))
    voiceFile = items[0].find("file")
    assert voiceFile.find("pathurl").text.endswith("voice%20over.m4a")
    assert voiceFile.find("media/audio/channelcount").text == "2"


def testVoiceRunningPastTheShotsIsFlagged(world):
    plan = {"name": "short", "segments": [{"start": 4.0, "end": 4.5}], "voiceOver": {"path": world["voice"]}}
    built = world["pipeline"].buildEdit(world["rush"], plan)
    assert any("past the last shot" in w for w in built["warnings"])
