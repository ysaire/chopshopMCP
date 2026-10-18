"""eyes: stills at given source times."""

from pathlib import Path

import av


def grabFrames(mediaPath: str, times: list, outDir: str, maxSide: int = 768) -> list:
    """one jpeg per requested source time, small enough for vision."""
    out = Path(outDir)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    with av.open(mediaPath) as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            return results
        for t in sorted(float(x) for x in times):
            target = max(0.0, t)
            container.seek(int(target / stream.time_base), stream=stream, backward=True, any_frame=False)
            picked = None
            for frame in container.decode(stream):
                if frame.time is None:
                    continue
                picked = frame
                if frame.time >= target:
                    break
            if picked is None:
                continue
            image = picked.to_image()
            image.thumbnail((maxSide, maxSide))
            path = out / f"frame_{target:09.3f}.jpg"
            image.save(path, quality=85)
            results.append({"time": round(picked.time, 3), "path": str(path)})
    return results
