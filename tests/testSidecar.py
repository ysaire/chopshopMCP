import os
import sys
import tempfile
import xml.etree.ElementTree as et
from pathlib import Path

import pytest

testRoot = Path(tempfile.mkdtemp(prefix="chopshopMCPTest"))
os.environ["APPDATA"] = str(testRoot / "appdata")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cutList  # noqa: E402
import ears  # noqa: E402
import eyes  # noqa: E402
import pipeline  # noqa: E402
import xmeml  # noqa: E402
from fixtures import bursts, makeRush  # noqa: E402


@pytest.fixture(scope="module")
def rushPath():
    return makeRush(str(testRoot / "rush test.mp4"))


def testMergeAndSubtract():
    assert cutList.mergeRanges([[3, 4], [0, 1], [0.9, 2]]) == [[0, 2], [3, 4]]
    assert cutList.subtractRanges([[0, 10]], [[2, 3], [5, 6]]) == [[0, 2], [3, 5], [6, 10]]
    assert cutList.invertRanges([[1, 2]], 5) == [[0, 1], [2, 5]]


def testKeepRangesDropsTakesWithoutBleed():
    utterances = [
        {"id": 0, "start": 1.0, "end": 2.0, "text": "alors le"},
        {"id": 1, "start": 2.05, "end": 4.0, "text": "alors le truc c'est"},
        {"id": 2, "start": 4.5, "end": 6.0, "text": "suite"},
    ]
    keep = cutList.buildKeepRanges(10.0, [], utterances, dropIds=[0], padSec=0.12)
    # padding of take 1 must not reach back into dropped take 0
    assert keep[0][0] >= 2.0
    assert all(not (s < 2.0 < e) for s, e in keep)


def testSilencesInsideSpeechAreRemoved():
    keep = cutList.buildKeepRanges(10.0, [[3.0, 5.0]], None, padSec=0.1)
    assert keep == [[0.0, 3.1], [4.9, 10.0]]


def testMarkersMergeContiguousDrops():
    utterances = [{"id": i, "start": i, "end": i + 0.9, "text": f"t{i}"} for i in range(5)]
    drops = [{"id": 1, "reason": "badTake"}, {"id": 2, "reason": "badTake"}, {"id": 4, "reason": "meta"}]
    markers = cutList.dropsToMarkers(utterances, drops)
    assert len(markers) == 2
    assert markers[0]["start"] == 1 and markers[0]["end"] == 2.9


def testRateFromFps():
    assert xmeml.rateFromFps(29.97) == (30, True, 30 * 1000 / 1001)
    assert xmeml.rateFromFps(23.976)[:2] == (24, True)
    assert xmeml.rateFromFps(25.0) == (25, False, 25.0)
    assert round(xmeml.fpsFromTicks(8475667200), 3) == 29.970


def testPathToUrl():
    assert xmeml.pathToUrl(r"C:\Users\me\Rush 01.mp4") == "file://localhost/C%3a/Users/me/Rush%2001.mp4"
    assert xmeml.pathToUrl(r"\\nas\rushes\a.mp4") == "file://nas/rushes/a.mp4"


def testClipsAreContiguous():
    clips = xmeml.toClips([[0.5, 1.5], [2.3, 3.5]], 30.0)
    assert {k: clips[0][k] for k in ("in", "out", "start", "end")} == {"in": 15, "out": 45, "start": 0, "end": 30}
    assert clips[1]["start"] == clips[0]["end"]


def testProbe(rushPath):
    info = pipeline.getProbe(rushPath)
    assert info["hasVideo"] and info["hasAudio"]
    assert abs(info["duration"] - 7.0) < 0.2
    assert round(info["fps"]) == 30
    assert info["channels"] == 2 and info["sampleRate"] == 48000
    assert (info["width"], info["height"]) == (320, 180)


def testSilencesMatchBursts(rushPath):
    result = ears.detectSilences(rushPath, None, 350)
    sil = result["silences"]
    expected = [(0.0, 0.5), (1.5, 2.3), (3.5, 5.0), (6.0, 7.0)]
    assert len(sil) == len(expected), sil
    for (s, e), (es, ee) in zip(sil, expected):
        assert abs(s - es) < 0.08 and abs(e - ee) < 0.12, (s, e, es, ee)


def testSilencesModeWritesImportableXml(rushPath):
    result = pipeline.roughCut(rushPath, mode="silences", options={"padMs": 100})
    keep = result["keepRanges"]
    assert len(keep) == len(bursts)
    for (s, e), (bs, be) in zip(keep, bursts):
        assert abs(s - (bs - 0.1)) < 0.1 and abs(e - (be + 0.1)) < 0.15

    tree = et.parse(result["xmlPath"])
    root = tree.getroot()
    assert root.tag == "xmeml"
    vClips = root.findall("./sequence/media/video/track/clipitem")
    aClips = root.findall("./sequence/media/audio/track/clipitem")
    # one stereo source track explodes into l + r xml tracks, like premiere's own export
    assert len(vClips) == 3 and len(aClips) == 6
    # back to back on the timeline
    for prev, cur in zip(vClips, vClips[1:]):
        assert prev.find("end").text == cur.find("start").text
    # full file definition only once, references afterwards
    fullFiles = [f for f in root.iter("file") if f.find("pathurl") is not None]
    assert len(fullFiles) == 1
    assert fullFiles[0].find("pathurl").text.endswith("rush%20test.mp4")
    assert root.find("./sequence/media/audio/track").get("premiereTrackType") == "Stereo"
    assert int(root.find("./sequence/duration").text) == int(vClips[-1].find("end").text)


def testActiveSequenceSettingsWin(rushPath):
    # vertical 1080x1920 sequence at 29.97 given by premiere
    result = pipeline.roughCut(rushPath, mode="silences",
                               options={"ticksPerFrame": "8475667200", "width": 1080, "height": 1920})
    root = et.parse(result["xmlPath"]).getroot()
    sc = root.find("./sequence/media/video/format/samplecharacteristics")
    assert sc.find("width").text == "1080" and sc.find("height").text == "1920"
    assert root.find("./sequence/rate/ntsc").text == "TRUE"


def fakeTranscript():
    words = []
    for (start, end), text in zip(bursts, ["alors le", "alors le truc", "euh"]):
        parts = text.split()
        step = (end - start) / len(parts)
        for i, w in enumerate(parts):
            words.append({"text": w, "start": round(start + i * step, 3), "end": round(start + (i + 1) * step, 3), "prob": 0.9})
    return {"language": "fr", "duration": 7.0, "words": words, "utterances": ears.buildUtterances(words)}


def testManualModeDropsWhatClaudePicked(rushPath, monkeypatch):
    transcript = fakeTranscript()
    monkeypatch.setattr(pipeline, "getTranscript", lambda *a, **k: transcript)
    assert [u["text"] for u in transcript["utterances"]] == ["alors le", "alors le truc", "euh"]
    result = pipeline.roughCut(rushPath, mode="manual", options={"removeFillers": True}, dropIds=[0])
    keep = result["keepRanges"]
    # false start gone, filler-only burst gone, the good take stays
    assert len(keep) == 1
    assert keep[0][0] >= 2.1 and keep[0][1] <= 3.7
    assert Path(result["xmlPath"]).exists()


def testUtteranceSplitOnPauses():
    words = [{"text": "a", "start": 0, "end": 0.2}, {"text": "b.", "start": 0.3, "end": 0.5},
             {"text": "c", "start": 0.6, "end": 0.8}, {"text": "d", "start": 2.0, "end": 2.2}]
    utts = ears.buildUtterances(words)
    assert [u["text"] for u in utts] == ["a b.", "c", "d"]


def testFillerDetection():
    assert ears.isFiller("Euh,", "fr")
    assert ears.isFiller("um", None)
    assert not ears.isFiller("alors", "fr")


def testGrabFramesSeesTheColorChange(rushPath):
    frames = eyes.grabFrames(rushPath, [1.0, 4.0], str(testRoot / "frames"))
    assert len(frames) == 2
    from PIL import Image
    first = Image.open(frames[0]["path"]).convert("RGB").getpixel((10, 10))
    second = Image.open(frames[1]["path"]).convert("RGB").getpixel((10, 10))
    assert first[0] > first[2] and second[2] > second[0]


def testXmlMatchesPremiereOwnExport():
    """ground truth: premiere 25 export of an obs rush, 60p, two stereo audio streams."""
    ref = et.parse(Path(__file__).parent / "data" / "premiere2025Export.xml").getroot()
    info = {
        "duration": 94158 / 60, "hasVideo": True, "hasAudio": True, "fps": 60.0,
        "width": 1920, "height": 1080, "sampleRate": 48000, "channels": 2,
        "audioStreams": [{"index": 1, "channels": 2, "sampleRate": 48000}, {"index": 2, "channels": 2, "sampleRate": 48000}],
    }
    content = xmeml.buildXmeml(r"C:\Users\me\Videos\rush.mp4", [[0.0, 94158 / 60]], info, "unboxing", clipName="unboxing")
    ours = et.fromstring(content.encode("utf-8"))

    def lanes(root):
        out = []
        for tr in root.findall("./sequence/media/audio/track"):
            item = tr.find("clipitem")
            if item is None:
                continue
            out.append((tr.get("premiereTrackType"), tr.get("currentExplodedTrackIndex"), tr.get("totalExplodedTrackCount"),
                        item.get("premiereChannelType"), item.find("sourcetrack/trackindex").text,
                        tr.find("outputchannelindex").text))
        return out

    assert lanes(ours) == lanes(ref)
    vOurs = ours.find("./sequence/media/video/track/clipitem")
    vRef = ref.find("./sequence/media/video/track/clipitem")
    for tag in ["in", "out", "start", "end", "pproTicksIn", "pproTicksOut", "duration", "name"]:
        assert vOurs.find(tag).text == vRef.find(tag).text, tag
    assert len(vOurs.findall("link")) == len(vRef.findall("link"))
    assert ours.find(".//file/pathurl").text == ref.find(".//file/pathurl").text
    assert [a.find("channelcount").text for a in ours.findall(".//file/media/audio")] == \
        [a.find("channelcount").text for a in ref.findall(".//file/media/audio")]
    assert ours.find("./sequence/rate/timebase").text == "60" and ours.find("./sequence/rate/ntsc").text == "FALSE"


def testVoiceTrackPicksTheRightStream(tmp_path):
    import av
    import numpy as np
    from media import decodeAudioMono
    # a2 silent, a1 tone: listening to a2 must hear nothing
    path = str(tmp_path / "twoTracks.mp4")
    c = av.open(path, "w")
    streams = [c.add_stream("aac", rate=48000), c.add_stream("aac", rate=48000)]
    for st in streams:
        st.layout = "stereo"
    n = 48000
    tone = (0.3 * np.sin(2 * np.pi * 220 * np.arange(n) / 48000)).astype(np.float32)
    for st, sig in zip(streams, [tone, np.zeros(n, dtype=np.float32)]):
        for i in range(0, n, 1024):
            fr = av.AudioFrame.from_ndarray(np.ascontiguousarray(np.stack([sig[i:i + 1024]] * 2)), format="fltp", layout="stereo")
            fr.sample_rate = 48000
            for pk in st.encode(fr):
                c.mux(pk)
        for pk in st.encode():
            c.mux(pk)
    c.close()
    a1 = decodeAudioMono(path, 16000, track=1)
    a2 = decodeAudioMono(path, 16000, track=2)
    assert np.abs(a1).max() > 0.1 and np.abs(a2).max() < 0.01
    info = pipeline.getProbe(path)
    assert len(info["audioStreams"]) == 2


def testKindOfTellsVideoSoundPictureAndMissingFiles(rushPath, tmp_path):
    import media
    from PIL import Image
    video = media.kindOf(rushPath)
    assert video["kind"] == "video" and video["duration"] == pytest.approx(7.0, abs=0.05) and video["size"] == [320, 180]
    sound = media.kindOf(makeRush(str(tmp_path / "voice over.m4a"), withVideo=False))
    assert sound["kind"] == "audio" and sound["duration"] == pytest.approx(7.0, abs=0.05) and "size" not in sound
    still = tmp_path / "logo.png"
    Image.new("RGB", (64, 32)).save(still)
    assert media.kindOf(str(still)) == {"kind": "image", "size": [64, 32]}
    assert media.kindOf(str(tmp_path / "gone.mp4")) == {"kind": "missing"}
