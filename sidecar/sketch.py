"""refine a mask sketched by hand: the pen mask the user drew on the mosaic or gaussian blur of a clip prompts
the video segmenter, and the silhouette it follows replaces the sketch frame by frame on the stretch between
the in and out points, in a copy of the saved project brought back under the same name."""

import os
import time

import numpy as np

import maskGraft
import masks
from config import mediaCacheDir
from pipeline import roundShape, safeName


def rasterize(vertices: list, width: int, height: int) -> np.ndarray:
    """the sketch as a bool bitmap of the frame, curves sampled along their handles."""
    import cv2
    pts = np.array([[x * width, y * height] for x, y in maskGraft.bezierPolyline(vertices)], dtype=np.float64)
    canvas = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(canvas, [np.round(pts).astype(np.int32)], 1)
    return canvas.astype(bool)


def sourceSpan(clip: dict) -> tuple:
    """(in, out) of a clip of the rush in source seconds: premiere reports a sped clip's in and out points
    divided by its speed."""
    speed = float(clip.get("speed") or 1)
    return (float(clip["inPoint"]) * speed, float(clip["outPoint"]) * speed)


def sourceRange(clip: dict, inPoint, outPoint, keys: list, fps: float) -> tuple:
    """[a, b) in source seconds: the in and out points (sequence seconds) through the clip's start, in point
    and speed, or the stretch between the sketch's first and last key when no marks are set."""
    step = 1 / float(fps)
    speed = float(clip.get("speed") or 1)
    start, end = float(clip["start"]), float(clip["end"])
    srcIn, srcOut = sourceSpan(clip)
    if inPoint is not None and outPoint is not None:
        a, b = max(start, float(inPoint)), min(end, float(outPoint))
        if b - a < step / 2:
            raise ValueError(f"the In/Out points ({float(inPoint):.2f}-{float(outPoint):.2f} s) are outside the clip "
                             f"({start:.2f}-{end:.2f} s)")
        return (srcIn + (a - start) * speed, srcIn + (b - start) * speed)
    times = [t for t, _v in keys if t is not None]
    if len(times) < 2:
        raise ValueError("set the In and Out points around the stretch to refine: the sketch has a single key")
    return (max(srcIn, min(times)), min(srcOut, max(times) + step))


def sketchAt(keys: list, t: float):
    """the sketch in force at t: the latest key at or before it, the first key before any."""
    picked = None
    for kt, vertices in keys:
        if kt is None or kt <= t + 1e-6:
            picked = vertices
    return picked if picked is not None else keys[0][1]


def sketchPrompts(keys: list, a: float, b: float) -> list:
    """[(time, vertices)] that prompt the model: the sketch in force at a, then every key inside (a, b)."""
    return [(a, sketchAt(keys, a))] + [(t, v) for t, v in keys if t is not None and a + 1e-6 < t < b - 1e-6]


def refinedKeys(sketchKeys: list, polys: list, a: float, b: float, clipIn: float, clipOut: float, fps: float) -> list:
    """the sketch's own keys outside [a, b) resampled to the polygons' vertex count, the polygons inside, and the
    mask parked off screen where the sketch has no key; a sketch never animated is replaced wholesale."""
    count = len(polys[0][1])
    park = masks.offScreenPolygon(count)
    step = 1 / float(fps)

    def resampled(vertices):
        pts = masks.orient(np.array(maskGraft.bezierPolyline(vertices), dtype=np.float64))
        return [[round(float(x), 5), round(float(y), 5)] for x, y in masks.resampleClosed(pts, count)]

    before = [(t, resampled(v)) for t, v in sketchKeys if t is not None and t < a - 1e-6]
    after = [(t, resampled(v)) for t, v in sketchKeys if t is not None and t >= b - 1e-6]
    keys = [(t, roundShape(p)) for t, p in polys]
    if not before and a > clipIn + step / 2:
        keys = [(clipIn, park), (max(clipIn, a - step), park)] + keys
    if not after and b < clipOut - step / 2:
        keys = keys + [(b, park), (max(b, clipOut - step), park)]
    return before + keys + after


def effectMatches(entry: dict, effect: str) -> bool:
    name = effect.strip().lower()
    aliases = {k.lower(): v for k, v in maskGraft.matchNames.items()}
    return entry["matchName"] in aliases.get(name, ()) or entry["effect"].lower() == name or entry["matchName"].lower() == name


def pickSketch(effects: list, effect: str | None = None) -> tuple:
    """(effect entry, mask) of the sketch: the last mask of the effect named, or of the only masked effect."""
    if not effects:
        raise ValueError("no mask on this clip: draw one with the pen on its Mosaic or Gaussian Blur")
    if effect:
        wanted = [e for e in effects if effectMatches(e, effect)]
        if not wanted:
            raise ValueError(f"no mask on the {effect} of this clip (masked: {', '.join(e['effect'] for e in effects)})")
    elif len(effects) > 1:
        raise ValueError(f"several effects carry a mask ({', '.join(e['effect'] for e in effects)}): say which one (effect)")
    else:
        wanted = effects
    return wanted[0], wanted[0]["masks"][-1]


def noProgress(stage, pct=None, message=None):
    pass


def refineSketch(callPremiere, segmenter, sequenceId: str | None = None, track: int | None = None, index: int | None = None,
                 inPoint: float | None = None, outPoint: float | None = None, effect: str | None = None, onProgress=None) -> dict:
    """clip and marks from premiere, the sketch from the saved project, the segmenter prompted with it frame by
    frame, the mask's keys replaced in a copy of the project and the sequence swapped for the copy's, same name."""
    progress = onProgress or noProgress
    rng = callPremiere("getRange", {"sequenceId": sequenceId} if sequenceId else {})
    sequenceId, sequenceName, fps = rng["sequenceId"], rng["sequence"], float(rng["fps"])
    if track is not None:
        target = {"track": f"V{int(track) + 1}", "index": int(index or 0)}
        clip = callPremiere("getClips", {"sequenceId": sequenceId, "targets": [target]})["clips"][0]
    else:
        selected = [c for c in rng.get("clips") or [] if not c.get("audio")]
        if len(selected) != 1:
            raise ValueError("select the clip that carries the sketch in the timeline" if not selected
                             else f"{len(selected)} clips selected: select only the one that carries the sketch")
        clip = selected[0]
    mediaPath = clip.get("mediaPath") or ""
    if not os.path.isfile(mediaPath):
        raise ValueError(f"{clip['label']} ({clip.get('name')}) is not a file on disk: refine a clip of the rush itself, not a nest")
    inPoint = rng.get("in") if inPoint is None else inPoint
    outPoint = rng.get("out") if outPoint is None else outPoint

    progress("render", 0.0, "saving the project")
    saved = callPremiere("saveProject", {}, timeout=600.0)
    path = saved.get("path") or ""
    if not os.path.isfile(path):
        raise ValueError(f"premiere did not save the project at {path!r} (save it once by hand, then try again)")
    text = maskGraft.readProject(path)
    project = maskGraft.Project(text)
    fx, mask = pickSketch(project.clipMasks(sequenceId, int(clip["track"]), int(clip["index"])), effect)
    keys = sorted(mask["keys"], key=lambda k: -1.0 if k[0] is None else k[0])
    a, b = sourceRange(clip, inPoint, outPoint, keys, fps)
    prompts = sketchPrompts(keys, a, b)
    expected = max(1, int(round((b - a) * fps)))
    progress("render", 0.0, f"refining the sketch on {fx['effect']} of {clip['label']}, {b - a:.1f} s of source")

    polys, state, previous, used, lost = [], None, None, 0, 0
    box = None
    for i, t, rgb, W, H, rate in masks.iterFrames(mediaPath, a, b, True):
        step = 1 / float(rate)
        bitmap = None
        while prompts and prompts[0][0] <= t + step / 2:
            bitmap = rasterize(prompts.pop(0)[1], W, H)
            used += 1
        if bitmap is not None:
            box = masks.binaryBox(bitmap, 0.0, W, H) or box or (0, 0, W, H)
            alpha, state = segmenter.outline(rgb, box, state, mask=bitmap)
        else:
            alpha, state = segmenter.outline(rgb, box, state)
        box = (state or {}).get("box") or box
        poly = masks.outlinePolygon(alpha, box, W, H)
        if poly is None:
            lost += 1
            poly = previous or masks.boxPolygon([box[0] / W, box[1] / H, box[2] / W, box[3] / H])
        previous = poly
        polys.append((t, poly))
        if i % 10 == 0:
            progress("render", min(0.95, (i + 1) / expected), f"frame {i + 1} of {expected} at {t:.2f} s")
    if not polys:
        raise ValueError(f"no frame of {mediaPath} between {a:.2f} and {b:.2f} s")

    refined = refinedKeys(keys, polys, a, b, *sourceSpan(clip), fps)
    grafts = [{"mask": mask["id"], "keys": [[round(float(t), 5), shape] for t, shape in refined]}]
    seq = project.sequence(sequenceId)
    nestIds = sorted(project.nestsOf(seq))
    renames = {sequenceId: sequenceName}
    for nest in nestIds:
        renames[nest] = project.byUid[nest].findtext("Name") or ""
    folder = mediaCacheDir(mediaPath) / "projects"
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / f"{safeName(sequenceName)}_{int(time.time())}.prproj"
    progress("render", 0.96, "writing the refined mask into a copy of the project")
    report = maskGraft.graftProjectFile(path, str(copy), grafts, renames)
    newId = report["ids"][sequenceId]
    progress("render", 0.98, "importing the refined sequence")
    done = callPremiere("importProjectSequences", {"path": str(copy), "sequenceIds": [newId], "open": newId,
                                                   "drop": [sequenceId] + nestIds}, timeout=600.0)
    added = done.get("sequences") or []
    if newId not in {s["id"] for s in added}:
        named = [s for s in added if s["name"] == sequenceName]
        if not named:
            raise ValueError(f"premiere imported {len(added)} sequences from the copy but not \"{sequenceName}\": the original is left as it was, the copy is {copy}")
        newId = named[0]["id"]
    dropped = done.get("dropped") or {"deleted": [], "left": [{"id": i} for i in [sequenceId] + nestIds]}
    warnings = []
    describe = segmenter.describe() if hasattr(segmenter, "describe") else {}
    if describe.get("quality") != "video":
        warnings.append("the sketch was refined by mobilesam frame by frame: install torch and transformers for sam 2 with video memory")
    if lost:
        warnings.append(f"the silhouette was lost on {lost} of {len(polys)} frames, the previous one was kept there")
    return {"ok": True, "sequenceId": newId, "sequenceName": sequenceName, "clip": clip["label"], "effect": fx["effect"],
            "mask": {"id": mask["id"], "slot": mask["slot"], "instance": mask["instance"]},
            "range": {"in": round(a, 4), "out": round(b, 4), "sequenceIn": inPoint, "sequenceOut": outPoint},
            "frames": len(polys), "sketchKeys": used, "keys": len(refined), "projectCopy": str(copy), "outlines": describe,
            "stageDeleted": [s.get("name") or s.get("id") for s in dropped.get("deleted") or []],
            "stageLeft": [s.get("name") or s.get("id") for s in dropped.get("left") or []], "warnings": warnings}
