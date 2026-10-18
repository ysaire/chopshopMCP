"""probe and decode rushes with pyav, no ffmpeg binary needed."""

from fractions import Fraction

import av
import numpy as np


imageExtensions = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")


def isImage(mediaPath: str) -> bool:
    return str(mediaPath).lower().endswith(imageExtensions)


def probe(mediaPath: str) -> dict:
    with av.open(mediaPath) as container:
        videoStream = next((s for s in container.streams if s.type == "video"), None)
        audioStream = next((s for s in container.streams if s.type == "audio"), None)

        duration = None
        if container.duration:
            duration = container.duration / av.time_base
        elif videoStream is not None and videoStream.duration:
            duration = float(videoStream.duration * videoStream.time_base)
        elif audioStream is not None and audioStream.duration:
            duration = float(audioStream.duration * audioStream.time_base)

        info = {
            "path": mediaPath,
            "duration": duration or 0.0,
            # a picture: no time of its own, held as long as the edit wants
            "still": isImage(mediaPath) or (videoStream is not None and audioStream is None and not duration),
            "hasVideo": videoStream is not None,
            "hasAudio": audioStream is not None,
            "fps": None,
            "width": None,
            "height": None,
            "rotation": 0,
            "sampleRate": None,
            "channels": None,
        }

        if videoStream is not None:
            rate = videoStream.average_rate or videoStream.guessed_rate or Fraction(30, 1)
            info["fps"] = float(rate)
            info["width"] = videoStream.codec_context.width
            info["height"] = videoStream.codec_context.height
            info["rotation"] = readRotation(container, videoStream)
            # phone footage shot vertical is stored landscape + rotation flag
            if info["rotation"] in (90, 270, -90, -270):
                info["width"], info["height"] = info["height"], info["width"]

        if audioStream is not None:
            ctx = audioStream.codec_context
            info["sampleRate"] = ctx.sample_rate
            info["channels"] = getattr(ctx, "channels", None) or len(ctx.layout.channels)

        # obs often records mic and desktop as separate streams
        info["audioStreams"] = [
            {
                "index": s.index,
                "channels": getattr(s.codec_context, "channels", None) or len(s.codec_context.layout.channels),
                "sampleRate": s.codec_context.sample_rate,
                "title": s.metadata.get("title") or s.metadata.get("handler_name") or "",
            }
            for s in container.streams if s.type == "audio"
        ]

        return info


def kindOf(mediaPath: str) -> dict:
    """video, audio, image, missing or unknown, with the duration of what plays and the size of what shows."""
    import os
    if not os.path.isfile(mediaPath):
        return {"kind": "missing"}
    try:
        info = probe(mediaPath)
    except Exception:
        return {"kind": "unknown"}
    if info["still"]:
        kind = "image"
    elif info["hasVideo"]:
        kind = "video"
    elif info["hasAudio"]:
        kind = "audio"
    else:
        return {"kind": "unknown"}
    out = {"kind": kind}
    if kind != "image":
        out["duration"] = round(float(info["duration"]), 2)
    if kind != "audio":
        out["size"] = [info["width"], info["height"]]
    return out


def readRotation(container, videoStream) -> int:
    try:
        for frame in container.decode(videoStream):
            return int(round(getattr(frame, "rotation", 0) or 0))
    except Exception:
        pass
    return 0


def pickAudioStream(container, track: int = 1):
    """track is 1-based like premiere a1/a2, falls back to the first stream."""
    streams = [s for s in container.streams if s.type == "audio"]
    if not streams:
        return None
    index = int(track or 1) - 1
    return streams[index] if 0 <= index < len(streams) else streams[0]


def iterAudioMono(mediaPath: str, sampleRate: int = 16000, maxSeconds: float | None = None, track: int = 1):
    """yields float32 mono chunks, keeps memory flat on long podcasts."""
    with av.open(mediaPath) as container:
        audioStream = pickAudioStream(container, track)
        if audioStream is None:
            return
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=sampleRate)
        emitted = 0
        limit = int(maxSeconds * sampleRate) if maxSeconds else None
        for frame in container.decode(audioStream):
            for outFrame in resampler.resample(frame):
                chunk = outFrame.to_ndarray().reshape(-1).astype(np.float32)
                if limit is not None and emitted + len(chunk) >= limit:
                    yield chunk[: limit - emitted]
                    return
                emitted += len(chunk)
                yield chunk
        for outFrame in resampler.resample(None):
            yield outFrame.to_ndarray().reshape(-1).astype(np.float32)


def decodeAudioMono(mediaPath: str, sampleRate: int = 16000, maxSeconds: float | None = None,
                    track: int = 1) -> np.ndarray:
    chunks = list(iterAudioMono(mediaPath, sampleRate, maxSeconds, track))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks)


def loudnessProfile(mediaPath: str, windowMs: int = 10, sampleRate: int = 16000, track: int = 1):
    """rms level in dbfs per window, returns (dbArray, windowSec)."""
    windowSize = int(sampleRate * windowMs / 1000)
    levels = []
    carry = np.zeros(0, dtype=np.float32)
    for chunk in iterAudioMono(mediaPath, sampleRate, track=track):
        buf = np.concatenate([carry, chunk]) if len(carry) else chunk
        usable = (len(buf) // windowSize) * windowSize
        if usable:
            blocks = buf[:usable].reshape(-1, windowSize)
            rms = np.sqrt(np.mean(blocks.astype(np.float64) ** 2, axis=1))
            levels.append(20.0 * np.log10(np.maximum(rms, 1e-7)))
        carry = buf[usable:]
    if len(carry):
        rms = np.sqrt(np.mean(carry.astype(np.float64) ** 2))
        levels.append(np.array([20.0 * np.log10(max(rms, 1e-7))]))
    db = np.concatenate(levels) if levels else np.zeros(0)
    return db, windowSize / sampleRate
