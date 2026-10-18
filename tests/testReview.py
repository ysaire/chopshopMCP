"""watching a rendered edit: presets, loudness, black, frozen and silent stretches, cuts, the shot sheet."""

import sys
from pathlib import Path

import av
import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))

import review  # noqa: E402

fps = 30
rate = 48000


def sine(seconds: float, amplitude: float = 0.1, freq: float = 997.0) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    tone = amplitude * np.sin(2 * np.pi * freq * t)
    return np.stack([tone, tone])


def scene(rng, base, t: float) -> np.ndarray:
    img = np.zeros((180, 320, 3), dtype=np.int16)
    img[:, :] = base
    img += rng.integers(-3, 4, img.shape)
    x = int(20 + t * 40) % 260
    img[60:110, x:x + 50] = 235
    return np.clip(img, 0, 255).astype(np.uint8)


def makeRender(path: str) -> str:
    """6 s: gray 0-1, black 1-1.5, green 1.5-3, still blue 3-4.5, yellow 4.5-6; a -20 dbfs tone, silent 2-3.5."""
    rng = np.random.default_rng(7)
    still = scene(rng, (30, 30, 200), 0.0)
    container = av.open(path, mode="w")
    vStream = container.add_stream("libx264", rate=fps)
    vStream.width, vStream.height, vStream.pix_fmt = 320, 180, "yuv420p"
    aStream = container.add_stream("aac", rate=rate)
    aStream.layout = "stereo"
    for i in range(6 * fps):
        t = i / fps
        if t < 1.0:
            img = scene(rng, (100, 100, 100), t)
        elif t < 1.5:
            img = np.zeros((180, 320, 3), dtype=np.uint8)
        elif t < 3.0:
            img = scene(rng, (40, 120, 40), t)
        elif t < 4.5:
            img = still
        else:
            img = scene(rng, (200, 180, 40), t)
        for packet in vStream.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
            container.mux(packet)
    for packet in vStream.encode():
        container.mux(packet)
    audio = sine(6.0)
    audio[:, int(2.0 * rate):int(3.5 * rate)] = 0.0
    audio = audio.astype(np.float32)
    for i in range(0, audio.shape[1], 1024):
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(audio[:, i:i + 1024]), format="fltp", layout="stereo")
        frame.sample_rate = rate
        for packet in aStream.encode(frame):
            container.mux(packet)
    for packet in aStream.encode():
        container.mux(packet)
    container.close()
    return path


@pytest.fixture(scope="module")
def render(tmp_path_factory):
    path = makeRender(str(tmp_path_factory.mktemp("review") / "edit.mp4"))
    return path, review.scanMedia(path)


def presetTree(root: Path, year: int) -> Path:
    folder = root / f"Adobe Premiere Pro {year}" / "MediaIO" / "systempresets" / review.h264Folder
    folder.mkdir(parents=True)
    for name in review.presetNames.values():
        (folder / name).write_text("<epr/>")
    return folder


def testPresetForPicksTheInstallOfThePremiereThatRuns(tmp_path):
    presetTree(tmp_path, 2025)
    newer = presetTree(tmp_path, 2026)
    assert review.presetFor("review", "26.0.1", roots=[str(tmp_path)]) == str(newer / review.presetNames["review"])
    assert review.presetFor("master", "26.0.1", roots=[str(tmp_path)]).endswith("01 - Match Source - High bitrate.epr")
    assert review.presetFor("review", "25.0.0", roots=[str(tmp_path)]).startswith(str(tmp_path / "Adobe Premiere Pro 2025"))


def testPresetForSaysWhereItLookedWhenThePresetIsMissing(tmp_path):
    with pytest.raises(ValueError, match="Adobe Premiere Pro 2027"):
        review.presetFor("review", "27.0.0", roots=[str(tmp_path)])
    with pytest.raises(ValueError, match="review or master"):
        review.presetFor("draft", "25.0.0", roots=[str(tmp_path)])


def testLoudnessOfAStereoToneFollowsBs1770():
    assert review.integratedLoudness(sine(10.0), rate) == pytest.approx(-20.0, abs=0.1)
    # one channel only: half the power
    mono = sine(10.0)
    mono[1] = 0.0
    assert review.integratedLoudness(mono, rate) == pytest.approx(-23.01, abs=0.1)


def testSilenceIsGatedOutOfTheLoudness():
    padded = np.concatenate([sine(6.0), np.zeros((2, 6 * rate))], axis=1)
    # ungated, six seconds of silence would pull it to -23; the blocks straddling the end still count
    assert review.integratedLoudness(padded, rate) == pytest.approx(-20.11, abs=0.03)
    assert review.integratedLoudness(np.zeros((2, rate)), rate) is None


def near(found: list, expected: list, tol: float):
    assert len(found) == len(expected), found
    for (s, e), (es, ee) in zip(found, expected):
        assert s == pytest.approx(es, abs=tol) and e == pytest.approx(ee, abs=tol), found


def testScanReadsTheFormat(render):
    _path, report = render
    assert report["duration"] == pytest.approx(6.0, abs=0.05)
    assert (report["width"], report["height"], round(report["fps"])) == (320, 180, 30)


def testScanFindsTheBlackStretch(render):
    near(render[1]["black"], [(1.0, 1.5)], 1.5 / fps)


def testScanFindsTheFrozenStretchButNotTheMovingOnes(render):
    near(render[1]["frozen"], [(3.0, 4.5)], 2.5 / fps)


def testScanFindsTheSilence(render):
    near(render[1]["silent"], [(2.0, 3.5)], 0.06)


def testScanFindsEveryCutAndNoMovementAsACut(render):
    cuts = render[1]["cuts"]
    assert cuts == pytest.approx([1.0, 1.5, 3.0, 4.5], abs=1.5 / fps)
    shots = render[1]["shots"]
    assert [round(s["start"], 1) for s in shots] == [0.0, 1.0, 1.5, 3.0, 4.5]
    assert shots[-1]["end"] == pytest.approx(6.0, abs=0.05)


def testScanMeasuresTheMix(render):
    report = render[1]
    assert report["loudness"] == pytest.approx(-20.0, abs=0.5)
    # aac overshoots where the tone stops and starts again
    assert -20.5 < report["peak"] < -18.0


def testShotSheetHasOneLabelledCellPerShot(render, tmp_path):
    path, report = render
    out = review.shotSheet(path, report["shots"], str(tmp_path / "sheet.jpg"))
    sheet = Image.open(out)
    assert sheet.width == min(review.sheetColumns, len(report["shots"])) * review.cellWidth
    rows = -(-len(report["shots"]) // review.sheetColumns)
    assert sheet.height >= rows * review.cellWidth * 180 // 320


def testShotSheetSamplesALongEditDownToTheCellLimit(render, tmp_path):
    path, _report = render
    shots = [{"start": i * 0.1, "end": (i + 1) * 0.1} for i in range(60)]
    picked = review.sheetShots(shots, 24)
    assert len(picked) == 24 and picked[0] is shots[0] and picked[-1] is shots[-1]
    out = review.shotSheet(path, shots, str(tmp_path / "long.jpg"), maxCells=24)
    assert Path(out).is_file()


def testDescribeGivesRhythmProblemsAndTheLoudnessVerdict():
    report = {"duration": 6.0, "width": 320, "height": 180, "fps": 30.0, "cuts": [1.0, 1.5, 3.0, 4.5],
              "shots": [{"start": a, "end": b} for a, b in [(0, 1), (1, 1.5), (1.5, 3), (3, 4.5), (4.5, 6)]],
              "black": [(1.0, 1.5)], "frozen": [(3.0, 4.5)], "silent": [(2.0, 3.5)], "loudness": -20.0, "peak": -20.0}
    text = review.describe(report)
    assert "5 shots" in text and "longest 1.5 s" in text
    assert "black 00:01.0-00:01.5" in text
    assert "frozen 00:03.0-00:04.5" in text
    assert "silent 00:02.0-00:03.5" in text
    assert "-20.0 LUFS" in text and "6.0 dB under" in text


def testDescribeSaysWhenNothingIsWrong():
    clean = {"duration": 10.0, "width": 1920, "height": 1080, "fps": 60.0, "cuts": [5.0],
             "shots": [{"start": 0.0, "end": 5.0}, {"start": 5.0, "end": 10.0}],
             "black": [], "frozen": [], "silent": [], "loudness": -14.2, "peak": -1.5}
    text = review.describe(clean)
    assert "no black, frozen or silent stretch" in text
    assert "on target" in text


def makeSameRoomRender(path: str) -> str:
    """60 fps, each picture shown twice as obs records, two shots of one desk cut at 2 s: a small, sudden change."""
    container = av.open(path, mode="w")
    stream = container.add_stream("libx264", rate=60)
    stream.width, stream.height, stream.pix_fmt = 320, 180, "yuv420p"
    xs = np.linspace(0, 1, 320)[None, :, None]
    ys = np.linspace(0, 1, 180)[:, None, None]
    for i in range(4 * 60):
        t = (i // 2) * 2 / 60
        ramp = xs if t < 2.0 else ys
        img = np.broadcast_to(80 + 40 * ramp, (180, 320, 3)).astype(np.uint8).copy()
        x = int(20 + t * 30)
        img[70:110, x:x + 40] = 150
        for packet in stream.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return path


def testScanFindsACutBetweenTwoShotsOfTheSameRoom(tmp_path):
    report = review.scanMedia(makeSameRoomRender(str(tmp_path / "room.mp4")))
    assert report["cuts"] == pytest.approx([2.0], abs=1.5 / 60)
    assert report["frozen"] == [] and report["black"] == []


def testStretchesCloserThanAQuarterSecondMerge():
    assert review.mergeRuns([(1.7, 3.0), (3.3, 4.5), (4.5, 7.3), (7.5, 8.0)], 0.25) == [(1.7, 3.0), (3.3, 8.0)]
    assert review.mergeRuns([], 0.25) == []


def testDescribeSummarisesAMostlySilentMix():
    stretches = [(i * 4.0, i * 4.0 + 2.5) for i in range(8)]
    report = {"duration": 35.0, "width": 1920, "height": 1080, "fps": 60.0, "cuts": [], "shots": [{"start": 0.0, "end": 35.0}],
              "black": [], "frozen": [], "silent": stretches, "loudness": -36.8, "peak": 0.3}
    text = review.describe(report)
    assert "silent 20.0 s of 35.1 s" not in text
    assert "silent 20.0 s of 35.0 s in 8 stretches, longest 2.5 s at 00:00.0" in text
    assert "00:04.0" not in text
