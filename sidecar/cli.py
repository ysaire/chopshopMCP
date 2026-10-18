"""cli used by the premiere panel. every stdout line is one json event."""

import argparse
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# windows consoles default to cp1252, transcripts are not
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def emit(event: dict):
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def progress(stage, pct=None, message=None):
    emit({"type": "progress", "stage": stage, "pct": pct, "message": message})


def cmdCheck(args):
    report = {"python": sys.version.split()[0]}
    for mod in ("av", "numpy", "faster_whisper", "mcp", "cv2", "onnxruntime", "yt_dlp"):
        try:
            __import__(mod)
            report[mod] = True
        except Exception as err:
            report[mod] = f"missing: {err}"
    from config import appDir, loadConfig
    cfg = loadConfig()
    report["appDir"] = str(appDir())
    report["maskMode"] = cfg.get("maskMode")
    import shutil
    # references from youtube need ffmpeg to merge 720p and a js runtime for signatures
    report["referenceTools"] = {name: bool(shutil.which(name)) for name in ("ffmpeg", "node", "deno")}
    try:
        import masks
        providers = masks.onnxProviders()
        report["onnxProviders"] = [masks.providerNames[p] for p in providers]
        gpu = providers[0] != "CPUExecutionProvider"
        quality = cfg.get("cutoutQuality") or "auto"
        # what the subject matte will use, without loading the models
        report["cutoutQuality"] = quality if quality in ("fast", "best") else ("best" if gpu else "fast")
    except Exception as err:
        report["onnxProviders"] = f"error: {err}"
    emit({"type": "result", "data": report})


def cmdProbe(args):
    import pipeline
    emit({"type": "result", "data": pipeline.getProbe(args.media)})


def cmdAnalyze(args):
    import pipeline
    from config import loadConfig
    cfg = loadConfig()
    cfg.update(json.loads(args.optionsJson or "{}"))
    info = pipeline.getProbe(args.media)
    silences = pipeline.getSilences(args.media, cfg.get("thresholdDb"), int(cfg.get("minSilenceMs", 350)), progress,
                                    int(cfg.get("voiceTrack") or 1))
    data = {"probe": info, "silences": silences}
    if not args.skipTranscript:
        transcript = pipeline.getTranscript(args.media, cfg, progress)
        data["utterances"] = transcript["utterances"]
        data["language"] = transcript["language"]
    emit({"type": "result", "data": data})


def cmdRoughCut(args):
    import pipeline
    options = json.loads(args.optionsJson or "{}")
    result = pipeline.roughCut(args.media, mode=args.mode, options=options, onProgress=progress)
    emit({"type": "result", "data": result})


def cmdPerceive(args):
    import pipeline
    from config import loadConfig
    cfg = loadConfig()
    cfg.update(json.loads(args.optionsJson or "{}"))
    result = pipeline.getPerception(args.media, cfg, progress, force=args.force)
    emit({"type": "result", "data": {"stats": result["stats"], "duration": result["duration"]}})


def cmdFrames(args):
    import eyes
    from config import mediaCacheDir
    times = [float(t) for t in args.times.split(",") if t.strip()]
    outDir = mediaCacheDir(args.media) / "frames"
    emit({"type": "result", "data": eyes.grabFrames(args.media, times, str(outDir))})


def cmdRefine(args):
    """the panel's Refine mask button: the sketch on the selected clip, between the in and out points."""
    import masks
    import sketch
    from bridgeClient import callPremiere
    options = json.loads(args.optionsJson or "{}")
    known = ("sequenceId", "track", "index", "inPoint", "outPoint", "effect")
    result = sketch.refineSketch(callPremiere, masks.outlineSegmenter(), onProgress=progress,
                                 **{k: v for k, v in options.items() if k in known})
    emit({"type": "result", "data": result})


def cmdSelfTest(args):
    import selftest
    from config import cacheDir
    emit({"type": "result", "data": selftest.build(args.out or str(cacheDir() / "selftest"))})


def main():
    parser = argparse.ArgumentParser(prog="chopshopMCP")
    subs = parser.add_subparsers(dest="command", required=True)

    subs.add_parser("check")

    p = subs.add_parser("probe")
    p.add_argument("--media", required=True)

    p = subs.add_parser("analyze")
    p.add_argument("--media", required=True)
    p.add_argument("--optionsJson")
    p.add_argument("--skipTranscript", action="store_true")

    p = subs.add_parser("roughCut")
    p.add_argument("--media", required=True)
    p.add_argument("--mode", choices=["silences"], default="silences")
    p.add_argument("--optionsJson")

    p = subs.add_parser("perceive")
    p.add_argument("--media", required=True)
    p.add_argument("--optionsJson")
    p.add_argument("--force", action="store_true")

    p = subs.add_parser("frames")
    p.add_argument("--media", required=True)
    p.add_argument("--times", required=True, help="comma separated seconds")

    p = subs.add_parser("selftest")
    p.add_argument("--out")

    p = subs.add_parser("refine")
    p.add_argument("--optionsJson", help="sequenceId, track, index, inPoint, outPoint, effect")

    args = parser.parse_args()
    handlers = {
        "check": cmdCheck,
        "selftest": cmdSelfTest,
        "probe": cmdProbe,
        "analyze": cmdAnalyze,
        "roughCut": cmdRoughCut,
        "frames": cmdFrames,
        "perceive": cmdPerceive,
        "refine": cmdRefine,
    }
    try:
        handlers[args.command](args)
    except Exception as err:
        emit({"type": "error", "message": str(err), "trace": traceback.format_exc()})
        sys.exit(1)


if __name__ == "__main__":
    main()
