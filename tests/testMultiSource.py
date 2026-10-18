"""several files in one edit: second camera, stills, covers, sound effects, split edits, ducking, the clip library."""

import os
import sys
import tempfile
import xml.etree.ElementTree as et
from pathlib import Path

import pytest

root = Path(tempfile.mkdtemp(prefix="chopshopMCPMulti"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import captions  # noqa: E402
import edl  # noqa: E402
import pipeline  # noqa: E402
import xmeml  # noqa: E402
from fixtures import makeColorClip, makeRush, makeSfx  # noqa: E402

FPS = 30


@pytest.fixture(scope="module")
def files():
    os.environ["APPDATA"] = str(root / "appdata")
    from PIL import Image
    meme = root / "meme face.png"
    Image.new("RGBA", (200, 200), (250, 200, 40, 255)).save(meme)
    return {"rush": makeRush(str(root / "rush.mp4")),
            "cam2": makeColorClip(str(root / "cam2.mp4"), audio="mono"),
            "meme": str(meme),
            "whoosh": makeSfx(str(root / "whoosh.wav"))}


@pytest.fixture(scope="module")
def built(files):
    os.environ["APPDATA"] = str(root / "appdata")
    plan = {"name": "multi", "sources": {"cam2": files["cam2"]}, "segments": [
        {"start": 0.5, "end": 1.5, "note": "a-roll"},
        {"media": "cam2", "start": 0.5, "end": 1.5, "jcut": 0.2, "note": "second camera"},
        {"media": files["meme"], "duration": 0.8, "note": "smash cut on a picture"},
        {"start": 2.3, "end": 3.5, "lcut": 0.3, "cover": [
            {"media": "cam2", "start": 1.0, "end": 1.6, "from": 0.2, "note": "b-roll under the voice"},
            {"media": files["whoosh"], "from": 0.0, "volume": -6},
            {"media": files["meme"], "duration": 0.5, "from": 0.6, "box": [0.6, 0.05, 0.95, 0.4]}]},
        {"start": 5.0, "end": 6.0, "cover": [
            {"media": "cam2", "start": 2.0, "duration": 0.5, "audio": "replace", "from": 0.2},
            {"media": "cam2", "start": 3.0, "duration": 0.4, "audio": "duck", "from": 0.75}]},
    ]}
    result = pipeline.buildEdit(files["rush"], plan)
    result["xml"] = et.parse(result["xmlPath"]).getroot()
    return result


def audioItems(xml, xmlTrack: int) -> list:
    track = xml.findall("./sequence/media/audio/track")[xmlTrack - 1]
    return [(int(c.find("start").text), int(c.find("end").text), int(c.find("in").text), c.find("name").text)
            for c in track.findall("clipitem")]


def testSegmentsPlayEachTheirFile(built, files):
    timeline = built["timeline"]
    assert [e.get("media", "main") for e in timeline][:2] == ["main", "cam2"]
    assert [round(e["output"][1] - e["output"][0], 2) for e in timeline] == [1.0, 1.0, 0.8, 1.2, 1.0]
    v1 = built["xml"].findall("./sequence/media/video/track")[0].findall("clipitem")
    names = [c.find("name").text for c in v1]
    assert names == ["rush", "cam2.mp4", "meme face.png", "rush", "rush"]
    urls = {f.get("id"): f.find("pathurl").text for f in built["xml"].iter("file") if f.find("pathurl") is not None}
    assert len(urls) == len(set(urls.values())) == 4  # rush, cam2, meme, whoosh: each defined once
    assert built["media"]["cam2"] == files["cam2"]


def testSoundGoesOnTracksByLayoutWithSplitEdits(built):
    roles = [(g["role"], g["tracks"]) for g in built["audioTracks"]]
    assert roles == [("rush", ["A1"]), ("rush", ["A2"]), ("cover", ["A3"])]
    xml = built["xml"]
    # the rush is stereo: two exploded xml tracks, then the mono second camera, then the covers
    rush = audioItems(xml, 1)
    assert audioItems(xml, 2) == rush
    # j cut: cam2 heard 0.2 s early; l cut: segment 3 runs 0.3 s on; the replaced 4.2-4.7 s span silences both
    assert [(a, b) for a, b, _i, _n in rush] == [(0, 24), (84, 126), (141, 150)]
    cam = audioItems(xml, 3)
    assert cam == [(24, 60, 9, "cam2.mp4")]
    covers = audioItems(xml, 4)
    assert [(a, b, n) for a, b, _i, n in covers] == [(84, 99, "whoosh.wav"), (126, 141, "cam2.mp4"), (142, 154, "cam2.mp4")]


def testLinksAndIdsHoldTogether(built):
    xml = built["xml"]
    ids = [c.get("id") for c in xml.iter("clipitem")]
    assert len(ids) == len(set(ids))
    assert all(ln.find("linkclipref").text in ids for ln in xml.iter("link"))
    # the replaced cover's picture is linked to its sound
    lanes = xml.findall("./sequence/media/video/track")
    coverItems = [c for lane in lanes[1:] for c in lane.findall("clipitem") if c.find("name").text == "cam2.mp4"]
    linked = [c for c in coverItems if any(ln.find("mediatype").text == "audio" for ln in c.findall("link"))]
    assert len(coverItems) == 3 and len(linked) == 2  # the b-roll is muted, its picture stands alone


def testCoversStackAndFrame(built):
    assert built["layers"]["covers"] == 4 and built["layers"]["coverSounds"] == 3
    lanes = built["xml"].findall("./sequence/media/video/track")
    # b-roll 3.0-3.6 s and the picture in picture 3.4-3.9 s overlap: two cover tracks
    assert len(lanes) == 3
    pip = [m for m in built["motions"] if m["track"] == 2]
    assert pip and pip[0]["from"] == {"scalePct": 31.5, "posX": 0.775, "posY": 0.225}
    still = [m for m in built["motions"] if m["track"] == 0]
    assert still and still[0]["index"] == 2 and still[0]["from"]["scalePct"] == 160.0


def testDuckAndVolumeJobs(built):
    jobs = {j["kind"]: j for j in built["audioJobs"]}
    assert jobs["duck"]["trackList"] == [0, 1]
    [(a, b, db)] = jobs["duck"]["ranges"]
    assert (a, b, db) == (pytest.approx(4.75, abs=1 / FPS), pytest.approx(5.15, abs=1 / FPS), -12.0)
    assert jobs["volume"]["clips"] == [{"track": 2, "index": 0, "db": -6.0}]


def testStoryboardShowsOtherFilesAndCovers(files):
    import storyboard
    import media
    plan = {"segments": [{"start": 0.5, "end": 1.5, "cover": [{"media": files["meme"], "duration": 0.5,
                                                                 "box": [0.6, 0.05, 0.95, 0.4]}]},
                         {"media": files["cam2"], "start": 0.5, "end": 1.5}, {"media": files["meme"], "duration": 1.0}]}
    board = storyboard.storyboard(files["rush"], plan, media.probe(files["rush"]), root / "board")
    assert board["segments"] == 3 and board["shown"] == 4


def testPlanWarnsAndSkipsWhatCannotWork(files):
    import media
    info = media.probe(files["rush"])
    norm = edl.normalizePlan({"segments": [
        {"start": 0.5, "end": 1.5, "cover": [{"media": "C:/nope.mp4"}, {"media": files["cam2"], "speed": 4, "audio": "mix"}]},
        {"media": "missing alias", "start": 0, "end": 1},
        {"media": files["cam2"], "start": 0.5, "end": 1.5, "title": {"text": "HI", "behind": True, "subject": {"box": [0.2, 0.2, 0.8, 0.8]}}},
    ]}, info, files["rush"])
    text = " ".join(norm["warnings"])
    assert "file not found" in text and "sound muted at x4" in text and "main rush only" in text
    assert len(norm["segments"]) == 2 and len(norm["segments"][0]["cover"]) == 1
    assert norm["segments"][0]["cover"][0]["audio"] == "mute"


def testCaptionsFollowWhatIsHeard():
    words = {"main": [{"text": "hello", "start": 1.0, "end": 1.4}, {"text": "there", "start": 1.6, "end": 1.9}],
             "cam2": [{"text": "yo", "start": 0.35, "end": 0.6}]}
    # cam2 heard from source 0.3 at output 0.8 (a j cut), the a-roll from 0.5 to 1.3 at output 0.0
    pieces = [{"media": "main", "in": 0.5, "out": 1.3, "start": 0.0}, {"media": "cam2", "in": 0.3, "out": 1.3, "start": 0.8}]
    out = captions.wordsOnPieces(words, pieces)
    assert [(w["text"], w["start"], w["end"]) for w in out] == [("hello", 0.5, 0.8), ("yo", 0.85, 1.1)]


def testSlotsSkipOtherFilesWithSound():
    timeline = [{"source": [0, 2], "output": [0, 2], "speed": 1, "note": "silent rush"},
                {"source": [0, 2], "output": [2, 4], "speed": 1, "note": "meme", "media": "m1"},
                {"source": [5, 7], "output": [4, 6], "speed": 1, "note": "muted b", "media": "m2", "muted": True}]
    slots = pipeline.voiceOverSlots(timeline, {"beats": []})
    assert [(s["start"], s["end"]) for s in slots] == [(0, 2), (4, 6)]


def testXmlKeepsTheSingleFileLayout():
    info = {"duration": 10.0, "hasVideo": True, "hasAudio": True, "fps": 30.0, "width": 320, "height": 180,
            "audioStreams": [{"channels": 2, "sampleRate": 48000}]}
    xml = et.fromstring(xmeml.buildXmeml("C:/r.mp4", [[1, 2], [3, 4]], info, "s").split("\n", 2)[2])
    ids = [c.get("id") for c in xml.iter("clipitem")]
    assert ids == ["clipitem-v1", "clipitem-v2", "clipitem-a1-1", "clipitem-a1-2", "clipitem-a2-1", "clipitem-a2-2"]
    first = xml.find("./sequence/media/video/track/clipitem")
    assert [ln.find("linkclipref").text for ln in first.findall("link")] == ["clipitem-v1", "clipitem-a1-1", "clipitem-a2-1"]


def testLibraryIndexSearchTagAndSheet(files, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    import shutil
    import library
    folder = tmp_path / "memes"
    (folder / "sfx").mkdir(parents=True)
    shutil.copy(files["cam2"], folder / "green guy dancing.mp4")
    shutil.copy(files["meme"], folder / "yellow face.png")
    shutil.copy(files["whoosh"], folder / "sfx" / "whoosh fast.wav")
    (folder / "notes.txt").write_text("not a clip")
    heard = lambda path: "let's go boys" if path.endswith(".mp4") else ""
    index = library.indexFolder(str(folder), transcriber=heard)
    assert [(e["id"], e["kind"]) for e in index["files"]] == [(0, "video"), (1, "image"), (2, "audio")]
    assert index["files"][0]["words"] == "let's go boys" and index["files"][1]["thumb"]
    assert library.search("whoosh")[0]["path"].endswith("whoosh fast.wav")
    assert library.search("let's go")[0]["id"] == 0
    library.tag(str(folder), [{"id": 1, "description": "shocked face, use after a fail", "tags": ["Shock", "reaction"]}])
    assert library.search("shocked reaction")[0]["id"] == 1
    assert library.search("fail", kind="video") == []
    # a new file keeps the old ids
    shutil.copy(files["meme"], folder / "another face.png")
    again = library.indexFolder(str(folder), transcriber=heard)
    byName = {e["name"]: e["id"] for e in again["files"]}
    assert byName["yellow face"] == 1 and byName["another face"] == 3
    assert again["files"][1]["description"].startswith("shocked")
    sheet = library.sheet(str(folder), tmp_path / "sheet.jpg", 0, 10)
    assert len(sheet["rows"]) == 4 and os.path.isfile(sheet["path"]) and sheet["next"] is None
    assert "shocked face" in sheet["rows"][1] and "tags shock, reaction" in sheet["rows"][1]


def overlapsOnTracks(xml) -> list:
    bad = []
    for t, track in enumerate(xml.findall("./sequence/media/audio/track"), start=1):
        items = sorted((int(c.find("start").text), int(c.find("end").text)) for c in track.findall("clipitem"))
        bad += [(t, a, b) for a, b in zip(items, items[1:]) if a[1] > b[0]]
        if not items:
            bad.append((t, "empty track"))
    return bad


@pytest.mark.parametrize("middle", ["rush", "cam2", "muted"])
def testLcutAndJcutMeetingAcrossAShortShotNeverOverlap(files, middle):
    os.environ["APPDATA"] = str(root / "appdata")
    b = {"rush": {"start": 2.0, "end": 2.5}, "cam2": {"media": files["cam2"], "start": 0.5, "end": 1.0},
         "muted": {"start": 2.0, "end": 2.5, "audio": "mute"}}[middle]
    plan = {"name": f"meet {middle}", "segments": [{"start": 0.5, "end": 1.5, "lcut": 0.4}, b, {"start": 5.0, "end": 6.0, "jcut": 0.4}]}
    built = pipeline.buildEdit(files["rush"], plan)
    xml = et.parse(built["xmlPath"]).getroot()
    assert overlapsOnTracks(xml) == []
    if middle != "muted":
        assert any("fully covered by the split edits" in w for w in built["warnings"])


def testAliasesNeverCollideWithGeneratedKeys(files, tmp_path):
    import media
    other = makeColorClip(str(tmp_path / "cam3.mp4"), color=(200, 30, 30), audio=None)
    info = media.probe(files["rush"])
    norm = edl.normalizePlan({"sources": {"_f1": files["cam2"], "rush": files["cam2"], "cam3": other},
                              "segments": [{"media": files["cam2"], "start": 0, "end": 1}, {"media": "cam3", "start": 0, "end": 1}]},
                             info, files["rush"])
    paths = {k: v["path"] for k, v in norm["media"].items()}
    assert [paths[s["media"]] for s in norm["segments"]] == [files["cam2"], other]
    assert sum("reserved name" in w for w in norm["warnings"]) == 2


def testSoundFileIsNotAPicture(files):
    import media
    norm = edl.normalizePlan({"segments": [{"start": 0.5, "end": 1.5}, {"media": files["whoosh"], "start": 0, "end": 0.4}]},
                             media.probe(files["rush"]), files["rush"])
    assert len(norm["segments"]) == 1 and any("has no picture" in w for w in norm["warnings"])


@pytest.mark.parametrize("mode", ["matte", "baked"])
def testCoverFromTheRushIsBlurredLikeTheRush(files, mode):
    os.environ["APPDATA"] = str(root / "appdata")
    plan = {"name": f"private cover {mode}", "blur": [{"box": [0.1, 0.1, 0.4, 0.4], "from": 3.0, "to": 4.5}],
            "segments": [{"media": files["cam2"], "start": 0.5, "end": 1.5,
                          "cover": [{"media": "main", "start": 3.2, "duration": 0.6, "from": 0.2}]}]}
    built = pipeline.buildEdit(files["rush"], plan, maskMode=mode)
    xml = et.parse(built["xmlPath"]).getroot()
    cover = xml.findall("./sequence/media/video/track")[1].find("clipitem")
    if mode == "matte":
        assert cover.find("sequence") is not None  # through the comp nest, blurred by premiere
    else:
        assert cover.find("name").text.startswith("baked_")  # blurred copy rendered here
