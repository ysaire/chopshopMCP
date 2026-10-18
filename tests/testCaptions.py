"""animated captions: words on the edit, chunks, stills in the user's font, presets replayed on each."""

import sys
import xml.etree.ElementTree as et
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import captions  # noqa: E402


def words(text, step=0.3, gapAt=None):
    out, t = [], 0.0
    for i, w in enumerate(text.split()):
        if gapAt is not None and i == gapAt:
            t += 1.0
        out.append({"text": w, "start": round(t, 3), "end": round(t + step * 0.8, 3)})
        t += step
    return out


def testStylesMergeBuiltinsConfigAndOverrides():
    configured = {"finzar": {"base": "pop", "font": "Montserrat-Black", "preset": "Transform 0 (nice pop in)", "words": 3}}
    style = captions.normalizeStyle("finzar", configured)
    assert (style["mode"], style["words"], style["preset"], style["uppercase"]) == ("pop", 3, "Transform 0 (nice pop in)", True)
    assert captions.normalizeStyle({"base": "karaoke", "highlight": "#00FF00"})["highlight"] == "#00FF00"
    assert captions.normalizeStyle("nope")["mode"] == "pop"


def testChunksBreakOnCountPunctuationAndPauses():
    pop = captions.normalizeStyle("pop")
    cues = captions.chunkWords(words("alors ce clavier, il est fou", gapAt=4), pop)
    assert [c["text"] for c in cues] == ["alors ce", "clavier,", "il", "est fou"]
    assert all(a["end"] <= b["start"] for a, b in zip(cues, cues[1:]))
    line = captions.chunkWords(words("alors ce clavier, il est fou"), captions.normalizeStyle("line"))
    assert [c["text"] for c in line] == ["alors ce clavier, il est fou"]
    karaoke = captions.chunkWords(words("un deux trois"), captions.normalizeStyle("karaoke"))
    assert [(c["highlight"], c["first"]) for c in karaoke] == [(0, True), (1, False), (2, False)]
    assert karaoke[0]["start"] == 0.0 and karaoke[1]["start"] == 0.3


def testWordsFollowTheEditAndSkipTimelapses():
    segments = [{"start": 10.0, "end": 11.0, "speed": 1.0}, {"start": 2.0, "end": 6.0, "speed": 4.0}, {"start": 0.0, "end": 1.0, "speed": 1.0}]
    clips = [{"segment": 0, "start": 0}, {"segment": 1, "start": 30}, {"segment": 2, "start": 60}]
    source = [{"text": "one", "start": 10.2, "end": 10.5}, {"text": "fast", "start": 3.0, "end": 3.2},
              {"text": "two", "start": 0.1, "end": 0.4}, {"text": "cut", "start": 11.5, "end": 11.8}]
    out = captions.wordsOnTimeline(source, segments, clips, 30.0)
    assert out == [{"text": "one", "start": 0.2, "end": 0.5}, {"text": "two", "start": 2.1, "end": 2.4}]


def testExplicitCuesSpreadWordsForKaraoke():
    cues = captions.cuesFromText([{"start": 1.0, "end": 2.0, "text": "a b c d"}], captions.normalizeStyle("karaoke"))
    assert [c["start"] for c in cues] == [1.0, 1.25, 1.5, 1.75] and cues[-1]["end"] == 2.0


def testRenderedStillIsTightHighlightedAndCached(tmp_path):
    style = captions.normalizeStyle({"base": "karaoke", "highlight": "#FF0000", "color": "#FFFFFF", "stroke": 0.0})
    cue = {"words": ["hello", "world"], "highlight": 1}
    info = captions.renderCaption(cue, style, 1080, 1920, tmp_path)
    import numpy as np
    from PIL import Image
    img = Image.open(info["path"]).convert("RGBA")
    assert img.size == (info["width"], info["height"]) and img.width < 1080 and info["posY"] == style["y"]
    px = np.asarray(img).reshape(-1, 4)
    px = px[px[:, 3] == 255]
    reds = int(((px[:, 0] > 200) & (px[:, 1] < 60)).sum())
    whites = int((px[:, :3].min(axis=1) > 200).sum())
    assert reds > 100 and whites > 100
    assert captions.renderCaption(cue, style, 1080, 1920, tmp_path)["path"] == info["path"]


def testBuildEditPutsCaptionsOnTopWithAnimation(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    import pipeline
    from fixtures import makeVoicelessRush
    path = makeVoicelessRush(str(tmp_path / "desk.mp4"))
    plan = {"name": "capped", "format": "vertical", "segments": [{"start": 1.0, "end": 3.0}, {"start": 4.0, "end": 6.0}],
            "captions": {"style": "karaoke", "preset": "Pop In",
                         "cues": [{"start": 0.0, "end": 1.0, "text": "listen to"}, {"start": 2.2, "end": 3.0, "text": "this"}]}}
    built = pipeline.buildEdit(path, plan)
    assert built["layers"]["captions"] == 3
    root = et.parse(built["xmlPath"]).getroot()
    lane = root.findall("./sequence/media/video/track")[-1].findall("clipitem")
    assert [(c.find("start").text, c.find("end").text) for c in lane] == [("0", "15"), ("15", "30"), ("66", "90")]
    assert all(c.find("file/pathurl").text.endswith(".png") for c in lane)
    motions = [m for m in built["motions"] if m["track"] == 1]
    assert len(motions) == 3 and motions[0]["from"]["posY"] == captions.normalizeStyle("karaoke")["y"]
    # the pop in replays on the first sub-cue of each line only
    assert built["presetJobs"] == [{"preset": "Pop In", "track": 1, "indexes": [0, 2]}]


def testMogrtControlsAreReadFromTheTemplate(tmp_path, monkeypatch):
    import json
    import zipfile
    import mogrt
    folder = tmp_path / "Adobe" / "Common" / "Motion Graphics Templates"
    folder.mkdir(parents=True)
    with zipfile.ZipFile(folder / "Pop Title.mogrt", "w") as z:
        z.writestr("definition.json", json.dumps({"clientControls": [
            {"id": "a", "type": 6, "label": {"strDB": [{"localeString": "en_US", "str": "Main Text"}]}},
            {"id": "b", "type": 4, "label": "Fill"}]}))
    (folder / "broken.mogrt").write_text("nope")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    templates = mogrt.listTemplates()
    assert [t["name"] for t in templates] == ["Pop Title", "broken"]
    assert templates[0]["controls"] == [{"label": "Main Text", "type": 6}, {"label": "Fill", "type": 4}]
    assert templates[1]["controls"] == []
    assert mogrt.find(templates, "pop")["name"] == "Pop Title"
