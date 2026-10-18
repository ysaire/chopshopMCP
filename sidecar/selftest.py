"""self-test assets using every trick the editor relies on, for the panel to import in the real premiere."""

import json
from pathlib import Path

import av
import numpy as np

import captions
import masks
import media
import xmeml

sequenceName = "chopshopMCP self-test"
nestName = "chopshopMCP self-test (chop comp)"
filesName = "chopshopMCP self-test (files)"


def makeRush(path: Path, seconds: float = 3.0, fps: int = 30) -> str:
    container = av.open(str(path), mode="w")
    v = container.add_stream("libx264", rate=fps)
    v.width, v.height, v.pix_fmt = 320, 180, "yuv420p"
    a = container.add_stream("aac", rate=48000)
    a.layout = "stereo"
    rng = np.random.default_rng(1)
    for i in range(int(seconds * fps)):
        img = rng.integers(60, 200, (180, 320, 3)).astype(np.uint8)
        img[60:120, 40 + i * 2:100 + i * 2] = 240
        for packet in v.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
            container.mux(packet)
    for packet in v.encode():
        container.mux(packet)
    n = int(seconds * 48000)
    tone = (0.2 * np.sin(2 * np.pi * 440 * np.arange(n) / 48000)).astype(np.float32)
    stereo = np.stack([tone, tone])
    for i in range(0, n, 1024):
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(stereo[:, i:i + 1024]), format="fltp", layout="stereo")
        frame.sample_rate = 48000
        for packet in a.encode(frame):
            container.mux(packet)
    for packet in a.encode():
        container.mux(packet)
    container.close()
    return str(path)


def makeSecondCamera(path: Path, seconds: float = 3.0, fps: int = 30) -> str:
    """green, mono sound: a file with another audio layout than the rush."""
    container = av.open(str(path), mode="w")
    v = container.add_stream("libx264", rate=fps)
    v.width, v.height, v.pix_fmt = 320, 180, "yuv420p"
    a = container.add_stream("aac", rate=48000)
    a.layout = "mono"
    for i in range(int(seconds * fps)):
        img = np.zeros((180, 320, 3), dtype=np.uint8)
        img[:, :] = (40, 170, 70)
        img[70:110, i * 3:i * 3 + 30] = 250
        for packet in v.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
            container.mux(packet)
    for packet in v.encode():
        container.mux(packet)
    n = int(seconds * 48000)
    tone = (0.2 * np.sin(2 * np.pi * 660 * np.arange(n) / 48000)).astype(np.float32).reshape(1, -1)
    for i in range(0, n, 1024):
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(tone[:, i:i + 1024]), format="fltp", layout="mono")
        frame.sample_rate = 48000
        for packet in a.encode(frame):
            container.mux(packet)
    for packet in a.encode():
        container.mux(packet)
    container.close()
    return str(path)


def buildFiles(out: Path, rush: str, info: dict, fps: float = 30.0) -> dict:
    """the rush, a second camera heard 0.2 s before its picture (j cut), a still, and the second camera as a linked cover."""
    from PIL import Image
    second = makeSecondCamera(out / "selftest cam2.mp4")
    still = out / "selftest still.png"
    Image.new("RGBA", (160, 160), (250, 200, 40, 255)).save(still)
    infos = {"main": info, "cam2": media.probe(second), "still": media.probe(str(still))}
    segments = [{"start": 0.5, "end": 1.5, "speed": 1.0, "audio": "keep"},
                {"start": 0.5, "end": 1.5, "speed": 1.0, "audio": "keep", "media": "cam2"},
                {"start": 0.0, "end": 0.5, "speed": 1.0, "audio": "mute", "media": "still", "still": True}]
    clips = xmeml.toClips(segments, fps)
    pieces = {0: [{"start": 0, "end": 24, "in": 15, "out": 39}], 1: [{"start": 24, "end": 60, "in": 9, "out": 45}]}
    groups = xmeml.clipAudioGroups(clips, infos, pieces)
    cover = {"media": "cam2", "start": 30, "end": 45, "in": 60, "out": 75, "link": "c1"}
    groups.append({"layout": xmeml.audioLayout(infos["cam2"]), "items": [cover], "role": "cover"})
    layers = [[dict(cover, kind="media")]]
    xmlPath = out / "selftest files.xml"
    sources = {"cam2": {"path": second, "info": infos["cam2"]}, "still": {"path": str(still), "info": infos["still"]}}
    xmeml.writeXmeml(str(xmlPath), rush, segments, info, filesName, fps=fps, width=320, height=180,
                     clipName="selftest rush", layers=layers, sources=sources, audioGroups=groups)
    return {"xmlPath": str(xmlPath), "sequenceName": filesName, "second": second, "still": str(still),
            "expect": {"v1": 3, "jcutStart": 0.8, "jcutIn": 0.3}}


def build(outDir: str) -> dict:
    out = Path(outDir)
    out.mkdir(parents=True, exist_ok=True)
    rush = makeRush(out / "selftest rush.mp4")
    info = media.probe(rush)
    fps = 30.0
    zone = masks.normalizeZone({"box": [0.1, 0.2, 0.5, 0.8], "from": 0.0, "to": 3.0}, 3.0)
    matte = masks.renderMatte(rush, 0.5, 1.5, out, zones=[zone])
    caption = captions.renderCaption({"words": ["SELF", "TEST"], "highlight": None}, captions.normalizeStyle("pop"), 320, 180, out)
    segments = [{"start": 0.5, "end": 1.5, "speed": 1.0, "audio": "keep", "nest": "comp"},
                {"start": 1.5, "end": 2.5, "speed": 2.0, "audio": "mute"}]
    fileFrames = int(round(info["duration"] * fps))
    nests = {"comp": {"name": nestName, "width": 320, "height": 180, "lanes": [
        {"items": [{"kind": "source", "start": 0, "end": fileFrames, "in": 0, "out": fileFrames}]},
        {"items": [{"kind": "source", "start": 15, "end": 45, "in": 15, "out": 45, "filters": [xmeml.gaussianBlurFilter(40)]}]},
        {"items": [{"path": matte["path"], "start": 15, "end": 45, "in": 0, "out": 30, "width": 320, "height": 180,
                    "frames": matte["frames"]}]}]}}
    layers = [[{"path": caption["path"], "start": 0, "end": 30, "width": caption["width"], "height": caption["height"], "frames": None}]]
    xmlPath = out / "selftest.xml"
    xmeml.writeXmeml(str(xmlPath), rush, segments, info, sequenceName, fps=fps, width=320, height=180,
                     clipName="selftest rush", layers=layers, nests=nests)
    return {
        "xmlPath": str(xmlPath), "rush": rush, "sequenceName": sequenceName, "nestName": nestName,
        "matteEffects": [{"name": nestName, "effects": [
            {"track": 1, "index": 0, "effect": "Gaussian Blur", "params": [{"name": "Blurriness", "index": 0, "value": 40},
                                                                       {"name": "Repeat Edge Pixels", "index": 2, "value": True}]},
            {"track": 1, "index": 0, "effect": "Track Matte Key", "params": [{"name": "Matte", "index": 0, "matteTrack": 3}]}]}],
        "expect": {"mainVideoTracks": 2, "mainClipsV1": 2, "nestVideoTracks": 3, "speed": 2.0},
        "files": buildFiles(out, rush, info, fps),
    }


if __name__ == "__main__":
    import sys
    print(json.dumps(build(sys.argv[1] if len(sys.argv) > 1 else "selftest")))
