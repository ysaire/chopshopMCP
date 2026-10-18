"""synthetic rushes, presets and reference edits for the tests."""

import av
import numpy as np

# speech-like bursts (start, end) in seconds, silence everywhere else
bursts = [(0.5, 1.5), (2.3, 3.5), (5.0, 6.0)]
totalSec = 7.0


def makeRush(path: str, fps: int = 30, sampleRate: int = 48000, withVideo: bool = True):
    """7 s: tone bursts between silences, red then blue from 3 s."""
    container = av.open(path, mode="w")
    vStream = None
    if withVideo:
        vStream = container.add_stream("libx264", rate=fps)
        vStream.width, vStream.height, vStream.pix_fmt = 320, 180, "yuv420p"
    aStream = container.add_stream("aac", rate=sampleRate)
    aStream.layout = "stereo"

    if vStream is not None:
        for i in range(int(totalSec * fps)):
            t = i / fps
            color = (200, 40, 40) if t < 3.0 else (40, 40, 200)
            img = np.zeros((180, 320, 3), dtype=np.uint8)
            img[:, :] = color
            frame = av.VideoFrame.from_ndarray(img, format="rgb24")
            for packet in vStream.encode(frame):
                container.mux(packet)
        for packet in vStream.encode():
            container.mux(packet)

    n = int(totalSec * sampleRate)
    t = np.arange(n) / sampleRate
    signal = np.zeros(n, dtype=np.float32)
    for start, end in bursts:
        mask = (t >= start) & (t < end)
        signal[mask] = 0.3 * np.sin(2 * np.pi * 220 * t[mask])
    signal += np.random.default_rng(0).normal(0, 0.0005, n).astype(np.float32)  # room tone
    stereo = np.stack([signal, signal])
    chunk = 1024
    for i in range(0, n, chunk):
        part = np.ascontiguousarray(stereo[:, i:i + chunk])
        frame = av.AudioFrame.from_ndarray(part, format="fltp", layout="stereo")
        frame.sample_rate = sampleRate
        for packet in aStream.encode(frame):
            container.mux(packet)
    for packet in aStream.encode():
        container.mux(packet)
    container.close()
    return path


def makeVoicelessRush(path: str, fps: int = 30, sampleRate: int = 48000):
    """12 s, no voice: 0-4 idle desk, 4-8 hands moving (square), hard cut at 8, 8-12 switch clicks."""
    container = av.open(path, mode="w")
    vStream = container.add_stream("libx264", rate=fps)
    vStream.width, vStream.height, vStream.pix_fmt = 320, 180, "yuv420p"
    aStream = container.add_stream("aac", rate=sampleRate)
    aStream.layout = "stereo"
    rng = np.random.default_rng(3)
    total = 12.0
    for i in range(int(total * fps)):
        t = i / fps
        if t < 8.0:
            img = np.full((180, 320, 3), 90, dtype=np.uint8)
            img = np.clip(img.astype(np.int16) + rng.integers(-2, 3, img.shape), 0, 255).astype(np.uint8)
            if t >= 4.0:
                x = int(20 + (t - 4.0) * 60)
                img[60:120, x:x + 60] = 240
        else:
            img = np.zeros((180, 320, 3), dtype=np.uint8)
            img[:, :] = (30, 30, 200)
        frame = av.VideoFrame.from_ndarray(img, format="rgb24")
        for packet in vStream.encode(frame):
            container.mux(packet)
    for packet in vStream.encode():
        container.mux(packet)

    n = int(total * sampleRate)
    signal = rng.normal(0, 0.0005, n).astype(np.float32)
    clickTimes = np.arange(8.1, 11.9, 0.25)
    for ct in clickTimes:
        s = int(ct * sampleRate)
        burst = np.exp(-np.arange(int(0.03 * sampleRate)) / (0.004 * sampleRate)) * 0.5
        signal[s:s + len(burst)] += (burst * rng.choice([-1, 1], len(burst))).astype(np.float32)
    stereo = np.stack([signal, signal])
    for i in range(0, n, 1024):
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(stereo[:, i:i + 1024]), format="fltp", layout="stereo")
        frame.sample_rate = sampleRate
        for packet in aStream.encode(frame):
            container.mux(packet)
    for packet in aStream.encode():
        container.mux(packet)
    container.close()
    return path


def makePresetFile(path: str) -> str:
    """a small .prfpset in premiere's object graph format, written from scratch for the tests."""
    ticks = 254016000000
    hour = 3600 * ticks
    objects = []

    def add(xml: str) -> int:
        objects.append(xml)
        return len(objects)

    def scalar(pid, name, value, keys=None, cls="fe47129e-6c94-4fc0-95d5-c056a517aaf3", control=2):
        tv = "true" if keys else "false"
        return (f'<VideoComponentParam ObjectID="{{id}}" ClassID="{cls}" Version="9"><ParameterID>{pid}</ParameterID>'
                f'<CurrentValue>0.</CurrentValue><StartKeyframe>-91445760000000000,{value},0,0,0,0,0,0</StartKeyframe>'
                f'<ParameterControlType>{control}</ParameterControlType><IsTimeVarying>{tv}</IsTimeVarying>'
                f'<Keyframes>{keys or ""}</Keyframes><Name>{name}</Name></VideoComponentParam>')

    def point(pid, name, value):
        return (f'<PointComponentParam ObjectID="{{id}}" ClassID="ca81d347-309b-44d2-acc7-1c572efb973c" Version="3">'
                f'<ParameterID>{pid}</ParameterID><CurrentValue>0,0</CurrentValue>'
                f'<StartKeyframe>-91445760000000000,{value},0,0,0,0,0,0,5,4,0,0,0,0</StartKeyframe>'
                f'<ParameterControlType>6</ParameterControlType><IsTimeVarying>false</IsTimeVarying><Keyframes />'
                f'<Name>{name}</Name></PointComponentParam>')

    def arb(name, blob):
        return (f'<ArbVideoComponentParam ObjectID="{{id}}" ClassID="313e54d4-6903-49ad-b0bf-8262cdd10f4e" Version="2">'
                f'<Name>{name}</Name><IsTimeVarying>false</IsTimeVarying><ParameterControlType>1</ParameterControlType>'
                f'<StartKeyframeValue>{blob}</StartKeyframeValue></ArbVideoComponentParam>')

    def preset(name, matchName, display, paramXml, anchor="1", anchorIn=hour, anchorOut=hour + 4 * ticks, audio=False):
        paramIds = [add(x.replace("{id}", str(len(objects) + 1))) for x in paramXml]
        refs = "".join(f'<Param Index="{i}" ObjectRef="{pid}"/>' for i, pid in enumerate(paramIds))
        tag = "AudioFilterComponent" if audio else "VideoFilterComponent"
        comp = add(f'<{tag} ObjectID="{len(objects) + 1}" ClassID="d10da199-beea-4dd1-b941-ed3a78766d50" Version="8">'
                   f'<MatchName>{matchName}</MatchName><Component Version="6"><Intrinsic>false</Intrinsic>'
                   f'<Params Version="1">{refs}</Params><DisplayName>{display}</DisplayName></Component></{tag}>')
        fp = add(f'<FilterPreset ObjectID="{len(objects) + 1}" ClassID="ee52a7d2-069e-47f7-aa30-e3e3286b65a3" Version="3">'
                 f'<AnchorOutPoint>{anchorOut}</AnchorOutPoint><AnchorInPoint>{anchorIn}</AnchorInPoint><Type>{anchor}</Type>'
                 f'<Component ObjectRef="{comp}"/><FilterMatchName>{matchName}</FilterMatchName></FilterPreset>')
        item = add(f'<FilterPresetItem ObjectID="{len(objects) + 1}" ClassID="e56c9ba0-91cb-4f21-9174-c1b52448c6c9" Version="2">'
                   f'<FilterPresets Version="1"><FilterPreset Index="0" ObjectRef="{fp}"/></FilterPresets></FilterPresetItem>')
        return add(f'<TreeItem ObjectID="{len(objects) + 1}" ClassID="025f59ac-1f29-42c0-9897-ada1059bd547" Version="3">'
                   f'<TreeItemBase Version="4"><Data ObjectRef="{item}"/><Name>{name}</Name></TreeItemBase></TreeItem>')

    f = ticks // 30  # one frame at 30 fps
    transform = lambda keysH, keysW="": [point(1, "Anchor Point", "0.5:0.5"), point(2, "Position", "0.5:0.5"),
                                         scalar(11, "", "true", cls="cc12343e-f113-4d3b-ae05-b287db77d461", control=4),
                                         scalar(3, "Scale Height", "100.", keysH), scalar(4, "Scale Width", "100.", keysW),
                                         scalar(8, "Opacity", "100.")]
    popKeys = (f"{hour},75.,0,0,0,0.1666,525.,0.1666;{hour + 2 * f},110.,0,0,525.,0.1666,-200.,0.1666;"
               f"{hour + 3 * f + f // 2},100.,0,0,-200.,0.1666,0,0.1666;")
    out0 = hour + 4 * ticks
    zoomKeys = f"{out0 - ticks // 2},130.,5,0,0,0.3333,0,0.75;{out0},100.,5,0,0,0.75,0,0.3333;"
    items = [
        preset("Pop In", "AE.ADBE Geometry2", "Transform", transform(popKeys, popKeys)),
        preset("Smooth Zoom Out", "AE.ADBE Geometry2", "Transform", transform(zoomKeys), anchor="2"),
        preset("Red Shadow", "AE.ADBE Drop Shadow", "Drop Shadow", [
            scalar(1, "Shadow Color", "18374966855136706560", cls="0fde4e9f-f895-4ba3-b0fe-9a6feafda583", control=5),
            scalar(3, "Direction", "135.", control=3), scalar(4, "Distance", "12.")]),
        preset("Graded", "AE.ADBE Lumetri", "Lumetri Color", [scalar(1, "Exposure", "0.5"), arb("Hue vs Sat", "QUJDRA==")]),
        preset("Voice Air", "{guid}", "{guid}", [], audio=True),
    ]
    sub = add(f'<BinTreeItem ObjectID="{len(objects) + 1}" ClassID="5e0f46fa-384f-4c09-bc53-0b8e2b7005b5" Version="4">'
              f'<TreeItemBase Version="4"><Name>flash test</Name></TreeItemBase><Items Version="1">'
              + "".join(f'<Item Index="{i}" ObjectRef="{r}"/>' for i, r in enumerate(items)) + '</Items></BinTreeItem>')
    rootBin = add(f'<BinTreeItem ObjectID="{len(objects) + 1}" ClassID="5e0f46fa-384f-4c09-bc53-0b8e2b7005b5" Version="4">'
                  f'<TreeItemBase Version="4"><Name>Root</Name></TreeItemBase><Items Version="1">'
                  f'<Item Index="0" ObjectRef="{sub}"/></Items></BinTreeItem>')
    tree = add(f'<Tree ObjectID="{len(objects) + 1}" ClassID="177f2841-dd5b-43bd-9d9a-79e231bd47dd" Version="1">'
               f'<RootBin ObjectRef="{rootBin}"/></Tree>')
    body = "\n".join(objects)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f'<?xml version="1.0" encoding="UTF-8"?>\n<PremiereData Version="3">\n<Tree ObjectRef="{tree}"/>\n{body}\n</PremiereData>\n')
    return path


def trackingTruth(t: float) -> dict:
    """where things are at time t: camera shake (pixels, degrees), the moving box, the screen corners."""
    shakeX = 6 * np.sin(2 * np.pi * 3.1 * t) + 3 * np.sin(2 * np.pi * 7.3 * t)
    shakeY = 4 * np.sin(2 * np.pi * 2.3 * t + 1)
    angle = 0.6 * np.sin(2 * np.pi * 1.7 * t)
    return {"shake": (shakeX, shakeY, angle), "box": (100 + 120 * t, 210 - 20 * t, 160 + 120 * t, 270 - 20 * t)}


def makeTrackingRush(path: str, fps: int = 30, seconds: float = 2.0, size=(640, 360)) -> str:
    """textured desk seen by a shaky camera: a screen fixed in the scene (screenAt) and a moving box (trackingTruth)."""
    import cv2
    W, H = size
    rng = np.random.default_rng(11)
    scene = cv2.GaussianBlur(rng.integers(0, 255, (H + 200, W + 200, 3)).astype(np.uint8), (0, 0), 2.0)
    scene = cv2.addWeighted(scene, 0.6, np.full_like(scene, 110), 0.4, 0)
    quad = np.array(screenQuad, dtype=np.float32) + 100
    screen = rng.integers(0, 255, (120, 200, 3)).astype(np.uint8)
    screen = cv2.resize(cv2.GaussianBlur(screen, (0, 0), 1.0), (200, 120), interpolation=cv2.INTER_NEAREST)
    warp = cv2.getPerspectiveTransform(np.float32([[0, 0], [199, 0], [199, 119], [0, 119]]), quad)
    layer = cv2.warpPerspective(screen, warp, (scene.shape[1], scene.shape[0]))
    mask = cv2.warpPerspective(np.full((120, 200), 255, np.uint8), warp, (scene.shape[1], scene.shape[0]))
    scene[mask > 0] = layer[mask > 0]
    container = av.open(path, mode="w")
    stream = container.add_stream("libx264", rate=fps)
    stream.width, stream.height, stream.pix_fmt = W, H, "yuv420p"
    stream.options = {"crf": "12"}
    boxTexture = rng.integers(160, 255, (60, 60, 3)).astype(np.uint8)
    for i in range(int(seconds * fps)):
        truth = trackingTruth(i / fps)
        dx, dy, angle = truth["shake"]
        m = cv2.getRotationMatrix2D((W / 2, H / 2), -angle, 1.0)
        m[:, 2] += (dx - 100, dy - 100)
        img = cv2.warpAffine(scene, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        x0, y0, x1, y1 = [int(round(v)) for v in truth["box"]]
        img[y0:y1, x0:x1] = cv2.resize(boxTexture, (x1 - x0, y1 - y0))
        frame = av.VideoFrame.from_ndarray(img, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return path


screenQuad = [[380, 60], [560, 80], [550, 190], [370, 175]]


def screenAt(t: float, size=(640, 360)) -> np.ndarray:
    """screen corners in frame pixels at time t."""
    import cv2
    W, H = size
    dx, dy, angle = trackingTruth(t)["shake"]
    m = cv2.getRotationMatrix2D((W / 2, H / 2), -angle, 1.0)
    m[:, 2] += (dx - 100, dy - 100)
    pts = np.hstack([np.array(screenQuad, dtype=np.float64) + 100, np.ones((4, 1))])
    return pts @ m.T


# reference edit: hard cuts, a zoom shot, a pan shot, a punch-in, a white flash, clicks on the cuts
refCuts = [1.0, 1.5, 2.0, 3.2, 4.0]
refPunch = 4.6
refFlash = 5.4
refScenes = [(0.0, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.2), (3.2, 4.0), (4.0, 6.0)]


def referenceScene(seed: int):
    """textured picture with its own palette, larger than the frame so it can zoom and pan."""
    import cv2
    rng = np.random.default_rng(100 + seed)
    base = cv2.GaussianBlur(rng.integers(0, 255, (450, 800, 3)).astype(np.uint8), (0, 0), 1.6)
    tint = rng.integers(40, 220, 3)
    base = cv2.addWeighted(base, 0.55, np.full_like(base, 0) + tint.astype(np.uint8), 0.45, 0)
    for _ in range(40):
        x, y = rng.integers(0, 760), rng.integers(0, 420)
        cv2.rectangle(base, (int(x), int(y)), (int(x + rng.integers(10, 60)), int(y + rng.integers(10, 40))),
                      tuple(int(c) for c in rng.integers(0, 255, 3)), -1)
    return base


def makeReferenceVideo(path: str, fps: int = 30, sampleRate: int = 48000) -> str:
    import cv2
    W, H = 320, 180
    scenes = [referenceScene(k) for k in range(len(refScenes))]
    container = av.open(path, mode="w")
    vStream = container.add_stream("libx264", rate=fps)
    vStream.width, vStream.height, vStream.pix_fmt = W, H, "yuv420p"
    vStream.options = {"crf": "14"}
    aStream = container.add_stream("aac", rate=sampleRate)
    aStream.layout = "stereo"
    total = int(6.0 * fps)
    for i in range(total):
        t = i / fps
        k = next(j for j, (a, b) in enumerate(refScenes) if a <= t + 1e-6 < b)
        scale, cx = 1.0, 400.0
        if k == 3:
            scale = 1.0 + 0.3 * (t - 2.0) / 1.2  # slow zoom in
        if k == 4:
            cx = 400.0 + 3.0 * (t - 3.2) * fps  # pan right, 3 px a frame on the base
        if k == 5 and t >= refPunch - 1e-6:
            scale = 1.25
        cw, ch = 640 / scale, 360 / scale
        crop = scenes[k][int(225 - ch / 2):int(225 + ch / 2), int(cx - cw / 2):int(cx + cw / 2)]
        img = cv2.resize(crop, (W, H), interpolation=cv2.INTER_AREA)
        if refFlash - 1e-6 <= t < refFlash + 2 / fps - 1e-6:
            img = np.full_like(img, 250)
        frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(img), format="rgb24")
        for packet in vStream.encode(frame):
            container.mux(packet)
    for packet in vStream.encode():
        container.mux(packet)
    n = int(6.0 * sampleRate)
    signal = np.random.default_rng(5).normal(0, 0.002, n).astype(np.float32)
    for c in refCuts + [refPunch]:
        s = int(c * sampleRate)
        burst = np.arange(int(0.08 * sampleRate))
        signal[s:s + len(burst)] += (0.6 * np.exp(-burst / (0.015 * sampleRate)) * np.sin(2 * np.pi * 900 * burst / sampleRate)).astype(np.float32)
    stereo = np.stack([signal, signal])
    for i in range(0, n, 1024):
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(stereo[:, i:i + 1024]), format="fltp", layout="stereo")
        frame.sample_rate = sampleRate
        for packet in aStream.encode(frame):
            container.mux(packet)
    for packet in aStream.encode():
        container.mux(packet)
    container.close()
    return path


def makeColorClip(path: str, color=(30, 160, 60), seconds: float = 4.0, size=(320, 180), fps: int = 30,
                  audio: str | None = "mono", tone: float = 440.0, sampleRate: int = 48000) -> str:
    """a second camera or a meme clip: flat colour with a moving bar, a steady tone (mono, stereo or none)."""
    W, H = size
    container = av.open(path, mode="w")
    vStream = container.add_stream("libx264", rate=fps)
    vStream.width, vStream.height, vStream.pix_fmt = W, H, "yuv420p"
    aStream = None
    if audio:
        aStream = container.add_stream("aac", rate=sampleRate)
        aStream.layout = "mono" if audio == "mono" else "stereo"
    for i in range(int(seconds * fps)):
        img = np.zeros((H, W, 3), dtype=np.uint8)
        img[:, :] = color
        x = (i * 4) % (W - 20)
        img[H // 3:2 * H // 3, x:x + 20] = 255
        for packet in vStream.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
            container.mux(packet)
    for packet in vStream.encode():
        container.mux(packet)
    if aStream is not None:
        n = int(seconds * sampleRate)
        signal = (0.2 * np.sin(2 * np.pi * tone * np.arange(n) / sampleRate)).astype(np.float32)
        channels = 1 if audio == "mono" else 2
        for i in range(0, n, 1024):
            part = np.ascontiguousarray(np.stack([signal[i:i + 1024]] * channels))
            frame = av.AudioFrame.from_ndarray(part, format="fltp", layout="mono" if channels == 1 else "stereo")
            frame.sample_rate = sampleRate
            for packet in aStream.encode(frame):
                container.mux(packet)
        for packet in aStream.encode():
            container.mux(packet)
    container.close()
    return path


def makeSfx(path: str, seconds: float = 0.5, sampleRate: int = 48000) -> str:
    """a whoosh-like noise burst as a mono wav."""
    container = av.open(path, mode="w")
    stream = container.add_stream("pcm_s16le", rate=sampleRate)
    stream.layout = "mono"
    n = int(seconds * sampleRate)
    envelope = np.sin(np.pi * np.arange(n) / n)
    signal = (0.4 * envelope * np.random.default_rng(2).normal(0, 0.5, n)).clip(-1, 1)
    pcm = (signal * 32767).astype(np.int16)
    for i in range(0, n, 1024):
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm[i:i + 1024].reshape(1, -1)), format="s16", layout="mono")
        frame.sample_rate = sampleRate
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return path
