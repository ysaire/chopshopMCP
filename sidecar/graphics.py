"""hyperframes graphics rendered to prores 4444 with alpha, previewed over the shot the way premiere composites it."""

import html as htmlLib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np

pluginRoot = Path(__file__).resolve().parents[1]
motionRoot = pluginRoot / "motion"
templatesDir = motionRoot / "templates"
# premiere 25 blends alpha clips in linear light, measured at gamma 2.4 (bt.1886)
premiereGamma = 2.4
renderTimeout = 900.0


def hyperframesScript() -> Path:
    return motionRoot / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"


def gsapScript() -> Path:
    return motionRoot / "node_modules" / "gsap" / "dist" / "gsap.min.js"


def ready() -> str | None:
    """None when graphics can render, else what is missing."""
    if not shutil.which("node"):
        return "motion graphics need node 22 or newer on the path"
    if not hyperframesScript().is_file() or not gsapScript().is_file():
        return f"motion graphics need hyperframes: run install.bat again, or npm install in {motionRoot}"
    return None


def quietEnv() -> dict:
    """no telemetry, no update check, no skills download, and frames never go to gemini."""
    env = dict(os.environ)
    env.update({"HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1", "HYPERFRAMES_NO_UPDATE_CHECK": "1",
                "HYPERFRAMES_SKIP_SKILLS": "1", "HYPERFRAMES_NO_AUTO_INSTALL": "1"})
    env.pop("GEMINI_API_KEY", None)
    return env


variablesAttr = re.compile(r"data-composition-variables='([^']*)'", re.S)


def declared(text: str) -> list:
    found = variablesAttr.search(text)
    return json.loads(htmlLib.unescape(found.group(1))) if found else []


def withValues(text: str, values: dict | None) -> str:
    """the values baked in as the declared defaults: render and snapshot then read them the same way."""
    if not values:
        return text
    decls = declared(text)
    ids = [d["id"] for d in decls]
    for key in values:
        if key not in ids:
            raise ValueError(f"no variable {key}: {', '.join(ids)}")
    for d in decls:
        if d["id"] in values:
            d["default"] = values[d["id"]]
    attr = json.dumps(decls, ensure_ascii=False).replace("&", "&amp;").replace("'", "&#39;")
    return variablesAttr.sub(lambda m: f"data-composition-variables='{attr}'", text, count=1)


def rootTag(text: str) -> str:
    found = re.search(r"<[a-zA-Z]+\b[^>]*\bdata-composition-id=[^>]*>", text)
    if not found:
        raise ValueError("no element with data-composition-id: a hyperframes composition needs one root")
    return found.group(0)


def setAttr(tag: str, name: str, value: str) -> str:
    if re.search(rf'\b{name}="[^"]*"', tag):
        return re.sub(rf'\b{name}="[^"]*"', f'{name}="{value}"', tag)
    return tag[:-1].rstrip("/").rstrip() + f' {name}="{value}">'


def fpsArg(fps: float) -> str:
    fps = float(fps)
    if abs(fps - round(fps)) < 0.01:
        return str(int(round(fps)))
    return f"{int(round(fps * 1.001)) * 1000}/1001"


def compose(text: str, duration: float | None = None, width: int = 1920, height: int = 1080, fps: float = 60) -> str:
    """elements that lasted as long as the root still do; gsap comes from the plugin, never a cdn."""
    root = rootTag(text)
    newRoot = setAttr(setAttr(setAttr(root, "data-width", str(int(width))), "data-height", str(int(height))), "data-fps", fpsArg(fps))
    if duration is not None:
        old = re.search(r'\bdata-duration="([^"]*)"', root)
        newRoot = setAttr(newRoot, "data-duration", f"{float(duration):g}")
        if old:
            text = text.replace(f'data-duration="{old.group(1)}"', f'data-duration="{float(duration):g}"')
            root = rootTag(text)
    text = text.replace(root, newRoot, 1)
    text = re.sub(r'(<meta\s+name="viewport"\s+content=")[^"]*(")', rf"\g<1>width={int(width)}, height={int(height)}\g<2>", text)
    return re.sub(r'<script\s+src="[^"]*gsap[^"]*\.js"\s*>', '<script src="gsap.min.js">', text)


def templateText(name: str) -> str:
    path = templatesDir / f"{name}.html"
    if not path.is_file():
        raise ValueError(f"no graphic template {name}: {', '.join(t['name'] for t in listTemplates())}")
    return path.read_text(encoding="utf-8")


def listTemplates() -> list:
    out = []
    for path in sorted(templatesDir.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        description = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', text)
        duration = re.search(r'\bdata-duration="([^"]*)"', rootTag(text))
        out.append({"name": path.stem, "description": htmlLib.unescape(description.group(1)) if description else "",
                    "duration": float(duration.group(1)) if duration else 0.0, "variables": declared(text)})
    return out


def stage(text: str, folder: Path, assets: list | None = None) -> Path:
    """a self-contained render folder: the composition, gsap, the templates' shared kit and assets by basename."""
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    (folder / "index.html").write_text(text, encoding="utf-8")
    shutil.copy(gsapScript(), folder / "gsap.min.js")
    for kit in templatesDir.iterdir():
        if kit.is_file() and kit.suffix != ".html":
            shutil.copy(kit, folder / kit.name)
    for asset in assets or []:
        src = Path(asset)
        if not src.is_file():
            raise ValueError(f"no file {asset}")
        shutil.copy(src, folder / src.name)
    return folder


def runHyperframes(args: list, cwd: Path) -> str:
    problem = ready()
    if problem:
        raise RuntimeError(problem)
    done = subprocess.run([shutil.which("node"), str(hyperframesScript()), *args], cwd=str(cwd), env=quietEnv(),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=renderTimeout)
    if done.returncode != 0:
        tail = "\n".join((done.stderr or done.stdout or "").strip().splitlines()[-12:])
        tail = re.sub(r"\x1b\[[0-9;]*m", "", tail)
        raise RuntimeError(f"hyperframes {args[0]} failed:\n{tail}")
    return done.stdout


def render(folder: Path, out: Path, fps: float) -> Path:
    """prores 4444 with alpha, the only mov hyperframes writes. Lint errors stop the render."""
    folder, out = Path(folder).resolve(), Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    runHyperframes(["render", str(folder), "--format", "mov", "-o", str(out), "--fps", fpsArg(fps), "--quiet", "--strict"], folder)
    if not out.is_file():
        raise RuntimeError(f"hyperframes reported success but wrote no {out}")
    return out


def snapshot(folder: Path, times: list) -> list:
    """the composition at those seconds as RGBA floats 0-1, transparent where nothing is drawn."""
    from PIL import Image
    folder = Path(folder).resolve()
    shots = folder / "snapshots"
    runHyperframes(["snapshot", str(folder), "--at", ",".join(f"{t:.3f}" for t in times), "--no-end",
                    "--describe", "false", "-o", str(shots)], folder)
    files = sorted(shots.glob("frame-*.png"))
    if len(files) != len(times):
        raise RuntimeError(f"hyperframes wrote {len(files)} snapshots for {len(times)} times")
    return [np.asarray(Image.open(f).convert("RGBA"), dtype=np.float64) / 255 for f in files]


def composite(frame: np.ndarray, overlay: np.ndarray, gamma: float = premiereGamma) -> np.ndarray:
    """the overlay over the frame as premiere blends it: straight alpha, in linear light."""
    alpha = overlay[..., 3:4]
    lin = (np.clip(frame, 0, 1) ** gamma) * (1 - alpha) + (np.clip(overlay[..., :3], 0, 1) ** gamma) * alpha
    return lin ** (1 / gamma)


def freeTrack(state: dict, start: float, end: float) -> str | None:
    """the lowest video track above V1 with nothing between start and end."""
    for track in (state.get("video") or [])[1:]:
        if all(c["end"] <= start + 1e-3 or c["start"] >= end - 1e-3 for c in track.get("clips") or []):
            return track["name"]
    return None


def prepare(template: str | None, html: str | None, values: dict | None, accent: str | None = None) -> tuple:
    """(composition with values baked in, duration in seconds, file name); the house accent fills an unset accent."""
    if template and html:
        raise ValueError("a template or html, not both")
    if not template and not html:
        raise ValueError("a template or html: listGraphics names the templates")
    text = templateText(template) if template else html
    values = dict(values or {})
    if accent and "accent" not in values and any(d["id"] == "accent" for d in declared(text)):
        values["accent"] = accent
    text = withValues(text, values)
    duration = re.search(r'\bdata-duration="([^"]*)"', rootTag(text))
    return text, float(duration.group(1)) if duration else 4.0, template or "graphic"


def strip(cells: list, path: str, width: int = 640) -> str:
    """frames side by side, each with its label above."""
    from PIL import Image, ImageDraw

    from sheets import font
    thumbs = []
    for label, rgb in cells:
        img = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8))
        thumbs.append((label, img.resize((width, img.height * width // img.width))))
    height = max(t.height for _, t in thumbs)
    out = Image.new("RGB", (len(thumbs) * (width + 8) + 8, height + 30), (24, 24, 24))
    draw = ImageDraw.Draw(out)
    for k, (label, thumb) in enumerate(thumbs):
        x = 8 + k * (width + 8)
        draw.text((x, 6), label, fill=(230, 230, 230), font=font(14))
        out.paste(thumb, (x, 26))
    out.save(path, quality=88)
    return path
