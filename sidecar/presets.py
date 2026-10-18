"""effect presets (.prfpset): read the user's packs, list them for claude, rebuild one on a clip.

premiere has no api to apply a custom preset, so a preset is replayed: each effect is added by
name, static values are set, animated values become keyframes. premiere's api can't set bezier
handles, so eased curves are evaluated here (after effects speed / influence model) and written
as one keyframe per frame; linear stretches keep only their real keys.

a .prfpset is premiere's object graph in xml: TreeItem (preset) -> FilterPresetItem ->
FilterPreset (one per effect: anchor type, anchor in / out) -> Video|AudioFilterComponent -> params.
static values live in StartKeyframe, animated ones in Keyframes, times in ticks of media time.
"""

import glob
import hashlib
import json
import math
import os
import xml.etree.ElementTree as et
from pathlib import Path

ticksPerSecond = 254016000000
anchorTypes = {"0": "scale", "1": "in", "2": "out"}
interpNames = {"0": "linear", "5": "bezier", "4": "hold", "1": "hold"}
intrinsicEffects = {"AE.ADBE Motion", "AE.ADBE Opacity"}
builtinPrefixes = ("AE.ADBE", "PR.ADBE", "AE.Mettle", "ADBE")


def parseColor(raw: str) -> list:
    """64 bit argb, 16 bits per channel -> [a, r, g, b] 0..255."""
    v = int(raw)
    return [((v >> shift) & 0xFFFF) >> 8 for shift in (48, 32, 16, 0)]


def parseValue(raw: str, kind: str):
    if raw is None:
        return None
    raw = raw.strip()
    if kind == "point":
        x, y = raw.split(":")
        return [float(x), float(y)]
    if kind == "bool":
        return raw == "true"
    if kind == "color":
        return parseColor(raw)
    if kind in ("int", "popup"):
        return int(float(raw))
    return float(raw)


def paramKind(node) -> str:
    if node.tag == "PointComponentParam":
        return "point"
    if node.tag == "ArbVideoComponentParam":
        return "arb"
    control = node.findtext("ParameterControlType")
    cls = (node.get("ClassID") or "")[:8]
    if cls == "0fde4e9f" or control == "5":
        return "color"
    if cls == "cc12343e" or control == "4":
        return "bool"
    if control == "7":
        return "popup"
    if cls == "6e02e8bb":
        return "int"
    return "float"


def parseKeys(raw: str, kind: str) -> list:
    keys = []
    for chunk in (raw or "").split(";"):
        f = chunk.split(",")
        if len(f) < 2 or not f[0].strip():
            continue
        key = {"t": int(f[0]) / ticksPerSecond, "v": parseValue(f[1], kind),
               "interp": interpNames.get(f[2], "bezier") if len(f) > 2 else "linear"}
        if len(f) >= 8:
            key.update(inSpeed=float(f[4]), inInfluence=float(f[5]), outSpeed=float(f[6]), outInfluence=float(f[7]))
        if kind == "point" and len(f) >= 14:
            key.update(inTangent=[float(f[10]), float(f[11])], outTangent=[float(f[12]), float(f[13])])
        keys.append(key)
    return keys


def loadPack(path: str) -> list:
    """every preset in a .prfpset, with its bin path."""
    root = et.parse(path).getroot()
    byId = {o.get("ObjectID"): o for o in root if o.get("ObjectID")}
    ref = lambda node: byId.get(node.get("ObjectRef")) if node is not None else None
    presets = []

    def walk(item, folder):
        name = (item.findtext("TreeItemBase/Name") or "").strip()
        if item.tag == "BinTreeItem":
            sub = folder if name in ("Root", "Presets", "") and not folder else folder + [name]
            for child in item.findall("Items/Item"):
                node = ref(child)
                if node is not None:
                    walk(node, sub)
        elif item.tag == "TreeItem":
            data = ref(item.find("TreeItemBase/Data"))
            if data is not None and data.tag == "FilterPresetItem":
                presets.append(readPreset(data, name, folder, ref, os.path.basename(path)))

    tree = next((o for o in root if o.tag == "Tree" and o.get("ObjectID")), None)
    if tree is not None:
        walk(ref(tree.find("RootBin")), [])
    return presets


def readPreset(item, name: str, folder: list, ref, packName: str) -> dict:
    effects, issues = [], []
    anchor, anchorIn, anchorOut = "in", 0.0, 0.0
    for fp in item.findall("FilterPresets/FilterPreset"):
        preset = ref(fp)
        comp = ref(preset.find("Component"))
        if comp is None:
            continue
        anchor = anchorTypes.get(preset.findtext("Type"), "in")
        anchorIn = int(preset.findtext("AnchorInPoint") or 0) / ticksPerSecond
        anchorOut = int(preset.findtext("AnchorOutPoint") or 0) / ticksPerSecond
        matchName = comp.findtext("MatchName") or preset.findtext("FilterMatchName") or ""
        effect = {
            "matchName": matchName,
            "displayName": (comp.findtext("Component/DisplayName") or matchName.replace("AE.ADBE ", "")).strip(),
            "audio": comp.tag == "AudioFilterComponent",
            "intrinsic": matchName in intrinsicEffects or comp.findtext("Component/Intrinsic") == "true",
            # mettle vr effects ship with premiere
            "thirdParty": not matchName.startswith(builtinPrefixes) and comp.tag != "AudioFilterComponent",
            "anchor": anchor, "anchorIn": anchorIn, "anchorOut": anchorOut,
            "params": [],
        }
        for index, p in enumerate(comp.findall("Component/Params/Param")):
            node = ref(p)
            if node is None:
                continue
            kind = paramKind(node)
            param = {"index": index, "name": (node.findtext("Name") or "").strip(), "kind": kind}
            if kind == "arb":
                # curves, masks, luts, text: opaque blobs the scripting api can't write
                param["opaque"] = bool((node.findtext("StartKeyframeValue") or "").strip())
            else:
                start = node.findtext("StartKeyframe") or ""
                param["value"] = parseValue(start.split(",")[1], kind) if "," in start else None
                if node.findtext("IsTimeVarying") == "true":
                    param["keys"] = parseKeys(node.findtext("Keyframes"), kind)
            effect["params"].append(param)
        if effect["audio"]:
            # audition effects are stored by guid with their settings in a private blob
            if "audio" not in " ".join(issues):
                issues.append("audio presets can't be replayed by script yet, use named audio effects instead")
        elif effect["thirdParty"]:
            issues.append(f"{effect['displayName']} is a third party effect, it must be installed")
        opaque = [p["name"] or f"#{p['index']}" for p in effect["params"] if p["kind"] == "arb" and p.get("opaque")]
        if opaque and effect["matchName"] != "AE.ADBE Lumetri":
            issues.append(f"{effect['displayName']}: {', '.join(opaque)} can't be set by script")
        elif opaque:
            issues.append("Lumetri curves, looks and luts can't be set by script, sliders are")
        effects.append(effect)
    animated = [p for e in effects for p in e["params"] if p.get("keys")]
    times = [k["t"] for p in animated for k in p["keys"]]
    return {
        "name": name,
        "folder": "/".join(folder),
        "pack": packName,
        "id": "/".join(folder + [name]),
        "anchor": anchor,
        "duration": round(max(times) - min(times), 3) if times else 0.0,
        "audio": bool(effects) and all(e["audio"] for e in effects),
        "effects": effects,
        "issues": issues,
    }


def profilePresetFiles() -> list:
    """premiere's own preset file(s) in every profile, newest version first."""
    docs = Path(os.environ.get("USERPROFILE") or Path.home()) / "Documents" / "Adobe" / "Premiere Pro"
    found = glob.glob(str(docs / "*" / "Profile-*" / "*.prfpset"))
    return sorted(found, key=lambda p: os.path.getmtime(p), reverse=True)


def libraryFiles(extra: list | None = None) -> list:
    files = []
    for entry in (extra or []):
        entry = os.path.expandvars(os.path.expanduser(str(entry)))
        if os.path.isdir(entry):
            files += sorted(glob.glob(os.path.join(entry, "**", "*.prfpset"), recursive=True))
        elif os.path.isfile(entry):
            files.append(entry)
    newest = profilePresetFiles()[:1]
    return list(dict.fromkeys(newest + files))


def loadLibrary(files: list, cacheDir: Path | None = None) -> list:
    """all presets of all files, parsed once per file version."""
    out, seen = [], set()
    for path in files:
        try:
            st = os.stat(path)
        except OSError:
            continue
        key = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}|2".encode()).hexdigest()[:16]
        cachePath = Path(cacheDir) / f"presets_{key}.json" if cacheDir else None
        presets = None
        if cachePath and cachePath.exists():
            try:
                presets = json.loads(cachePath.read_text(encoding="utf-8"))
            except ValueError:
                presets = None
        if presets is None:
            presets = loadPack(path)
            if cachePath:
                cachePath.parent.mkdir(parents=True, exist_ok=True)
                cachePath.write_text(json.dumps(presets), encoding="utf-8")
        for p in presets:
            # the same pack imported in premiere and kept as a file: list it once
            sig = (p["id"], p["anchor"], round(p["duration"], 3), len(p["effects"]))
            if sig not in seen:
                seen.add(sig)
                out.append(p)
    return out


def summary(preset: dict) -> str:
    parts = []
    for e in preset["effects"]:
        animated = [p["name"] or f"#{p['index']}" for p in e["params"] if p.get("keys")]
        parts.append(e["displayName"] + (f" ({', '.join(animated)})" if animated else ""))
    timing = f"{preset['duration']:.2f}s from the {preset['anchor']}" if preset["duration"] else "static"
    flag = "" if not preset["issues"] else " | partial: " + "; ".join(preset["issues"])
    kind = "audio" if preset["audio"] else "video"
    return f"{preset['id']} | {kind} | {timing} | {' + '.join(parts)}{flag}"


def find(library: list, query: str) -> dict:
    """exact id, then exact name, then a unique substring match."""
    q = query.strip().lower()
    for test in (lambda p: p["id"].lower() == q, lambda p: p["name"].lower() == q):
        hits = [p for p in library if test(p)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ValueError(f"several presets match {query!r}: " + ", ".join(h["id"] for h in hits[:8]))
    hits = [p for p in library if q in p["id"].lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ValueError(f"no preset matches {query!r}")
    raise ValueError(f"several presets match {query!r}: " + ", ".join(h["id"] for h in hits[:8]))


def bezier(p0: float, p1: float, p2: float, p3: float, u: float) -> float:
    w = 1 - u
    return w * w * w * p0 + 3 * w * w * u * p1 + 3 * w * u * u * p2 + u * u * u * p3


def solveU(a: dict, b: dict, t: float) -> float:
    """bezier parameter at time t, time handles from the keys' influences."""
    d = b["t"] - a["t"]
    ia = min(1.0, max(0.01, a.get("outInfluence", 1 / 3)))
    ib = min(1.0, max(0.01, b.get("inInfluence", 1 / 3)))
    target = min(1.0, max(0.0, (t - a["t"]) / d))
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if bezier(0, ia, 1 - ib, 1, mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def easedValue(a: dict, b: dict, t: float, start: float, end: float) -> float:
    """after effects temporal ease: value handles = speed x influence x duration, so overshoots work too."""
    d = b["t"] - a["t"]
    if d <= 0:
        return end
    if a.get("interp") == "hold":
        return start
    if "outSpeed" not in a or a.get("interp") == "linear" and b.get("interp") == "linear":
        return start + (end - start) * min(1.0, max(0.0, (t - a["t"]) / d))
    ia = min(1.0, max(0.01, a.get("outInfluence", 1 / 3)))
    ib = min(1.0, max(0.01, b.get("inInfluence", 1 / 3)))
    v1 = start + a.get("outSpeed", 0.0) * ia * d
    v2 = end - b.get("inSpeed", 0.0) * ib * d
    return bezier(start, v1, v2, end, solveU(a, b, t))


def pathPoints(a: dict, b: dict, steps: int = 64) -> list:
    p0, p3 = a["v"], b["v"]
    p1 = [p0[i] + a.get("outTangent", [0, 0])[i] for i in (0, 1)]
    p2 = [p3[i] + b.get("inTangent", [0, 0])[i] for i in (0, 1)]
    return [[bezier(p0[i], p1[i], p2[i], p3[i], s / steps) for i in (0, 1)] for s in range(steps + 1)]


def valueAt(keys: list, t: float):
    if t <= keys[0]["t"]:
        return keys[0]["v"]
    if t >= keys[-1]["t"]:
        return keys[-1]["v"]
    for a, b in zip(keys, keys[1:]):
        if a["t"] <= t <= b["t"]:
            if isinstance(a["v"], bool) or isinstance(a["v"], int) and not isinstance(a["v"], float):
                return a["v"] if t < b["t"] else b["v"]
            if isinstance(a["v"], list):
                path = pathPoints(a, b)
                lengths = [0.0]
                for p, q in zip(path, path[1:]):
                    lengths.append(lengths[-1] + math.dist(p, q))
                total = lengths[-1]
                if total < 1e-12:
                    return list(a["v"])
                goal = min(total, max(0.0, easedValue(a, b, t, 0.0, total)))
                for i in range(1, len(path)):
                    if lengths[i] >= goal:
                        f = (goal - lengths[i - 1]) / max(1e-12, lengths[i] - lengths[i - 1])
                        return [path[i - 1][j] + f * (path[i][j] - path[i - 1][j]) for j in (0, 1)]
                return list(b["v"])
            return easedValue(a, b, t, a["v"], b["v"])
    return keys[-1]["v"]


def segmentIsLinear(a: dict, b: dict) -> bool:
    if a.get("interp") == "hold":
        return False
    if isinstance(a["v"], list):
        tangents = a.get("outTangent", [0, 0]) + b.get("inTangent", [0, 0])
        if any(abs(x) > 1e-6 for x in tangents):
            return False
        span = math.dist(a["v"], b["v"])
    elif isinstance(a["v"], (bool, int)) and not isinstance(a["v"], float):
        return True
    else:
        span = abs(b["v"] - a["v"])
    if a.get("interp") == "linear" and b.get("interp") == "linear":
        return True
    d = b["t"] - a["t"]
    slope = span / d if d > 0 else 0.0
    tol = max(1e-6, 0.01 * slope)
    return abs(a.get("outSpeed", slope) - slope) <= tol and abs(b.get("inSpeed", slope) - slope) <= tol


def anchorRatio(effect: dict, clipIn: float, clipOut: float) -> float:
    """clip media seconds per preset second: 1, or the stretch of a "scale" preset."""
    if effect["anchor"] == "scale" and effect["anchorOut"] > effect["anchorIn"]:
        return (clipOut - clipIn) / (effect["anchorOut"] - effect["anchorIn"])
    return 1.0


def mapTime(t: float, effect: dict, clipIn: float, clipOut: float) -> float:
    """preset media time -> the target clip's media time, following the preset's anchor."""
    if effect["anchor"] == "out":
        return clipOut + (t - effect["anchorOut"])
    return clipIn + (t - effect["anchorIn"]) * anchorRatio(effect, clipIn, clipOut)


def unmapTime(m: float, effect: dict, clipIn: float, clipOut: float) -> float:
    if effect["anchor"] == "out":
        return effect["anchorOut"] + (m - clipOut)
    return effect["anchorIn"] + (m - clipIn) / max(1e-9, anchorRatio(effect, clipIn, clipOut))


def sameValue(a, b) -> bool:
    if isinstance(a, list):
        return all(abs(x - y) < 1e-6 for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return abs(a - b) < 1e-6
    return a == b


def rounded(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [round(x, 6) for x in value]
    return value


def keyframesFor(param: dict, effect: dict, clipIn: float, clipOut: float, step: float) -> list:
    """[[media seconds, value], ...]: real keys on linear stretches, one key per sequence frame on eased ones.
    step = one sequence frame in preset seconds. a preset longer than the clip is cut at the clip's edges with
    the value it has there, so a fade on a short clip still starts from where it should."""
    keys = param["keys"]
    times = set()
    for a, b in zip(keys, keys[1:]):
        times.add(a["t"])
        times.add(b["t"])
        # keys already a frame or two apart (shakes): the real keys are dense enough
        if not segmentIsLinear(a, b) and b["t"] - a["t"] > 1.5 * step:
            n = max(1, int(math.ceil((b["t"] - a["t"]) / step - 1e-9)))
            if a.get("interp") == "hold":
                times.add(b["t"] - min(step, (b["t"] - a["t"]) / 2))
            else:
                times.update(a["t"] + i * (b["t"] - a["t"]) / n for i in range(1, n))
    if len(keys) == 1:
        times.add(keys[0]["t"])
    out, cutBefore, cutAfter = [], False, False
    for t in sorted(times):
        mapped = mapTime(t, effect, clipIn, clipOut)
        if mapped < clipIn - 1e-6:
            cutBefore = True
            continue
        if mapped > clipOut + 1e-6:
            cutAfter = True
            continue
        out.append([round(mapped, 6), rounded(valueAt(keys, t))])
    if cutBefore or not out:
        out.insert(0, [round(clipIn, 6), rounded(valueAt(keys, unmapTime(clipIn, effect, clipIn, clipOut)))])
    if cutAfter or len(out) == 1:
        out.append([round(clipOut, 6), rounded(valueAt(keys, unmapTime(clipOut, effect, clipIn, clipOut)))])
    out = [k for i, k in enumerate(out) if i == 0 or k[0] - out[i - 1][0] > 1e-6]
    return [k for i, k in enumerate(out)
            if i in (0, len(out) - 1) or not (sameValue(out[i - 1][1], k[1]) and sameValue(out[i + 1][1], k[1]))]


def replayPlan(preset: dict, clipIn: float, clipOut: float, fps: float = 30.0, speed: float = 1.0,
               keepPlacement: bool = True) -> list:
    """host instructions to rebuild a preset on one clip, times in the clip's media seconds. keepPlacement: the
    clip's own motion and opacity stay unless the preset animates them (a preset saved elsewhere would reset them)."""
    plan = []
    for effect in preset["effects"]:
        spec = {"effect": effect["displayName"], "matchName": effect["matchName"], "intrinsic": effect["intrinsic"],
                "audio": effect["audio"], "params": [], "skipped": [], "notes": []}
        # one sequence frame, in preset seconds
        step = max(0.01, speed) / max(1.0, fps) / max(1e-6, anchorRatio(effect, clipIn, clipOut))
        for p in effect["params"]:
            label = p["name"] or f"#{p['index']}"
            if p["kind"] == "arb":
                if p.get("opaque"):
                    spec["skipped"].append(label)
                continue
            entry = {"name": p["name"], "index": p["index"], "kind": p["kind"]}
            if p.get("keys"):
                if p["kind"] == "color":
                    spec["skipped"].append(label + " (animated color)")
                    continue
                entry["keys"] = keyframesFor(p, effect, clipIn, clipOut, step)
                if effect["intrinsic"] and p["name"].lower() == "position":
                    spec["notes"].append(f"{effect['displayName']} position is animated: it replaces this clip's own placement")
            elif effect["intrinsic"] and keepPlacement:
                continue
            elif p.get("value") is not None:
                entry["value"] = p["value"]
            else:
                continue
            spec["params"].append(entry)
        plan.append(spec)
    return plan
