"""masks: blurred zones, subject cutouts and titles.

matte mode (default, mocha style): only mattes are rendered here (white + alpha, quicktime animation).
premiere does the compositing on the original footage: blur or mosaic on a duplicate, keyed by a
track matte key that reads the hidden matte track, inside a nest at source size.

baked mode (fallback): blur is baked into a rendered copy of the segment and the subject is
rendered as a prores 4444 cutout with alpha.

the subject matte comes from mobilesam (box prompt, coarse) refined by birefnet lite on the gpu.
"""

import hashlib
import json
import os
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFont

renderVersion = 2


def clamp(value, low, high):
    return max(low, min(high, value))


def normalizeBox(box) -> list:
    x0, y0, x1, y1 = [clamp(float(v), 0.0, 1.0) for v in box]
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    if x1 - x0 < 0.01:
        x1 = min(1.0, x0 + 0.01)
    if y1 - y0 < 0.01:
        y1 = min(1.0, y0 + 0.01)
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def normalizeZone(raw: dict, duration: float) -> dict:
    if "keys" in raw:
        keys = [{"t": float(k["t"]), "box": normalizeBox(k["box"])} for k in raw["keys"]]
    else:
        keys = [{"t": float(raw.get("from", 0.0)), "box": normalizeBox(raw["box"])}]
    keys.sort(key=lambda k: k["t"])
    track = bool(raw.get("track", False))
    shape = raw.get("shape")
    if shape not in ("rect", "ellipse", "outline"):
        # a tracked zone hugs its object unless a plain shape is asked for
        shape = "outline" if track else "rect"
    return {
        "keys": keys,
        "from": float(raw.get("from", keys[0]["t"] if "keys" in raw else 0.0)),
        "to": float(raw.get("to", duration)),
        "shape": shape,
        "style": "pixelate" if raw.get("style") == "pixelate" else "blur",
        "strength": round(clamp(float(raw.get("strength", 1.0)), 0.2, 3.0), 2),
        "track": track,
        "label": str(raw.get("label", ""))[:60],
    }


def boxAt(zone: dict, t: float) -> list:
    keys = zone["keys"]
    if t <= keys[0]["t"] or len(keys) == 1:
        return keys[0]["box"]
    if t >= keys[-1]["t"]:
        return keys[-1]["box"]
    for a, b in zip(keys, keys[1:]):
        if a["t"] <= t <= b["t"]:
            f = (t - a["t"]) / max(1e-9, b["t"] - a["t"])
            return [a["box"][i] + f * (b["box"][i] - a["box"][i]) for i in range(4)]
    return keys[-1]["box"]


def zoneActive(zone: dict, t: float) -> bool:
    return zone["from"] <= t <= zone["to"]


def toPixels(box, width: int, height: int) -> tuple:
    x0, y0, x1, y1 = box
    return (int(round(x0 * width)), int(round(y0 * height)), int(round(x1 * width)), int(round(y1 * height)))


class BoxTracker:
    """csrt from opencv-contrib on a downscaled frame; falls back to the planned box."""

    def __init__(self, maxSide: int = 640):
        self.maxSide = maxSide
        self.tracker = None
        self.scale = 1.0
        self.lost = False

    def available(self) -> bool:
        try:
            import cv2
            return hasattr(cv2, "TrackerCSRT_create")
        except Exception:
            return False

    def small(self, rgb):
        import cv2
        h, w = rgb.shape[:2]
        self.scale = min(1.0, self.maxSide / max(h, w))
        img = cv2.resize(rgb, (int(w * self.scale), int(h * self.scale))) if self.scale < 1.0 else rgb
        return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

    def start(self, rgb, boxPx):
        import cv2
        x0, y0, x1, y1 = boxPx
        self.tracker = cv2.TrackerCSRT_create()
        small = self.small(rgb)
        s = self.scale
        self.tracker.init(small, (int(x0 * s), int(y0 * s), max(2, int((x1 - x0) * s)), max(2, int((y1 - y0) * s))))
        self.lost = False

    def update(self, rgb):
        ok, (x, y, w, h) = self.tracker.update(self.small(rgb))
        if not ok:
            self.lost = True
            return None
        s = self.scale
        return (int(x / s), int(y / s), int((x + w) / s), int((y + h) / s))


def featherMask(h: int, w: int, shape: str, feather: float) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    if shape == "ellipse":
        nx = (xx + 0.5 - w / 2) / (w / 2)
        ny = (yy + 0.5 - h / 2) / (h / 2)
        dist = 1.0 - np.sqrt(nx * nx + ny * ny)
        edge = feather / max(1.0, min(w, h) / 2)
        return np.clip(dist / max(1e-6, edge), 0.0, 1.0)
    dx = np.minimum(xx + 0.5, w - xx - 0.5)
    dy = np.minimum(yy + 0.5, h - yy - 0.5)
    return np.clip(np.minimum(dx, dy) / max(1.0, feather), 0.0, 1.0)


def blurRegion(rgb: np.ndarray, boxPx: tuple, zone: dict) -> None:
    """in place. downscale, blur, upscale: strong and fast even on big zones."""
    import cv2
    H, W = rgb.shape[:2]
    x0, y0, x1, y1 = boxPx
    x0, y0, x1, y1 = clamp(x0, 0, W - 1), clamp(y0, 0, H - 1), clamp(x1, 1, W), clamp(y1, 1, H)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    roi = rgb[y0:y1, x0:x1]
    h, w = roi.shape[:2]
    if zone["style"] == "pixelate":
        blocks = max(4, int(18 / zone["strength"]))
        tiny = cv2.resize(roi, (max(1, w // max(1, w // blocks)), max(1, h // max(1, w // blocks))), interpolation=cv2.INTER_AREA)
        effect = cv2.resize(tiny, (w, h), interpolation=cv2.INTER_NEAREST)
    else:
        factor = 8
        tiny = cv2.resize(roi, (max(1, w // factor), max(1, h // factor)), interpolation=cv2.INTER_AREA)
        k = max(3, int(min(tiny.shape[:2]) * 0.25 * zone["strength"]) | 1)
        tiny = cv2.GaussianBlur(tiny, (k, k), 0)
        effect = cv2.resize(tiny, (w, h), interpolation=cv2.INTER_LINEAR)
    mask = featherMask(h, w, zone["shape"], feather=max(2.0, 0.06 * min(w, h)))[..., None]
    roi[:] = (roi.astype(np.float32) * (1 - mask) + effect.astype(np.float32) * mask).astype(np.uint8)


providerNames = {"CUDAExecutionProvider": "cuda", "DmlExecutionProvider": "directml", "CPUExecutionProvider": "cpu"}


def onnxProviders() -> list:
    """cuda (nvidia), directml (any dx12 gpu), cpu, in that order, keeping only what this onnxruntime build has."""
    import onnxruntime as ort
    available = ort.get_available_providers()
    if "CUDAExecutionProvider" in available and hasattr(ort, "preload_dlls"):
        import contextlib
        import sys
        try:
            # cuda and cudnn from the nvidia pip wheels; it prints on stdout, which is the mcp channel
            with contextlib.redirect_stdout(sys.stderr):
                ort.preload_dlls()
        except Exception:
            pass
    return [p for p in providerNames if p in available]


def makeSession(path: str, providers: list):
    import onnxruntime as ort
    options = ort.SessionOptions()
    if "DmlExecutionProvider" in providers:
        # directml wants sequential execution and no memory pattern
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(path, sess_options=options, providers=providers)


def activeProvider(session) -> str:
    try:
        return providerNames.get(session.get_providers()[0], "cpu")
    except Exception:
        return "cpu"


class SamModel:
    """mobilesam (mit): box prompt -> coarse mask, solid on the object. ~45 mb from hugging face."""

    repo = "Acly/MobileSAM"

    def __init__(self, providers: list):
        from huggingface_hub import hf_hub_download
        self.encoder = makeSession(hf_hub_download(self.repo, "mobile_sam_image_encoder.onnx"), providers)
        self.decoder = makeSession(hf_hub_download(self.repo, "sam_mask_decoder_single.onnx"), providers)

    def embed(self, rgb: np.ndarray) -> tuple:
        """(image embedding, scale of the 1024 px side): one per frame, every prompt decodes from it."""
        H, W = rgb.shape[:2]
        scale = 1024 / max(H, W)
        small = np.asarray(Image.fromarray(rgb).resize((round(W * scale), round(H * scale)), Image.BILINEAR), dtype=np.float32)
        return self.encoder.run(None, {"input_image": small})[0], scale

    def decode(self, embedding, scale: float, H: int, W: int, boxPx: tuple, previous=None, point: tuple | None = None) -> tuple:
        """(alpha, low-res mask for the next frame, predicted iou) for a box prompt, plus a positive point."""
        x0, y0, x1, y1 = boxPx
        coords, labels = [[x0, y0], [x1, y1]], [2, 3]
        if point is not None:
            coords.append([point[0], point[1]])
            labels.append(1)
        feeds = {
            "image_embeddings": embedding,
            "point_coords": np.array([coords], dtype=np.float32) * scale,
            "point_labels": np.array([labels], dtype=np.float32),
            "mask_input": previous if previous is not None else np.zeros((1, 1, 256, 256), np.float32),
            "has_mask_input": np.array([1.0 if previous is not None else 0.0], dtype=np.float32),
            "orig_im_size": np.array([H, W], dtype=np.float32),
        }
        masks, iou, lowRes = self.decoder.run(None, feeds)
        logits = masks[0, 0]
        alpha = 1.0 / (1.0 + np.exp(-np.clip(logits, -20, 20)))
        return alpha.astype(np.float32), lowRes, float(np.asarray(iou).ravel()[0])

    def mask(self, rgb: np.ndarray, boxPx: tuple, previous=None) -> tuple:
        H, W = rgb.shape[:2]
        embedding, scale = self.embed(rgb)
        alpha, lowRes, _iou = self.decode(embedding, scale, H, W, boxPx, previous)
        return alpha, lowRes


def sketchPoint(mask: np.ndarray) -> tuple:
    """(x, y) pixels of the sketch's deepest point: the object lies under it, where a box alone may miss it."""
    import cv2
    dist = cv2.distanceTransform(np.ascontiguousarray(mask, dtype=np.uint8), cv2.DIST_L2, 5)
    y, x = np.unravel_index(int(np.argmax(dist)), dist.shape)
    return (int(x), int(y))


def sketchGate(mask: np.ndarray, grow: float = 0.1) -> np.ndarray:
    """the sketch grown by grow x its longer side, float 0/1: how far a model prompted from it may bleed."""
    import cv2
    box = binaryBox(mask, 0.0, mask.shape[1], mask.shape[0])
    if box is None:
        return mask.astype(np.float32)
    r = int(round(grow * max(box[2] - box[0], box[3] - box[1])))
    if r <= 0:
        return mask.astype(np.float32)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    return cv2.dilate(mask.astype(np.uint8), kernel).astype(np.float32)


def lowResLogits(mask: np.ndarray, size: int = 256, strength: float = 10.0) -> np.ndarray:
    """a bool mask as the low-res logits mobilesam's decoder takes with a prompt (1, 1, 256, 256)."""
    small = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize((size, size), Image.BILINEAR), dtype=np.float32) / 255
    return ((small * 2 - 1) * strength).astype(np.float32)[None, None]


def binaryBox(binary: np.ndarray, grow: float, width: int, height: int) -> tuple | None:
    """the box around a binary mask, grown by a fraction of its size; None for an empty mask."""
    ys, xs = np.nonzero(binary)
    if len(xs) < 20:
        return None
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    gx, gy = (x1 - x0) * grow, (y1 - y0) * grow
    return (int(max(0, x0 - gx)), int(max(0, y0 - gy)), int(min(width, x1 + gx)), int(min(height, y1 + gy)))


def maskIou(p: np.ndarray, q: np.ndarray) -> float:
    union = np.logical_or(p, q).sum()
    return float(np.logical_and(p, q).sum() / union) if union else 0.0


def pickCandidate(candidates: list, previous: np.ndarray | None) -> int:
    """index of the (alpha, box, predicted iou) that best continues previous, else the most confident one; a
    mask filling most of its box is the background and loses half a point."""
    best, bestScore = 0, -1e9
    for i, (alpha, box, predicted) in enumerate(candidates):
        binary = alpha > 0.5
        x0, y0, x1, y1 = box
        fill = float(binary[y0:y1, x0:x1].mean()) if y1 > y0 and x1 > x0 else 0.0
        score = (maskIou(previous, binary) if previous is not None else predicted) - (0.5 if fill > 0.6 else 0.0)
        if score > bestScore:
            best, bestScore = i, score
    return best


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


def expandBox(boxPx: tuple, width: int, height: int, margin: float = 0.15) -> tuple:
    x0, y0, x1, y1 = boxPx
    mx, my = (x1 - x0) * margin, (y1 - y0) * margin
    return (int(clamp(round(x0 - mx), 0, width - 1)), int(clamp(round(y0 - my), 0, height - 1)),
            int(clamp(round(x1 + mx), 1, width)), int(clamp(round(y1 + my), 1, height)))


class FineModel:
    """birefnet lite (mit): hair-level matte on the subject crop. fp16 on gpu (110 mb), fp32 on cpu (220 mb)."""

    repo = "onnx-community/BiRefNet_lite-ONNX"
    size = 1024
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    def __init__(self, providers: list, gpu: bool = True):
        from huggingface_hub import hf_hub_download
        gpu = gpu and providers[0] != "CPUExecutionProvider"
        self.session = makeSession(hf_hub_download(self.repo, "onnx/model_fp16.onnx" if gpu else "onnx/model.onnx"), providers)
        self.inputName = self.session.get_inputs()[0].name

    def prepare(self, rgb: np.ndarray, crop: tuple) -> np.ndarray:
        x0, y0, x1, y1 = crop
        patch = Image.fromarray(rgb[y0:y1, x0:x1]).resize((self.size, self.size), Image.BILINEAR)
        x = (np.asarray(patch, dtype=np.float32) / 255.0 - self.mean) / self.std
        return np.ascontiguousarray(x.transpose(2, 0, 1)[None])

    def alpha(self, rgb: np.ndarray, boxPx: tuple) -> np.ndarray:
        """full-frame alpha, zero outside the box plus a 15% margin."""
        import cv2
        H, W = rgb.shape[:2]
        crop = expandBox(boxPx, W, H)
        logits = self.session.run(None, {self.inputName: self.prepare(rgb, crop)})[0][0, 0]
        x0, y0, x1, y1 = crop
        out = np.zeros((H, W), dtype=np.float32)
        out[y0:y1, x0:x1] = cv2.resize(sigmoid(logits).astype(np.float32), (x1 - x0, y1 - y0), interpolation=cv2.INTER_LINEAR)
        return out


def gateAlpha(fine: np.ndarray, sam: np.ndarray, boxPx: tuple) -> np.ndarray:
    """birefnet edges inside sam's object (birefnet may pick background objects too), with the eroded sam core
    filling holes birefnet may leave in flat areas."""
    import cv2
    x0, y0, x1, y1 = boxPx
    radius = max(3, int(round(0.03 * max(x1 - x0, y1 - y0))))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    hard = (sam > 0.5).astype(np.uint8)
    gate = cv2.GaussianBlur(cv2.dilate(hard, kernel).astype(np.float32), (2 * radius + 1, 2 * radius + 1), 0)
    core = cv2.erode(hard, kernel).astype(np.float32)
    return np.maximum(fine * gate, core)


slowMatteMs, slowMatteFrames = 400, 5


class Segmenter:
    """subject matte from a box. quality: fast = mobilesam only, best = mobilesam + birefnet on every frame,
    auto = best when a gpu runs onnx, fast otherwise."""

    def __init__(self, quality: str = "auto"):
        self.providers = onnxProviders()
        self.sam = SamModel(self.providers)
        # a listed gpu provider can still fail to start (old driver): trust what the session really runs on
        self.gpu = activeProvider(self.sam.encoder) != "cpu"
        if quality not in ("fast", "best"):
            quality = "best" if self.gpu else "fast"
        self.quality = quality
        if quality == "best":
            # a gpu that failed to start is not retried for the second model
            self.fine = FineModel(self.providers if self.gpu else ["CPUExecutionProvider"], gpu=self.gpu)
        else:
            self.fine = None
        self.frames, self.seconds, self.warm = 0, 0.0, False
        self.downgraded = None

    def outline(self, rgb: np.ndarray, boxPx: tuple, state: dict | None = None, mask: np.ndarray | None = None) -> tuple:
        """(alpha, state): the box and the previous mask's box, each with and without the previous low-res mask,
        and the candidate that continues the previous mask wins, since a loose box makes sam pick the background
        on some frames. mask (bool): a hand-drawn sketch that stands in for the previous mask and gates alpha."""
        H, W = rgb.shape[:2]
        state = state or {}
        point = None
        if mask is not None:
            boxPx = binaryBox(mask, 0.0, W, H) or boxPx
            point = sketchPoint(mask)
            state = {"low": lowResLogits(mask), "binary": mask > 0, "box": boxPx}
        embedding, scale = self.sam.embed(rgb)
        prompts = [(boxPx, state.get("low")), (boxPx, None)]
        previous = state.get("binary")
        prevBox = binaryBox(previous, 0.15, W, H) if previous is not None else None
        if prevBox:
            prompts += [(prevBox, state.get("low")), (prevBox, None)]
        decoded = [self.sam.decode(embedding, scale, H, W, box, low, point) + (box,) for box, low in prompts]
        alpha, lowRes, _iou, box = decoded[pickCandidate([(a, b, p) for a, _l, p, b in decoded], previous)]
        if mask is not None:
            alpha = alpha * sketchGate(mask)
        return alpha, {"low": lowRes, "binary": alpha > 0.5, "box": box}

    def describe(self) -> dict:
        model = self.fine.session if self.fine else getattr(self.sam, "encoder", None)
        out = {"quality": self.quality, "provider": activeProvider(model), "gpu": self.gpu}
        if self.frames:
            out["msPerFrame"] = int(round(1000 * self.seconds / self.frames))
        if self.downgraded:
            out["downgraded"] = dict(self.downgraded)
        return out

    def mask(self, rgb: np.ndarray, boxPx: tuple, previous=None) -> tuple:
        import time
        started = time.perf_counter()
        alpha, lowRes = self.sam.mask(rgb, boxPx, previous)
        if self.fine is not None:
            alpha = gateAlpha(self.fine.alpha(rgb, boxPx), alpha, boxPx)
        # the first call pays for gpu warmup, keep it out of the average
        if self.warm:
            self.frames += 1
            self.seconds += time.perf_counter() - started
        self.warm = True
        ms = 1000 * self.seconds / self.frames if self.frames else 0.0
        if self.fine is not None and self.frames >= slowMatteFrames and ms > slowMatteMs:
            # a busy gpu can make best quality cost seconds a frame: finish with mobilesam alone
            self.downgraded = {"from": "best", "afterFrames": self.frames, "msPerFrame": int(round(ms))}
            self.fine, self.quality = None, "fast"
            self.frames, self.seconds = 0, 0.0
        return alpha, lowRes


def refineAlpha(alpha: np.ndarray, previous: np.ndarray | None, smoothing: float = 0.5, fine: bool = False) -> np.ndarray:
    """soft edges, less flicker; fine (birefnet) alpha gets light smoothing only: a strong one trails a moving hand."""
    import cv2
    if not fine:
        radius = max(1, int(round(min(alpha.shape) / 540)))
        alpha = cv2.GaussianBlur(alpha, (radius * 2 + 1, radius * 2 + 1), 0)
    if previous is not None:
        weight = 0.2 if fine else smoothing
        alpha = weight * previous + (1 - weight) * alpha
    return alpha


def cropWindow(boxPx: tuple, width: int, height: int, zoom: float = 2.0, minSide: int = 512) -> tuple:
    """a square window of zoom x the box's longer side (at least minSide) inside the frame: the object seen large."""
    x0, y0, x1, y1 = boxPx
    side = max(zoom * max(x1 - x0, y1 - y0), minSide)
    w, h = int(round(min(side, width))), int(round(min(side, height)))
    left = int(round(clamp((x0 + x1) / 2 - w / 2, 0, width - w)))
    top = int(round(clamp((y0 + y1) / 2 - h / 2, 0, height - h)))
    return (left, top, left + w, top + h)


def nextWindow(window: tuple, boxPx: tuple, width: int, height: int, zoom: float = 2.0, minSide: int = 512) -> tuple:
    """the same window while the object stays a tenth of the side clear of its edges and the window is not far
    too big for it, a new one otherwise: sam 2's memory likes a steady view."""
    wanted = cropWindow(boxPx, width, height, zoom, minSide)
    wx0, wy0, wx1, wy1 = window
    side = max(wx1 - wx0, wy1 - wy0)
    margin = 0.1 * side
    inside = (boxPx[0] >= wx0 + margin and boxPx[1] >= wy0 + margin
              and boxPx[2] <= wx1 - margin and boxPx[3] <= wy1 - margin)
    fits = max(wanted[2] - wanted[0], wanted[3] - wanted[1]) >= 0.7 * side
    return window if inside and fits else wanted


class Sam2Backend:
    """sam 2.1 hiera tiny via transformers, one streaming session per object. ~150 mb, bf16 on cuda."""

    repo = "facebook/sam2.1-hiera-tiny"
    remembered = 16

    def __init__(self, repo: str | None = None):
        import torch
        from transformers import Sam2VideoModel, Sam2VideoProcessor
        self.repo = repo or self.repo
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
        self.model = Sam2VideoModel.from_pretrained(self.repo).to(self.device, dtype=self.dtype).eval()
        self.processor = Sam2VideoProcessor.from_pretrained(self.repo)

    def open(self) -> dict:
        return {"session": self.processor.init_video_session(inference_device=self.device, dtype=self.dtype), "n": 0}

    def step(self, session: dict, rgb: np.ndarray, box: tuple | None = None, point: tuple | None = None) -> np.ndarray:
        """alpha of the object on this frame; box and point (pixels of rgb) prompt it, neither propagates the memory.
        no mask prompt: sam 2 takes a mask as the object itself and tracks that blob, loose edges included."""
        import torch
        h, w = rgb.shape[:2]
        state, n = session["session"], session["n"]
        inputs = self.processor(images=rgb, device=self.device, return_tensors="pt")
        if box is not None or point is not None:
            prompts = {}
            if box is not None:
                prompts["input_boxes"] = [[[float(v) for v in box]]]
            if point is not None:
                prompts["input_points"] = [[[[float(point[0]), float(point[1])]]]]
                prompts["input_labels"] = [[[1]]]
            self.processor.add_inputs_to_inference_session(state, frame_idx=n, obj_ids=1, original_size=(h, w), **prompts)
        with torch.inference_mode():
            out = self.model(inference_session=state, frame=inputs.pixel_values[0], frame_idx=n)
            logits = self.processor.post_process_masks([out.pred_masks], original_sizes=[(h, w)], binarize=False)[0]
        session["n"] = n + 1
        self.forget(state, n)
        return sigmoid(logits[0, 0].float().cpu().numpy())

    def forget(self, state, n: int) -> None:
        """the session keeps every frame and every mask it saw: drop what the memory no longer reads."""
        state.processed_frames.pop(n, None)
        for outputs in state.output_dict_per_obj.values():
            tracked = outputs["non_cond_frame_outputs"]
            for key in [k for k in tracked if k < n - self.remembered]:
                tracked.pop(key)
            for stored in outputs.values():
                for frame in stored.values():
                    frame.pop("high_res_masks", None)


class VideoSegmenter:
    """an outline zone's object through a video model: the box prompts the first frame, the memory carries it on a
    zoomed window, and patience empty masks in a row restart from the caller's box (the tracker's)."""

    def __init__(self, backend=None, zoom: float = 2.0, minSide: int = 512, patience: int = 2):
        self.backend = backend if backend is not None else Sam2Backend()
        self.zoom, self.minSide, self.patience = zoom, minSide, patience
        self.quality = "video"
        self.frames, self.seconds = 0, 0.0

    def outline(self, rgb: np.ndarray, boxPx: tuple, state: dict | None = None, mask: np.ndarray | None = None) -> tuple:
        """mask (bool, frame size): a hand-drawn sketch around the object; its box and deepest point prompt this frame
        (new session or re-prompt of the running one) and what lands outside the sketch grown by a tenth is dropped."""
        import time
        started = time.perf_counter()
        H, W = rgb.shape[:2]
        state = dict(state or {})
        prompt, point = None, None
        if mask is not None:
            prompt = binaryBox(mask, 0.0, W, H) or boxPx
            point = sketchPoint(mask)
            if "session" not in state:
                state = {"session": self.backend.open(), "lost": 0}
            state["window"] = cropWindow(prompt, W, H, self.zoom, self.minSide)
            state["lost"] = 0
        elif "session" not in state or state.get("lost", 0) >= self.patience:
            state = {"session": self.backend.open(), "window": cropWindow(boxPx, W, H, self.zoom, self.minSide), "lost": 0}
            prompt = boxPx
        elif state.get("box"):
            state["window"] = nextWindow(state["window"], state["box"], W, H, self.zoom, self.minSide)
        x0, y0, x1, y1 = state["window"]
        local = None
        if prompt is not None:
            local = (clamp(prompt[0] - x0, 0, x1 - x0 - 1), clamp(prompt[1] - y0, 0, y1 - y0 - 1),
                     clamp(prompt[2] - x0, 1, x1 - x0), clamp(prompt[3] - y0, 1, y1 - y0))
        localPoint = (point[0] - x0, point[1] - y0) if point is not None else None
        alpha = np.zeros((H, W), dtype=np.float32)
        alpha[y0:y1, x0:x1] = self.backend.step(state["session"], np.ascontiguousarray(rgb[y0:y1, x0:x1]), local, localPoint)
        if mask is not None:
            alpha *= sketchGate(mask)
        found = binaryBox(alpha > 0.5, 0.0, W, H)
        if found:
            state["box"], state["lost"] = found, 0
        else:
            state["lost"] = state.get("lost", 0) + 1
        # the first frame pays for the warmup, keep it out of the average
        if self.frames:
            self.seconds += time.perf_counter() - started
        self.frames += 1
        return alpha, state

    def mask(self, rgb: np.ndarray, boxPx: tuple, previous=None) -> tuple:
        """one frame on its own, for callers that only know the box interface."""
        alpha, _state = self.outline(rgb, boxPx, None)
        return alpha, None

    def describe(self) -> dict:
        device = str(getattr(self.backend, "device", "cpu"))
        out = {"quality": "video", "provider": device, "gpu": device != "cpu", "model": getattr(self.backend, "repo", "")}
        if self.frames > 1:
            out["msPerFrame"] = int(round(1000 * self.seconds / (self.frames - 1)))
        return out


def outlineSegmenter(quality: str = "fast"):
    """sam 2 with video memory when torch and transformers are installed, mobilesam at that quality otherwise."""
    try:
        return VideoSegmenter(Sam2Backend())
    except Exception as exc:
        import sys
        print(f"sam 2 unavailable ({type(exc).__name__}: {exc}), outline masks use mobilesam", file=sys.stderr)
        return Segmenter(quality)


fontCandidates = [
    "seguibl.ttf", "impact.ttf", "arialbd.ttf", "segoeuib.ttf",
    "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial Bold.ttf",
]
fontDirs = [
    os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
    "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation", "/Library/Fonts", "/System/Library/Fonts/Supplemental",
]


def findFont(name: str | None) -> str | None:
    if name and os.path.isfile(name):
        return name
    names = ([name] if name else []) + fontCandidates
    for n in names:
        for d in fontDirs:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    return None


def hexColor(value: str, default=(255, 255, 255)) -> tuple:
    try:
        v = value.lstrip("#")
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return default


def normalizeTitle(raw: dict | None, segLength: float) -> dict | None:
    if not raw or not str(raw.get("text", "")).strip():
        return None
    start = clamp(float(raw.get("from", 0.0)), 0.0, segLength)
    end = clamp(float(raw.get("to", segLength)), start, segLength)
    subject = raw.get("subject") or {}
    return {
        "text": str(raw["text"]).strip()[:120],
        "behind": bool(raw.get("behind", False)),
        "subjectBox": normalizeBox(subject["box"]) if subject.get("box") else None,
        "trackSubject": bool(subject.get("track", True)),
        "x": clamp(float(raw.get("x", 0.5)), 0.0, 1.0),
        "y": clamp(float(raw.get("y", 0.35)), 0.0, 1.0),
        "size": clamp(float(raw.get("size", 0.2)), 0.03, 0.6),
        "color": raw.get("color", "#FFFFFF"),
        "stroke": clamp(float(raw.get("stroke", 0.0)), 0.0, 0.2),
        "strokeColor": raw.get("strokeColor", "#000000"),
        "font": raw.get("font"),
        "from": round(start, 3),
        "to": round(end if end > start else segLength, 3),
    }


def renderTitle(title: dict, width: int, height: int, outPath: str) -> str:
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    fontPath = findFont(title.get("font"))
    size = max(8, int(title["size"] * height))

    def load(px):
        return ImageFont.truetype(fontPath, px) if fontPath else ImageFont.load_default(size=px)

    font = load(size)
    stroke = int(title["stroke"] * size)
    while size > 8:
        box = draw.multiline_textbbox((0, 0), title["text"], font=font, align="center", stroke_width=stroke)
        if box[2] - box[0] <= 0.92 * width:
            break
        size = int(size * 0.92)
        font = load(size)
        stroke = int(title["stroke"] * size)
    draw.multiline_text(
        (title["x"] * width, title["y"] * height), title["text"], font=font, anchor="mm", align="center",
        fill=hexColor(title["color"]) + (255,), stroke_width=stroke, stroke_fill=hexColor(title["strokeColor"], (0, 0, 0)) + (255,),
    )
    canvas.save(outPath)
    return outPath


def renderKey(*parts) -> str:
    raw = json.dumps([renderVersion] + list(parts), sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]


def openWriter(path: str, codec: str, rate: Fraction, width: int, height: int, pixFmt: str, options: dict):
    container = av.open(path, mode="w")
    stream = container.add_stream(codec, rate=rate)
    stream.width, stream.height, stream.pix_fmt = width, height, pixFmt
    stream.options = options
    return container, stream


def writeFrame(container, stream, array: np.ndarray, fmt: str, index: int):
    # index unused: frames go in order at a constant rate and the encoder numbers them
    frame = av.VideoFrame.from_ndarray(array, format=fmt)
    for packet in stream.encode(frame):
        container.mux(packet)


def closeWriter(container, stream):
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def renderSegment(mediaPath: str, start: float, end: float, outDir: Path, zones: list | None = None,
                  cutout: dict | None = None, segmenter: Segmenter | None = None, cutoutFps: float = 30.0,
                  onProgress=None, bake: bool = True) -> dict:
    """one decode pass: an optional baked copy with blurred zones, an optional subject cutout with alpha.
    returns {"baked", "cutout", "frames", "fps", "rate", "width", "height", "lostTracks"}."""
    zones = [z for z in (zones or []) if z["to"] >= start and z["from"] <= end]
    outDir = Path(outDir)
    outDir.mkdir(parents=True, exist_ok=True)
    key = renderKey(os.path.abspath(mediaPath), os.path.getsize(mediaPath), round(start, 3), round(end, 3), zones, cutout, bake)
    bakedPath = outDir / f"baked_{key}.mp4" if zones and bake else None
    cutoutPath = outDir / f"cutout_{key}.mov" if cutout else None
    metaPath = outDir / f"render_{key}.json"
    if metaPath.exists():
        meta = json.loads(metaPath.read_text(encoding="utf-8"))
        if (not bakedPath or bakedPath.exists()) and (not cutoutPath or cutoutPath.exists()):
            return meta

    with av.open(mediaPath) as container:
        stream = next(s for s in container.streams if s.type == "video")
        stream.thread_type = "AUTO"
        rate = stream.average_rate or stream.guessed_rate or Fraction(30, 1)
        fps = float(rate)
        firstFrame = int(round(start * fps))
        lastFrame = int(round(end * fps))
        total = max(1, lastFrame - firstFrame)
        container.seek(int(max(0.0, start - 2.0) / stream.time_base), stream=stream, backward=True)

        writers = {}
        trackers = {}
        lost = []
        segModel = segmenter if cutout else None
        if cutout and segModel is None:
            segModel = Segmenter()
        subjectTracker = BoxTracker() if cutout and cutout.get("track", True) else None
        fine = getattr(segModel, "fine", None) is not None
        cutoutEvery = 1 if fine else max(1, int(round(fps / max(1.0, cutoutFps))))
        alpha = None
        lowRes = None
        written = 0
        W = H = None

        for frame in container.decode(stream):
            if frame.time is None:
                continue
            index = int(round(frame.time * fps))
            if index < firstFrame:
                continue
            if index >= lastFrame:
                break
            rgb = frame.to_ndarray(format="rgb24")
            rotation = int(round(getattr(frame, "rotation", 0) or 0)) % 360
            if rotation:
                rgb = np.ascontiguousarray(np.rot90(rgb, k=rotation // 90))
            H, W = rgb.shape[:2]
            t = index / fps

            if not writers:
                if bakedPath:
                    writers["baked"] = openWriter(str(bakedPath), "libx264", rate, W, H, "yuv420p",
                                                  {"crf": "14", "preset": "faster", "g": str(max(1, int(round(fps))))})
                if cutoutPath:
                    writers["cutout"] = openWriter(str(cutoutPath), "prores_ks", rate, W, H, "yuva444p10le",
                                                   {"profile": "4444", "vendor": "apl0"})

            for zi, zone in enumerate(zones):
                if not zoneActive(zone, t):
                    continue
                boxPx = toPixels(boxAt(zone, t), W, H)
                if zone["track"]:
                    tracker = trackers.get(zi)
                    if tracker is None and BoxTracker().available():
                        tracker = BoxTracker()
                        tracker.start(rgb, boxPx)
                        trackers[zi] = tracker
                    elif tracker is not None:
                        tracked = tracker.update(rgb)
                        if tracked:
                            boxPx = tracked
                        elif zi not in lost:
                            lost.append(zi)
                blurRegion(rgb, boxPx, zone)

            if writers.get("baked"):
                writeFrame(*writers["baked"], rgb, "rgb24", written)

            if writers.get("cutout"):
                boxPx = toPixels(cutout["box"], W, H)
                if subjectTracker is not None and subjectTracker.available():
                    if subjectTracker.tracker is None:
                        subjectTracker.start(rgb, boxPx)
                    else:
                        tracked = subjectTracker.update(rgb)
                        if tracked:
                            boxPx = tracked
                if alpha is None or written % cutoutEvery == 0:
                    raw, lowRes = segModel.mask(rgb, boxPx, lowRes)
                    alpha = refineAlpha(raw, alpha, fine=fine)
                rgba = np.dstack([rgb, (alpha * 255).astype(np.uint8)])
                writeFrame(*writers["cutout"], rgba, "rgba", written)

            written += 1
            if onProgress and written % 15 == 0:
                onProgress("render", min(1.0, written / total), None)

        for container_, stream_ in writers.values():
            closeWriter(container_, stream_)

    meta = {
        "baked": str(bakedPath) if bakedPath else None,
        "cutout": str(cutoutPath) if cutoutPath else None,
        "frames": written,
        "fps": fps,
        "rate": [rate.numerator, rate.denominator],
        "width": W,
        "height": H,
        "lostTracks": [zones[i].get("label") or f"zone {i}" for i in lost],
    }
    metaPath.write_text(json.dumps(meta), encoding="utf-8")
    return meta


def zoneMask(height: int, width: int, boxes: list) -> np.ndarray:
    """feathered shapes, 1 inside. boxes: [(boxPx, zone)]."""
    out = np.zeros((height, width), dtype=np.float32)
    for boxPx, zone in boxes:
        x0, y0, x1, y1 = boxPx
        x0, y0, x1, y1 = clamp(x0, 0, width - 1), clamp(y0, 0, height - 1), clamp(x1, 1, width), clamp(y1, 1, height)
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        h, w = y1 - y0, x1 - x0
        shape = featherMask(h, w, zone["shape"], feather=max(2.0, 0.06 * min(w, h)))
        np.maximum(out[y0:y1, x0:x1], shape, out=out[y0:y1, x0:x1])
    return out


def iterFrames(mediaPath: str, start: float, end: float, wantRgb: bool):
    """(index from 0, source time, rgb or None, width, height, rate) for every source frame in [start, end)."""
    with av.open(mediaPath) as container:
        stream = next(s for s in container.streams if s.type == "video")
        stream.thread_type = "AUTO"
        rate = stream.average_rate or stream.guessed_rate or Fraction(30, 1)
        fps = float(rate)
        firstFrame, lastFrame = int(round(start * fps)), int(round(end * fps))
        container.seek(int(max(0.0, start - 2.0) / stream.time_base), stream=stream, backward=True)
        written = 0
        for frame in container.decode(stream):
            if frame.time is None:
                continue
            index = int(round(frame.time * fps))
            if index < firstFrame:
                continue
            if index >= lastFrame:
                break
            rotation = int(round(getattr(frame, "rotation", 0) or 0)) % 360
            W, H = (frame.height, frame.width) if rotation in (90, 270) else (frame.width, frame.height)
            rgb = None
            if wantRgb:
                rgb = frame.to_ndarray(format="rgb24")
                if rotation:
                    rgb = np.ascontiguousarray(np.rot90(rgb, k=rotation // 90))
            yield written, index / fps, rgb, W, H, rate
            written += 1


outlinePoints = 24


def signedArea(poly) -> float:
    total = 0.0
    for (x0, y0), (x1, y1) in zip(poly, list(poly[1:]) + [poly[0]]):
        total += x0 * y1 - x1 * y0
    return total / 2


def orient(points: np.ndarray) -> np.ndarray:
    """one winding for every polygon, the first point kept first: premiere matches vertices by index."""
    if signedArea(points.tolist()) < 0:
        return np.concatenate([points[:1], points[1:][::-1]])
    return points


def resampleClosed(points: np.ndarray, count: int) -> np.ndarray:
    """count points at equal distance along a closed polyline, from its first point."""
    pts = np.asarray(points, dtype=np.float64)
    steps = np.roll(pts, -1, axis=0) - pts
    lengths = np.hypot(steps[:, 0], steps[:, 1])
    total = float(lengths.sum())
    if total <= 0:
        return np.repeat(pts[:1], count, axis=0)
    cum = np.concatenate([[0.0], np.cumsum(lengths)])
    out = []
    for d in np.arange(count) * total / count:
        i = min(int(np.searchsorted(cum, d, side="right")) - 1, len(pts) - 1)
        f = (d - cum[i]) / max(float(lengths[i]), 1e-9)
        out.append(pts[i] + f * steps[i])
    return np.array(out)


def boxPolygon(box, count: int = outlinePoints) -> list:
    """a normalized box as an outline polygon (same vertex count, from the top left), for missed frames."""
    x0, y0, x1, y1 = [float(v) for v in box]
    corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
    return [[round(float(x), 5), round(float(y), 5)] for x, y in resampleClosed(orient(corners), count)]


def offScreenPolygon(count: int = outlinePoints) -> list:
    """a speck far off the frame: a parked mask hides nothing."""
    angles = np.arange(count) * 2 * np.pi / count
    pts = np.stack([-0.25 + 0.01 * np.cos(angles), -0.25 - 0.01 * np.sin(angles)], axis=1)
    return [[round(float(x), 5), round(float(y), 5)] for x, y in orient(pts)]


def boxGap(a, b) -> float:
    """distance between two connected-component stat rows (x, y, w, h, area), 0 when they overlap."""
    dx = max(0, max(a[0], b[0]) - min(a[0] + a[2], b[0] + b[2]))
    dy = max(0, max(a[1], b[1]) - min(a[1] + a[3], b[1] + b[3]))
    return float(np.hypot(dx, dy))


def outlinePolygon(alpha: np.ndarray, boxPx: tuple, width: int, height: int, margin: float = 0.02,
                   count: int = outlinePoints) -> list | None:
    """the thing in the box as a normalized polygon along its silhouette, grown by margin x the box's longer side,
    count points from the topmost one; nearby parts split by a hand share one hull. None when the box is empty."""
    import cv2
    x0, y0, x1, y1 = expandBox(boxPx, width, height)
    region = (alpha[y0:y1, x0:x1] > 0.5).astype(np.uint8)
    if region.sum() < 20:
        return None
    grow = int(round(margin * max(boxPx[2] - boxPx[0], boxPx[3] - boxPx[1])))
    if grow > 0:
        region = cv2.dilate(np.pad(region, grow), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1)))
        x0, y0 = x0 - grow, y0 - grow
    n, labels, stats, _ = cv2.connectedComponentsWithStats(region, connectivity=8)
    areas = stats[1:, cv2.CC_STAT_AREA]
    main = int(np.argmax(areas))
    near = 0.3 * max(boxPx[2] - boxPx[0], boxPx[3] - boxPx[1])
    kept = [i + 1 for i, area in enumerate(areas)
            if area >= 0.15 * areas[main] and boxGap(stats[i + 1], stats[main + 1]) <= near]
    if len(kept) == 1:
        contours, _ = cv2.findContours((labels == kept[0]).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        pts = max(contours, key=cv2.contourArea).reshape(-1, 2).astype(np.float64)
    else:
        ys, xs = np.nonzero(np.isin(labels, kept))
        pts = cv2.convexHull(np.stack([xs, ys], axis=1).astype(np.float32)).reshape(-1, 2).astype(np.float64)
    if len(pts) < 3:
        return None
    pts += (x0 + 0.5, y0 + 0.5)  # pixel indexes to pixel centers
    top = int(np.lexsort((pts[:, 0], pts[:, 1]))[0])
    pts = resampleClosed(orient(np.roll(pts, -top, axis=0)), count)
    return [[round(float(x / width), 5), round(float(y / height), 5)] for x, y in pts]


def followedBox(tracked: tuple, planned: tuple, width: int, height: int) -> tuple:
    """the tracker's center with at least the planned size: csrt shrinks on plain objects and uncovers them."""
    tx0, ty0, tx1, ty1 = tracked
    px0, py0, px1, py1 = planned
    w, h = max(tx1 - tx0, px1 - px0), max(ty1 - ty0, py1 - py0)
    cx, cy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
    return (int(round(cx - w / 2)), int(round(cy - h / 2)), int(round(cx + w / 2)), int(round(cy + h / 2)))


class ZoneFollower:
    """each zone's box on a frame: the planned one, or csrt's when zone.track (the planned one once lost); outline
    zones also get the silhouette from the segmenter."""

    def __init__(self, zones: list, segmenter=None, outlines: bool = True):
        self.zones = zones
        self.segmenter = segmenter
        self.outlines = outlines
        self.trackers = {}
        self.lowRes = {}
        self.state = {}
        self.previous = {}
        self.lost = []

    def shape(self, zi: int, promptPx: tuple, boxPx: tuple, rgb, W: int, H: int) -> list:
        """the silhouette inside promptPx (the tracker's tight box: a wide prompt makes the segmenter pick the
        background); the last one, or the box, when the segmenter finds nothing."""
        if self.segmenter is None:
            self.segmenter = outlineSegmenter()
        box = promptPx
        if hasattr(self.segmenter, "outline"):
            alpha, self.state[zi] = self.segmenter.outline(rgb, promptPx, self.state.get(zi))
            box = self.state[zi].get("box", promptPx)
        else:
            alpha, self.lowRes[zi] = self.segmenter.mask(rgb, promptPx, self.lowRes.get(zi))
        poly = outlinePolygon(alpha, box, W, H)
        if poly is None:
            poly = self.previous.get(zi) or boxPolygon([boxPx[0] / W, boxPx[1] / H, boxPx[2] / W, boxPx[3] / H])
        self.previous[zi] = poly
        return poly

    def at(self, t: float, rgb, W: int, H: int) -> list:
        """[(zone index, box in pixels, zone, polygon or None)] for the zones active at t."""
        out = []
        for zi, zone in enumerate(self.zones):
            if not zoneActive(zone, t):
                continue
            boxPx = toPixels(boxAt(zone, t), W, H)
            promptPx = boxPx
            if zone["track"] and rgb is not None:
                tracker = self.trackers.get(zi)
                if tracker is None and BoxTracker().available():
                    tracker = BoxTracker()
                    tracker.start(rgb, boxPx)
                    self.trackers[zi] = tracker
                elif tracker is not None:
                    tracked = tracker.update(rgb)
                    if tracked:
                        promptPx = tracked
                        boxPx = followedBox(tracked, boxPx, W, H)
                    elif zi not in self.lost:
                        self.lost.append(zi)
            wantShape = self.outlines and zone["shape"] == "outline" and rgb is not None
            shape = self.shape(zi, promptPx, boxPx, rgb, W, H) if wantShape else None
            out.append((zi, boxPx, zone, shape))
        return out

    def lostLabels(self) -> list:
        return [self.zones[i].get("label") or f"zone {i}" for i in self.lost]


def trackZones(mediaPath: str, start: float, end: float, zones: list, segmenter=None) -> dict:
    """zone boxes on every source frame of [start, end), nothing rendered: {"fps", "width", "height", "frames":
    [{"index", "t", "boxes": [[zone index, normalized box]], "shapes": [[zone index, polygon]]}], "lostTracks"}."""
    follower = ZoneFollower(zones, segmenter)
    frames, fps, W, H = [], 30.0, None, None
    needPixels = any(z["track"] or z["shape"] == "outline" for z in zones)
    for _i, t, rgb, W, H, rate in iterFrames(mediaPath, start, end, needPixels):
        fps = float(rate)
        seen = follower.at(t, rgb, W, H)
        boxes = [[zi, [round(x0 / W, 5), round(y0 / H, 5), round(x1 / W, 5), round(y1 / H, 5)]]
                 for zi, (x0, y0, x1, y1), _zone, _shape in seen]
        shapes = [[zi, shape] for zi, _box, _zone, shape in seen if shape is not None]
        frames.append({"index": int(round(t * fps)), "t": round(t, 6), "boxes": boxes, "shapes": shapes})
    return {"fps": fps, "width": W, "height": H, "frames": frames, "lostTracks": follower.lostLabels()}


def renderMatte(mediaPath: str, start: float, end: float, outDir: Path, zones: list | None = None,
                subject: dict | None = None, segmenter=None, cutoutFps: float = 30.0, onProgress=None) -> dict:
    """white matte for a track matte key, alpha where the effect shows: qtrle (lossless, tiny on flat mattes) at
    source size, one frame per source frame so it lines up 1:1 inside the nest. subject: {"box", "track"}.
    returns {"path", "frames", "fps", "width", "height", "lostTracks", "quality"}."""
    zones = [z for z in (zones or []) if z["to"] > start and z["from"] < end]
    outDir = Path(outDir)
    outDir.mkdir(parents=True, exist_ok=True)
    quality = getattr(segmenter, "quality", "fast") if subject else None
    key = renderKey("matte", os.path.abspath(mediaPath), os.path.getsize(mediaPath), round(start, 3), round(end, 3),
                    zones, subject, quality)
    path = outDir / f"matte_{key}.mov"
    metaPath = outDir / f"matte_{key}.json"
    if metaPath.exists() and path.exists():
        return json.loads(metaPath.read_text(encoding="utf-8"))

    needPixels = bool(subject) or any(z["track"] for z in zones)
    if subject and segmenter is None:
        segmenter = Segmenter()
    fine = getattr(segmenter, "fine", None) is not None
    follower = ZoneFollower(zones, outlines=False)
    subjectTracker = BoxTracker() if subject and subject.get("track", True) else None
    total = max(1, int(round((end - start) * 60)))
    writer = rgba = None
    alpha = lowRes = None
    written, fps, W, H, cutoutEvery = 0, 30.0, None, None, 1

    for written, t, rgb, W, H, rate in iterFrames(mediaPath, start, end, needPixels):
        if writer is None:
            fps = float(rate)
            total = max(1, int(round((end - start) * fps)))
            cutoutEvery = 1 if fine else max(1, int(round(fps / max(1.0, cutoutFps))))
            writer = openWriter(str(path), "qtrle", rate, W, H, "argb", {})
        boxes = [(boxPx, zone) for _zi, boxPx, zone, _shape in follower.at(t, rgb, W, H)]
        if subject:
            boxPx = toPixels(subject["box"], W, H)
            if subjectTracker is not None and subjectTracker.available():
                if subjectTracker.tracker is None:
                    subjectTracker.start(rgb, boxPx)
                else:
                    tracked = subjectTracker.update(rgb)
                    if tracked:
                        boxPx = tracked
            if alpha is None or written % cutoutEvery == 0:
                raw, lowRes = segmenter.mask(rgb, boxPx, lowRes)
                alpha = refineAlpha(raw, alpha, fine=fine)
            matte = alpha
        else:
            matte = zoneMask(H, W, boxes)
        if rgba is None:
            rgba = np.full((H, W, 4), 255, dtype=np.uint8)
        rgba[..., 3] = np.clip(matte * 255 + 0.5, 0, 255).astype(np.uint8)
        writeFrame(*writer, rgba, "rgba", written)
        if onProgress and written % 15 == 0:
            onProgress("matte", min(1.0, written / total), None)

    if writer is None:
        raise ValueError(f"no video frames between {start:.2f} and {end:.2f} s")
    closeWriter(*writer)
    meta = {
        "path": str(path),
        "frames": written + 1,
        "fps": fps,
        "width": W,
        "height": H,
        "lostTracks": follower.lostLabels(),
        "quality": quality,
    }
    metaPath.write_text(json.dumps(meta), encoding="utf-8")
    return meta


def readFrame(mediaPath: str, t: float) -> np.ndarray:
    if str(mediaPath).lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")):
        return np.asarray(Image.open(mediaPath).convert("RGB"))
    with av.open(mediaPath) as container:
        stream = next(s for s in container.streams if s.type == "video")
        container.seek(int(max(0.0, t) / stream.time_base), stream=stream, backward=True)
        picked = None
        for frame in container.decode(stream):
            if frame.time is None:
                continue
            picked = frame
            if frame.time >= t:
                break
        if picked is None:
            raise ValueError("no frame at that time")
        rgb = picked.to_ndarray(format="rgb24")
        rotation = int(round(getattr(picked, "rotation", 0) or 0)) % 360
        return np.ascontiguousarray(np.rot90(rgb, k=rotation // 90)) if rotation else rgb


def drawGrid(image: Image.Image) -> Image.Image:
    """10% grid with labels, so coordinates read straight off the picture."""
    image = image.convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    w, h = image.size
    try:
        font = ImageFont.load_default(size=max(12, h // 45))
    except TypeError:
        font = ImageFont.load_default()
    for i in range(1, 10):
        x, y = w * i / 10, h * i / 10
        draw.line([(x, 0), (x, h)], fill=(255, 255, 0, 110), width=1)
        draw.line([(0, y), (w, y)], fill=(255, 255, 0, 110), width=1)
        draw.text((x + 3, 3), f"{i / 10:.1f}", fill=(255, 255, 0, 230), font=font)
        draw.text((3, y + 2), f"{i / 10:.1f}", fill=(255, 255, 0, 230), font=font)
    return image


def previewFrame(mediaPath: str, t: float, zones: list, title: dict | None, outPath: str,
                 seqSize: tuple | None = None, view: dict | None = None, segmenter: Segmenter | None = None,
                 maxSide: int = 1280) -> str:
    """what the viewer will see at that instant: blurred zones, title behind the subject, cropped to the format."""
    rgb = readFrame(mediaPath, t)
    H, W = rgb.shape[:2]
    for zone in zones:
        if zoneActive(zone, t):
            blurRegion(rgb, toPixels(boxAt(zone, t), W, H), zone)
    image = Image.fromarray(rgb).convert("RGBA")

    seqW, seqH = seqSize or (W, H)
    if title:
        # the title is laid out in sequence space, then mapped back onto the source frame
        layer = Image.open(renderTitle(title, seqW, seqH, outPath + ".title.png")).convert("RGBA")
        crop = visibleCrop(W, H, seqW, seqH, view)
        layer = layer.resize((crop[2] - crop[0], crop[3] - crop[1]))
        titleLayer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        titleLayer.paste(layer, (crop[0], crop[1]))
        image = Image.alpha_composite(image, titleLayer)
        if title.get("behind") and title.get("subjectBox"):
            model = segmenter or Segmenter()
            alpha, _ = model.mask(rgb, toPixels(title["subjectBox"], W, H))
            fine = getattr(model, "fine", None) is not None
            subject = Image.fromarray(np.dstack([rgb, (refineAlpha(alpha, None, fine=fine) * 255).astype(np.uint8)]), "RGBA")
            image = Image.alpha_composite(image, subject)

    x0, y0, x1, y1 = visibleCrop(W, H, seqW, seqH, view)
    image = image.crop((x0, y0, x1, y1)).convert("RGB")
    image.thumbnail((maxSide, maxSide))
    image.save(outPath, quality=85)
    return outPath


def visibleCrop(srcW: int, srcH: int, seqW: int, seqH: int, view: dict | None) -> tuple:
    """source pixel rectangle visible in the sequence frame, for a fill + zoom framing."""
    view = view or {"scale": 1.0, "x": 0.5, "y": 0.5}
    fill = max(seqW / srcW, seqH / srcH)
    scale = fill * float(view.get("scale", 1.0))
    visW, visH = seqW / scale, seqH / scale
    cx = clamp(float(view.get("x", 0.5)) * srcW, visW / 2, srcW - visW / 2)
    cy = clamp(float(view.get("y", 0.5)) * srcH, visH / 2, srcH - visH / 2)
    return (int(round(cx - visW / 2)), int(round(cy - visH / 2)), int(round(cx + visW / 2)), int(round(cy + visH / 2)))
