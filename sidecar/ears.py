"""ears: silence map, word-level transcript, utterance split."""

import os
import re
import sys
from pathlib import Path

import numpy as np

import media

# whisper tends to clean disfluencies, a filler-heavy prompt makes it keep them
fillerPrompts = {
    "fr": "Euh, alors, hum, bah, euh... du coup, ben, euh, voilà.",
    "en": "Um, uh, so, umm, like, uh... you know, hmm, uh.",
    "es": "Eh, este, mmm, bueno, o sea, eh...",
    "de": "Äh, ähm, also, hm, äh...",
    "it": "Ehm, cioè, allora, ehm, mmm...",
    "pt": "É, tipo, hum, então, né, é...",
}

fillerWords = {
    "fr": {"euh", "heu", "hum", "hmm", "bah", "beuh", "mmh"},
    "en": {"um", "uh", "umm", "uhm", "erm", "er", "hmm", "mm", "mmm", "ah"},
    "es": {"eh", "em", "mmm"},
    "de": {"äh", "ähm", "hm", "hmm"},
    "it": {"ehm", "eh", "mmm"},
    "pt": {"hum", "ahn"},
}

sentenceEnd = re.compile(r"[.!?…]$")


def autoThreshold(db: np.ndarray) -> float:
    # sit between room tone and speech level, robust to noisy home studios
    noiseFloor = float(np.percentile(db, 10))
    speechLevel = float(np.percentile(db, 90))
    threshold = noiseFloor + (speechLevel - noiseFloor) * 0.3
    return float(min(max(threshold, -60.0), -20.0))


def detectSilences(mediaPath: str, thresholdDb: float | None = None, minSilenceMs: int = 350,
                   track: int = 1) -> dict:
    db, windowSec = media.loudnessProfile(mediaPath, track=track)
    duration = len(db) * windowSec
    if not len(db):
        return {"thresholdDb": None, "silences": [], "duration": 0.0}
    threshold = autoThreshold(db) if thresholdDb is None else float(thresholdDb)
    quiet = db < threshold
    minWindows = max(1, int(round(minSilenceMs / 1000 / windowSec)))

    silences = []
    runStart = None
    for idx, isQuiet in enumerate(quiet):
        if isQuiet and runStart is None:
            runStart = idx
        elif not isQuiet and runStart is not None:
            if idx - runStart >= minWindows:
                silences.append([round(runStart * windowSec, 3), round(idx * windowSec, 3)])
            runStart = None
    if runStart is not None and len(quiet) - runStart >= minWindows:
        silences.append([round(runStart * windowSec, 3), round(duration, 3)])

    return {"thresholdDb": round(threshold, 1), "silences": silences, "duration": round(duration, 3)}


def addCudaDllDirs():
    # pip nvidia wheels are not on the windows dll path by default
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return
    for pkg in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime"):
        try:
            mod = __import__(pkg, fromlist=["__path__"])
            for base in mod.__path__:
                binDir = Path(base) / "bin"
                if binDir.is_dir():
                    os.add_dll_directory(str(binDir))
        except Exception:
            continue


modelCache = {}


def cudaAvailable() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def loadWhisper(modelName: str = "auto", device: str = "auto"):
    from faster_whisper import WhisperModel

    addCudaDllDirs()
    attempts = []
    # check the gpu first, otherwise auto downloads the 1.6 gb turbo model for nothing
    if device == "auto" and not cudaAvailable():
        device = "cpu"
    if device in ("auto", "cuda"):
        attempts.append(("cuda", "float16", "large-v3-turbo" if modelName == "auto" else modelName))
    if device in ("auto", "cpu"):
        attempts.append(("cpu", "int8", "small" if modelName == "auto" else modelName))

    lastError = None
    for dev, computeType, name in attempts:
        key = (dev, computeType, name)
        if key in modelCache:
            return modelCache[key], dev, name
        try:
            model = WhisperModel(name, device=dev, compute_type=computeType)
            modelCache[key] = model
            return model, dev, name
        except Exception as err:  # no gpu or missing cudnn, fall back
            lastError = err
    raise RuntimeError(f"could not load whisper: {lastError}")


def detectLanguage(model, audio) -> str | None:
    try:
        sample = audio[: 30 * 16000]
        if not len(sample):
            return None
        lang, _prob, _all = model.detect_language(audio=sample)
        return lang
    except Exception:
        return None


def transcribe(mediaPath: str, language: str = "auto", modelName: str = "auto", device: str = "auto",
               keepFillers: bool = True, onProgress=None, track: int = 1) -> dict:
    model, usedDevice, usedModel = loadWhisper(modelName, device)
    # decode ourselves: faster-whisper's own decoder breaks on recent pyav releases
    audio = media.decodeAudioMono(mediaPath, 16000, track=track)
    if not len(audio):
        raise RuntimeError("no audio stream in this file")
    lang = None if language in (None, "", "auto") else language
    if lang is None and keepFillers:
        lang = detectLanguage(model, audio)

    prompt = fillerPrompts.get(lang or "", None) if keepFillers else None
    segments, info = model.transcribe(
        audio,
        language=lang,
        word_timestamps=True,
        initial_prompt=prompt,
        vad_filter=False,
        condition_on_previous_text=False,
    )

    words = []
    totalDuration = info.duration or 1.0
    for segment in segments:
        for w in segment.words or []:
            text = w.word.strip()
            if not text:
                continue
            words.append({
                "text": text,
                "start": round(float(w.start), 3),
                "end": round(float(w.end), 3),
                "prob": round(float(w.probability), 3),
            })
        if onProgress:
            onProgress(min(segment.end / totalDuration, 1.0))

    return {
        "language": info.language,
        "duration": round(float(info.duration), 3),
        "device": usedDevice,
        "model": usedModel,
        "words": words,
    }


def normalizeWord(text: str) -> str:
    return re.sub(r"[^\w']", "", text.lower(), flags=re.UNICODE)


def isFiller(text: str, language: str | None) -> bool:
    word = normalizeWord(text)
    if not word:
        return False
    pool = fillerWords.get(language or "", set()) | fillerWords["en"] | fillerWords["fr"]
    return word in pool


def buildUtterances(words: list, maxGap: float = 0.45, maxLen: float = 15.0) -> list:
    """split on pauses and sentence ends, small enough to isolate false starts."""
    utterances = []
    current = []

    def flush():
        if not current:
            return
        utterances.append({
            "id": len(utterances),
            "start": current[0]["start"],
            "end": current[-1]["end"],
            "text": " ".join(w["text"] for w in current),
            "firstWord": current[0]["index"],
            "lastWord": current[-1]["index"],
        })
        current.clear()

    for idx, word in enumerate(words):
        entry = dict(word, index=idx)
        if current:
            gap = word["start"] - current[-1]["end"]
            tooLong = word["end"] - current[0]["start"] > maxLen
            if gap > maxGap or sentenceEnd.search(current[-1]["text"]) or tooLong:
                flush()
        current.append(entry)
    flush()
    return utterances


def fillerRanges(words: list, language: str | None) -> list:
    return [[w["start"], w["end"]] for w in words if isFiller(w["text"], language)]
