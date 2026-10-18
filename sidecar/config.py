"""shared settings for panel, cli and mcp server."""

import hashlib
import json
import os
from pathlib import Path

defaultConfig = {
    # auto = large-v3-turbo on gpu, small on cpu
    "whisperModel": "auto",
    "whisperDevice": "auto",
    "language": "auto",
    "padMs": 120,
    "minSilenceMs": 350,
    "thresholdDb": None,
    "removeFillers": True,
    # audio track whisper listens to (1 = a1). obs track 1 is usually the full mix
    "voiceTrack": 1,
    # native = premiere pen masks, matte = rendered mattes keyed by premiere, baked = pixels rendered here
    "maskMode": "native",
    # native mask feather and expansion in pixels at 1080p, scaled with the source height
    "maskFeather": 30,
    "maskExpansion": 12,
    # subject matte: auto = birefnet edges when a gpu runs onnx, fast = mobilesam only, best = always birefnet
    "cutoutQuality": "auto",
    # track matte key "matte" popup value = track number + offset, None = probe it in premiere
    "matteOffset": None,
    "pythonPath": "python",
    # accent colour for every graphics template, None = the template's own
    "graphicsAccent": None,
}


pluginVersion = "1.2.1"


def appDir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    path = Path(base) / "chopshopMCP"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cacheDir() -> Path:
    path = appDir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def configPath() -> Path:
    return appDir() / "config.json"


def loadConfig() -> dict:
    cfg = dict(defaultConfig)
    try:
        with open(configPath(), "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    except (OSError, ValueError):
        pass
    return cfg


def mediaKey(mediaPath: str) -> str:
    # cache key changes when the rush is replaced or re-exported
    st = os.stat(mediaPath)
    raw = f"{os.path.abspath(mediaPath)}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def mediaCacheDir(mediaPath: str) -> Path:
    path = cacheDir() / mediaKey(mediaPath)
    path.mkdir(parents=True, exist_ok=True)
    return path


def promptsDir() -> Path:
    return Path(__file__).resolve().parent / "prompts"
