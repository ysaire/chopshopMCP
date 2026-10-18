"""2d tracking, after effects style, turned into premiere keyframes:
- trackBox: follow an object (a label or sticker rides along)
- trackQuad: follow a flat surface in perspective (a screen to replace with corner pin)
- cameraPath: the shake of the camera, smoothed, as a transform correction (stabilize)
all coordinates normalized to the source frame, times in source seconds.
"""

import math

import numpy as np

from masks import BoxTracker, iterFrames, toPixels


def gray(rgb: np.ndarray) -> np.ndarray:
    import cv2
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def smoothSeries(values: np.ndarray, radius: int) -> np.ndarray:
    """centered moving average, edges padded with the end values."""
    if radius <= 0 or len(values) < 3:
        return values
    padded = np.concatenate([np.repeat(values[:1], radius, axis=0), values, np.repeat(values[-1:], radius, axis=0)])
    kernel = np.ones(2 * radius + 1) / (2 * radius + 1)
    if values.ndim == 1:
        return np.convolve(padded, kernel, mode="valid")
    return np.stack([np.convolve(padded[:, i], kernel, mode="valid") for i in range(values.shape[1])], axis=1)


def gaussianSmooth(values: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0 or len(values) < 3:
        return values
    radius = int(math.ceil(3 * sigma))
    x = np.arange(-radius, radius + 1)
    kernel = np.exp(-x * x / (2 * sigma * sigma))
    kernel /= kernel.sum()
    padded = np.concatenate([np.repeat(values[:1], radius, axis=0), values, np.repeat(values[-1:], radius, axis=0)])
    if values.ndim == 1:
        return np.convolve(padded, kernel, mode="valid")
    return np.stack([np.convolve(padded[:, i], kernel, mode="valid") for i in range(values.shape[1])], axis=1)


def pointsIn(grayImg: np.ndarray, quadPx: np.ndarray, grow: float = 0.08) -> np.ndarray | None:
    import cv2
    center = quadPx.mean(axis=0)
    poly = ((quadPx - center) * (1 + grow) + center).astype(np.int32)
    mask = np.zeros_like(grayImg)
    cv2.fillConvexPoly(mask, poly, 255)
    return cv2.goodFeaturesToTrack(grayImg, maxCorners=400, qualityLevel=0.01, minDistance=5, mask=mask, blockSize=7)


def trackCorners(mediaPath: str, start: float, end: float, quad: list, model: str = "homography") -> dict:
    """klt features inside the shape, a transform from the reference frame (no drift from chaining),
    reseeded when too few points survive. model: homography (a flat surface in perspective) or
    similarity (an object: move, turn, scale). {"frames": [{"t", "quad"}], "lost", "quality", "size"}."""
    import cv2
    lk = dict(winSize=(21, 21), maxLevel=3, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
    frames, lost, quality = [], [], []
    refPts = curPts = prevGray = None
    refQuad = current = None
    W = H = None
    for _i, t, rgb, W, H, _rate in iterFrames(mediaPath, start, end, True):
        g = gray(rgb)
        if refQuad is None:
            refQuad = np.array([[x * W, y * H] for x, y in quad], dtype=np.float64)
            current = refQuad.copy()
            refPts = pointsIn(g, refQuad, 0.0 if model == "similarity" else 0.08)
            curPts = refPts.copy() if refPts is not None else None
            frames.append((t, current.copy()))
            quality.append(1.0)
            prevGray = g
            continue
        ok = False
        if curPts is not None and len(curPts) >= 8:
            nextPts, status, _err = cv2.calcOpticalFlowPyrLK(prevGray, g, curPts, None, **lk)
            backPts, backStatus, _err = cv2.calcOpticalFlowPyrLK(g, prevGray, nextPts, None, **lk)
            fb = np.linalg.norm((curPts - backPts).reshape(-1, 2), axis=1)
            good = (status.ravel() == 1) & (backStatus.ravel() == 1) & (fb < 1.0)
            if good.sum() >= 8:
                refPts, curPts = refPts[good], nextPts[good]
                a, b = refPts.reshape(-1, 2), curPts.reshape(-1, 2)
                if model == "similarity":
                    m, inliers = cv2.estimateAffinePartial2D(a, b, method=cv2.RANSAC, ransacReprojThreshold=2.0)
                    hmat = np.vstack([m, [0, 0, 1]]) if m is not None else None
                else:
                    hmat, inliers = cv2.findHomography(a, b, cv2.RANSAC, 2.0)
                if hmat is not None and inliers is not None and inliers.sum() >= 8:
                    current = cv2.perspectiveTransform(refQuad.reshape(-1, 1, 2), hmat).reshape(-1, 2)
                    keep = inliers.ravel() == 1
                    refPts, curPts = refPts[keep], curPts[keep]
                    quality.append(round(float(keep.mean()), 3))
                    ok = True
        if not ok:
            lost.append(round(t, 3))
            quality.append(0.0)
        if curPts is None or len(curPts) < 40:
            fresh = pointsIn(g, current, 0.0 if model == "similarity" else 0.08)
            if fresh is not None and len(fresh) >= 8:
                refQuad, refPts, curPts = current.copy(), fresh, fresh.copy()
        frames.append((t, current.copy()))
        prevGray = g
    out = [{"t": round(t, 4), "quad": [[round(x / W, 5), round(y / H, 5)] for x, y in q]} for t, q in frames]
    return {"frames": out, "lost": lost, "quality": quality, "size": [W, H]}


def trackQuad(mediaPath: str, start: float, end: float, quad: list) -> dict:
    """a flat surface in perspective. quad = upper left, upper right, lower right, lower left, normalized."""
    return trackCorners(mediaPath, start, end, quad, "homography")


def trackBox(mediaPath: str, start: float, end: float, box: list) -> dict:
    """an object: {"frames": [{"t", "box"}], "lost"}. feature tracking when the object has texture,
    csrt when it is too plain to hold 8 points."""
    x0, y0, x1, y1 = box
    corners = trackCorners(mediaPath, start, end, [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "similarity")
    if corners["frames"] and len(corners["lost"]) <= 0.2 * len(corners["frames"]):
        out = []
        for f in corners["frames"]:
            q = np.array(f["quad"])
            out.append({"t": f["t"], "box": [round(float(q[:, 0].min()), 5), round(float(q[:, 1].min()), 5),
                                             round(float(q[:, 0].max()), 5), round(float(q[:, 1].max()), 5)]})
        return {"frames": out, "lost": corners["lost"], "method": "features"}
    frames, lost = [], []
    tracker = BoxTracker()
    current = None
    W = H = None
    for _i, t, rgb, W, H, _rate in iterFrames(mediaPath, start, end, True):
        if current is None:
            current = toPixels(box, W, H)
            tracker.start(rgb, current)
        else:
            found = tracker.update(rgb)
            if found:
                current = found
            else:
                lost.append(round(t, 3))
        frames.append((t, current))
    if not frames:
        return {"frames": [], "lost": [], "method": "csrt"}
    boxes = smoothSeries(np.array([b for _, b in frames], dtype=np.float64), 2)
    out = [{"t": round(t, 4), "box": [round(boxes[i][0] / W, 5), round(boxes[i][1] / H, 5), round(boxes[i][2] / W, 5),
                                       round(boxes[i][3] / H, 5)]} for i, (t, _) in enumerate(frames)]
    return {"frames": out, "lost": lost, "method": "csrt"}


def cameraPath(mediaPath: str, start: float, end: float, smoothness: float = 0.5) -> dict:
    """frame to frame camera motion (center based similarity), smoothed, and the per frame correction a
    transform effect must apply: {"frames": [{"t", "dx", "dy", "angle", "scale"}], "zoom", "size"}.
    smoothness 0..1: 0 keeps slow moves, 1 locks the shot. dx / dy are normalized to the frame."""
    import cv2
    steps, times = [], []
    prevGray = prevPts = None
    W = H = None
    for _i, t, rgb, W, H, _rate in iterFrames(mediaPath, start, end, True):
        g = gray(rgb)
        times.append(t)
        if prevGray is None:
            steps.append((0.0, 0.0, 0.0, 0.0))
        else:
            step = (0.0, 0.0, 0.0, 0.0)
            if prevPts is not None and len(prevPts) >= 10:
                nextPts, status, _err = cv2.calcOpticalFlowPyrLK(prevGray, g, prevPts, None, winSize=(21, 21), maxLevel=3)
                good = status.ravel() == 1
                if good.sum() >= 10:
                    center = np.array([W / 2, H / 2], dtype=np.float32)
                    a = prevPts[good].reshape(-1, 2) - center
                    b = nextPts[good].reshape(-1, 2) - center
                    m, _inl = cv2.estimateAffinePartial2D(a, b, method=cv2.RANSAC, ransacReprojThreshold=2.0)
                    if m is not None:
                        step = (float(m[0, 2]), float(m[1, 2]), math.degrees(math.atan2(m[1, 0], m[0, 0])),
                                math.log(max(1e-6, math.hypot(m[0, 0], m[1, 0]))))
            steps.append(step)
        prevGray = g
        prevPts = cv2.goodFeaturesToTrack(g, maxCorners=300, qualityLevel=0.01, minDistance=12, blockSize=7)
    if not times:
        return {"frames": [], "zoom": 1.0, "size": [W, H]}
    trajectory = np.cumsum(np.array(steps), axis=0)
    fps = (len(times) - 1) / max(1e-6, times[-1] - times[0]) if len(times) > 1 else 30.0
    sigma = max(0.5, (0.15 + 1.6 * min(1.0, max(0.0, smoothness))) * fps)
    target = gaussianSmooth(trajectory, sigma)
    correction = target - trajectory
    # enough zoom so the shifted, rotated frame never shows its edges
    halfDiag = math.hypot(W, H) / 2
    need = 1.0
    for cx, cy, ang, _s in correction:
        rot = math.radians(abs(ang))
        need = max(need, 1 + 2 * abs(cx) / W + 2 * abs(cy) / H + 2 * rot * halfDiag / min(W, H))
    zoom = round(min(1.35, need) + 0.01, 3)
    out = [{"t": round(t, 4), "dx": round(c[0] / W, 6), "dy": round(c[1] / H, 6), "angle": round(c[2], 4),
            "scale": round(math.exp(c[3]), 5)} for t, c in zip(times, correction)]
    return {"frames": out, "zoom": zoom, "size": [W, H], "shake": round(float(np.abs(correction[:, :2]).max()), 2)}


def trackSheet(mediaPath: str, frames: list, kind: str, outPath: str, cells: int = 8, cellWidth: int = 360) -> str:
    """a few frames of the track with the box or quad drawn on them, for claude to check before building."""
    import cv2
    from PIL import Image, ImageDraw
    if not frames:
        raise ValueError("nothing tracked")
    picks = [frames[round(i * (len(frames) - 1) / max(1, cells - 1))] for i in range(min(cells, len(frames)))]
    wanted = {round(f["t"], 4): f for f in picks}
    images = []
    start, end = picks[0]["t"], picks[-1]["t"] + 1e-3
    for _i, t, rgb, W, H, _rate in iterFrames(mediaPath, start, end + 0.05, True):
        f = wanted.get(round(t, 4))
        if f is None:
            continue
        img = rgb.copy()
        if kind == "quad":
            pts = np.array([[x * W, y * H] for x, y in f["quad"]], dtype=np.int32)
            cv2.polylines(img, [pts], True, (255, 40, 40), max(2, W // 300))
        else:
            x0, y0, x1, y1 = f["box"]
            cv2.rectangle(img, (int(x0 * W), int(y0 * H)), (int(x1 * W), int(y1 * H)), (255, 40, 40), max(2, W // 300))
        im = Image.fromarray(img)
        im = im.resize((cellWidth, int(im.height * cellWidth / im.width)))
        ImageDraw.Draw(im).text((6, 4), f"{t:.2f}s", fill=(255, 255, 0))
        images.append(im)
    cols = 4
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cellWidth, rows * images[0].height), (0, 0, 0))
    for i, im in enumerate(images):
        sheet.paste(im, ((i % cols) * cellWidth, (i // cols) * im.height))
    sheet.save(outPath, quality=85)
    return outPath
