# How chopshopMCP works

This document explains what happens under each tool: how a plan becomes a Premiere sequence, how presets, masks and tracking are written, how the result is checked, and which settings change what. For a task-by-task walkthrough, read the [user guide](userGuide.md) first.

## Architecture

Three processes take part in every edit:

- The CEP panel (`panel/`) runs inside Premiere. `panel/jsx/host.jsx` is the ExtendScript side: it reads and changes the project through Premiere's DOM and its undocumented QE layer. `panel/js/bridgeServer.js` exposes a fixed list of host functions on `127.0.0.1`.
- The MCP server (`sidecar/mcpServer.py`) is started by Claude Desktop over stdio. It holds the tools, runs long jobs in background threads and calls Premiere through the bridge.
- The panel also runs the sidecar's command line (`sidecar/cli.py`) for the work started from its own buttons: analysis, silence cuts, the setup check and the self-test.

Both the panel and the MCP server read the same settings and cache in `%APPDATA%\chopshopMCP`:

| Path | Contents |
| --- | --- |
| `config.json` | Settings (see [Configuration](#configuration)) |
| `bridge.json` | Port and token of the running panel's bridge |
| `cache\` | Analyses, transcripts, renders, mattes, graphics and frames, per rush |
| `style\` | Your style memory and saved reference breakdowns, in Markdown |
| `library\` | The index of your clip library |

### The bridge

The panel starts an HTTP server on a free port of `127.0.0.1` and writes the port and a random 48-character token to `bridge.json`. Every request must carry that token, use `Content-Type: application/json`, come without a browser `Origin` header, and name a function on the whitelist in `bridgeServer.js`. There is no way to evaluate arbitrary ExtendScript through it.

## Analysis

`analyzeRush` (or **Analyze for Claude** in the panel) decodes the rush once with PyAV and writes `perception.json` in the rush's cache folder:

- motion every half second and hard cuts in the source;
- sound energy and transients per audio track;
- speech, from a Whisper transcript of the voice track;
- beats: the rush split into `idle`, `active` and `speech` stretches, with tiny runs merged into their neighbours;
- thumbnails for the contact sheets.

The cache key includes the file's size and modification time, so a replaced or re-exported rush is analysed again. `rushOverview` prints the beat table, `viewSheet` turns any range into a contact sheet, and `lookAt` returns full-resolution stills with an optional coordinate grid. Claude writes what it saw with `logShots`; every later edit of that rush reuses the log.

Transcription uses faster-whisper: `large-v3-turbo` in float16 on an NVIDIA GPU, `small` in int8 on the CPU, unless you pick a model. A prompt full of fillers makes Whisper keep the "um"s, which talk cuts need.

## Edit plans

`buildEdit` takes a plan: a list of source segments plus what goes on top of them. `previewPlan` draws the same plan as a storyboard before anything renders.

```json
{
  "name": "keyboard | flash 45s",
  "format": "vertical",
  "blur": [{"box": [0.04, 0.05, 0.42, 0.45], "from": 0, "to": 1569, "style": "pixelate", "label": "monitor"}],
  "screens": [{"quad": [[0.61, 0.12], [0.88, 0.15], [0.87, 0.47], [0.6, 0.44]], "from": 600, "to": 640,
               "media": "C:/clips/clean capture.mp4"}],
  "sources": {"cam2": "C:/rushes/overhead.mp4"},
  "segments": [
    {"start": 1402.0, "end": 1404.2, "zoom": {"scale": 1.4, "x": 0.55, "y": 0.6}, "note": "hook: first thock"},
    {"media": "cam2", "start": 88.0, "end": 91.5, "jcut": 0.3, "note": "overhead, its sound starts early",
     "cover": [{"media": "C:/library/sfx/whoosh.wav", "volume": -8},
               {"media": "C:/library/memes/shocked.mp4", "from": 1.2, "duration": 0.8, "audio": "duck",
                "box": [0.62, 0.06, 0.96, 0.4]}]},
    {"start": 95.5, "end": 99.0, "note": "lid lift",
     "title": {"text": "KEYBOARD 75", "behind": true, "from": 0.8, "to": 3.0, "y": 0.55, "size": 0.24,
               "subject": {"box": [0.28, 0.45, 0.85, 0.95]}}},
    {"start": 140.0, "end": 143.0, "attach": [{"text": "gasket mount", "box": [0.4, 0.5, 0.55, 0.62], "anchor": "above"}]},
    {"start": 310.0, "end": 372.0, "speed": 8, "note": "switch install timelapse"},
    {"start": 1380.0, "end": 1392.0, "stabilize": true, "note": "typing test"}
  ],
  "captions": {"style": "pop", "preset": "Transform 0 (nice pop in)"},
  "voiceOver": {"path": "C:/vo/take 3.wav", "dropIds": [0, 4], "duck": -14}
}
```

Boxes and points are fractions of the source frame. Times in `blur`, `screens` and segments are source seconds; times inside a title or a cover are relative to their segment. The full field list is in the `buildEdit` tool description, which Claude reads at runtime.

### From plan to sequence

The sidecar writes the plan as Final Cut Pro XML (`xmeml.py`), shaped like the XML Premiere 25 exports itself, and the panel imports it with `app.project.importSequences`. What XML cannot carry is added right after the import and read back:

- Track Matte Key, Mosaic and Corner Pin go on through QE.
- Presets are replayed key by key (see below).
- Transform keys for labels, zooms and stabilization are written through the DOM.

Two Premiere quirks shape the writer. Premiere multiplies a clip's in and out points by its speed when it reads XML, so the writer divides them first. Premiere's Transform starts with uniform scale off, so both axes are always set together.

## Editing in place

`readEdit` reads a sequence as it is now, including your hand edits, and labels clips the way `editSequence` addresses them (`V1 #2`). `editSequence` then applies trims, removals, inserts, overwrites, moves and transitions. Each step takes the shot's linked sound along, and a ripple moves every track.

Premiere 25 needed some care here, and each point was checked frame by frame on real renders:

- Setting a clip's end through scripting trims its tail, but setting its start slides the head and keeps the in point, and setting the in point slips the content.
- A ripple delete through scripting does not ripple.
- None of these touch the linked sound.
- The out point Premiere reports after a change is stale, so it is computed from the in point and the duration on screen.
- Every read of a track item returns a new object, so clips are compared by `nodeId`, never by identity.

`tests/liveTimeline.js` reproduces this behaviour so the tests catch regressions without Premiere.

## Your presets

Premiere has no scripting API to apply a custom effect preset, so chopshopMCP reads the preset file and rebuilds it: each effect is added by name, static values are set, and animated values are written as keyframes. Bezier handles cannot be set by script, so eased curves are evaluated with Premiere's speed and influence model and written one keyframe per frame; linear stretches keep only their real keys. Anchoring follows the preset: from the in point, from the out point, or stretched over the clip.

Presets come from your Premiere profile (`Documents\Adobe\Premiere Pro\<version>\Profile-<name>\Effect Presets and Custom Items.prfpset`) and from any file or folder listed in `presetPaths`. Packs you bought stay on your machine.

`describePreset` reports what cannot be replayed by script: Lumetri curves, looks and LUTs (the sliders work), masks drawn inside an effect, audio presets (named audio effects work through `setClipEffect`), and third-party effects that are not installed.

## Captions

Each caption is a tight transparent still rendered in your font and placed by motion, so a Transform pop-in scales around the text rather than around the frame. Three base styles ship: `pop` (one to three big words), `karaoke` (the spoken word highlighted) and `line` (plain subtitles). Define your own in `config.json`:

```json
"captionStyles": {
  "mine": {"base": "pop", "font": "Montserrat-Black", "size": 0.075, "color": "#FFFFFF", "stroke": 0.12,
           "y": 0.72, "words": 2, "uppercase": true, "preset": "Transform 0 (nice pop in)"}
}
```

Fonts are found by name among your installed fonts. To change the text, ask Claude, which renders it again. For captions you can edit with Premiere's text tool, use Premiere's caption track (`addCaptions`) or a motion graphics template (`addMogrt`).

## Masks

chopshopMCP never paints over your pixels unless you ask for baked mode. Blur and pixelate zones become Premiere's own masks, titles behind the subject use a rendered matte, and the compositing happens in Premiere. Pick the mode per plan (`"masks": "native" | "matte" | "baked"`) or in **Settings > Masks**.

### Native masks (default)

Neither the ExtendScript DOM nor QE can write a mask path. Premiere does read one from a project file, so the edit takes a detour:

1. The edit is built under a temporary stage name and Premiere saves the project.
2. The sidecar decompresses a copy of the `.prproj` and writes an `AE.ADBE AEMask` component under the Gaussian Blur or Mosaic of each clip a zone shows on: the 24 parameters Premiere itself writes, the path as a `2cin` block, one keyframe per source frame in media ticks.
3. The sequence gets a fresh id and the plan's name, `app.project.importSequences` brings it back, and the stage sequence is removed.

The effect and its mask sit on the rush clip in the main sequence, where you would put them by hand, so you can change a block size, a feather or a point with the pen tool. The masks ride the clip's Motion, so zooms and the vertical format need nothing more:

```
edit   V1  rush, cut as planned; a clip a zone shows on carries Gaussian Blur and/or Mosaic, one mask per zone
       V2  titles
       V3  "(chop subject)" for a title behind the subject: its fill is the rush with the same effects and masks
```

A mask holds its last path forever, so a zone shorter than its clip has its mask parked off screen on the clip's other frames, and a zone spanning several clips gets one mask on each. Mask keys live in source seconds whatever the clip's speed. A screen replacement, or a cover taken from the rush over a zone, still needs a `(chop comp)` nest; the build says so in a warning.

`buildEdit` returns a `maskReport`: the number of masks, the project copy that was imported (kept in the cache) and whether the stage was removed. If the graft fails, the edit is rebuilt with rendered mattes as `... (matte)`.

Two things are easy to get wrong when writing a project file by hand. `PremiereFilterPrivateData` needs its `BinaryHash` attribute: the hash keys Premiere's blob store, and private data without it is dropped silently, which crashes the renderer. And after a crash, Premiere reopens its recovery copy of the project rather than the saved file.

### Outline masks

A tracked zone is an outline by default (`shape: "outline"`): the mask follows the object's silhouette, not a box around it.

SAM 2.1 hiera tiny, through `transformers`, is prompted once with the zone's box at `from`; its video memory then carries the object frame by frame. It runs on a window zoomed on the object (twice its size, at least 512 px, resized to the model's 1024 px input), so a small object fills a good part of what the model sees. The window moves only when the object nears its edge, and an empty mask two frames in a row restarts the model from the CSRT tracker's box. The mask is the contour of the silhouette: 24 points, grown by 2 % of the box, with parts that stay close wrapped in one hull so an object under a hand is never half hidden.

The box at `from` is the only prompt, so it has to be tight on the object on that frame. With an RTX 3060 Ti and Premiere open, SAM 2 takes 120 to 130 ms per frame, bf16 on CUDA.

SAM 2 needs `torch` and `transformers` (see `sidecar/requirements.txt`). Without them the outline falls back to MobileSAM frame by frame, with four prompts per frame where the candidate that continues the previous mask wins, and the build report says so. `outlines` in the report names the model that ran and its time per frame.

`shape: "rect"` or `"ellipse"` keeps a tracked zone as a plain shape centred on the tracker, never smaller than the planned box. Static zones get a key on their first and last frame, since Premiere ignores a mask path with a single keyframe.

### Refining a sketched mask

Some objects are easier to draw than to describe, such as a knife half under a hand or a label on a box that turns. Draw it yourself and let the sidecar finish it:

1. Draw a rough pen mask on the clip's Mosaic or Gaussian Blur, on a frame where the object is whole. Leave a small margin around the object.
2. Set the sequence's In and Out points on the stretch to hide.
3. Select the clip and click **Refine mask** in the panel, or ask Claude, which calls `refineMask`.

The sidecar saves the project, reads the sketch out of the project file and rasterizes it. SAM 2 is prompted with the sketch's box and its deepest point (the point farthest from the sketch's edges, which lands on the object), and only what it finds inside the sketch grown by a tenth is kept. Every later keyframe of the sketch re-registers the model on its own frame, so a sketch keyed twice across a flip follows the flip. The silhouette replaces the sketch's keys frame by frame in a copy of the project; the mask keeps its feather, expansion and place under the effect, and the sequence comes back under the same name. Outside the stretch the sketch's own keys stay.

Without In and Out points the stretch is the span of the sketch's keys, so a sketch with one key needs the marks. A sketch much larger than the object makes the model pick whatever fills it, like the desk under a knife.

### Rendered mattes

In `matte` mode only the mattes are rendered (white with alpha, QuickTime Animation, lossless and small), and Track Matte Key composites your original footage:

```
edit                          V1  rush, or "(chop comp)" where a zone or a screen shows
                              V2  title (PNG), then labels, then captions
                              V3  "(chop subject)", framed exactly like V1
(chop comp)     source size   V1  original rush
                              V2  replaced screens + Corner Pin
                              V3  same rush + Gaussian Blur + Track Matte Key (Matte: Video 4)
                              V4  blur matte
                              V5  same rush + Mosaic + Track Matte Key (pixelate zones only)
                              V6  pixelate matte
(chop subject)  source size   V1  the comp (or the rush) + Track Matte Key (Matte: Video 2)
                              V2  subject matte
```

Tracks only exist when used. Grade the rush inside `(chop comp)` and everything follows. Matte tracks stay switched on: Track Matte Key hides the matte it reads, and reads black from a track whose output is off.

The Matte popup of Track Matte Key is tied to the default track names ("Video 1", "Video 2"...), so renamed tracks break it. When Premiere refuses a key, `buildEdit` rebuilds the edit in baked mode as `... (baked)`: blur rendered into H.264 copies on V1, the subject as ProRes 4444 with alpha on V3.

### Subject cutout

Titles behind the subject need a matte of the subject, set in **Settings > Masks > Subject cutout**:

| Mode | Models | Edges | Speed |
| --- | --- | --- | --- |
| Fast | MobileSAM | Soft, may miss fine detail | CPU friendly |
| Best | MobileSAM + BiRefNet lite (MIT), gated by SAM | Cables and fingers | Every frame, made for a GPU |
| Auto | Best when a GPU runs ONNX, Fast otherwise | | |

On a synthetic desk scene with a known ground truth, edge error drops from 0.066 with MobileSAM to 0.028 with BiRefNet gated by SAM. The models download from Hugging Face on first use: 45 MB for MobileSAM, 110 to 220 MB for BiRefNet, 150 MB for SAM 2.

## 2D tracking

Tracking uses KLT features inside the shape and a transform from the reference frame, so errors do not accumulate from frame to frame; points are reseeded when they run out. On a synthetic shaky rush with a known truth, screen corners stay within 0.2 px, a moving object within 0.5 px, and stabilization cuts frame-to-frame shake about six times.

- Labels on objects: a still that rides on the tracked object, motion keyed on every frame through the shot's own framing (zoom, vertical crop).
- Screen replacement: the replacement is conformed to the rush size inside the comp nest, with Corner Pin keyed on every frame.
- Stabilization: the camera path is smoothed and written as Transform position and rotation keys, with a small zoom to hide the edges. `method: "warp"` uses Premiere's Warp Stabilizer instead when the clip allows it.

`checkTracking` runs a track and shows it before anything is built.

## Shot matching

`matchShots` measures every V1 shot on frames Premiere renders, its effects included: the mean brightness, and the colour cast of the pixels that are neutral under that shot's own light. It moves the Exposure, Temperature and Tint of the clip's Lumetri Color, renders again and measures again until the shot sits with the reference. Each slider's effect was measured on Premiere 25's own renders, so the first correction usually lands close. The grey pixels are picked once on the uncorrected frame and reused, so a correction cannot change which pixels count as grey.

## Motion graphics

Graphics are HTML compositions rendered by [HyperFrames](https://github.com/heygen-com/hyperframes), bundled in `motion/` with a pinned version and GSAP served from the plugin, so rendering works offline. Templates live in `motion/templates/` and share `chop.css` and `chop.js`.

A composition of Claude's own follows HyperFrames' rules: a root element with `data-composition-id`, `data-width`, `data-height` and `data-duration`; timed elements with `class="clip"`, `data-start` and `data-duration`; a paused GSAP timeline registered in `window.__timelines` under the root's id; a transparent page.

Premiere blends an alpha clip over the shot in linear light (gamma 2.4), which makes anything semi-transparent look lighter than in a browser. `previewGraphic` blends the same way, and the templates use solid panels. HyperFrames runs with telemetry, update checks and AI frame descriptions turned off (`HYPERFRAMES_NO_TELEMETRY`, `DO_NOT_TRACK`, `--describe false`, and no `GEMINI_API_KEY` in its environment).

## Checking the result

- `sequenceFrame(time)` exports one frame of a built sequence as Premiere renders it, for a blur, a title behind the subject or a transition.
- `watchEdit` has Premiere render the whole sequence with the "Match Source - Low bitrate" H.264 preset shipped with the running Premiere, then reads every frame and sample with PyAV (no FFmpeg binary needed). Claude gets the shots and their lengths, black stretches (a hole in the timeline), frozen stretches, silent stretches, the integrated loudness measured per ITU-R BS.1770, and a sheet with a frame from the middle of every shot. The render stays available to `viewSheet`, `lookAt` and `transcribeMedia`.
- `exportEdit` does the same with the "Match Source - High bitrate" preset and saves the master into an `exports` folder next to the project, never over an existing file.

Premiere is busy while it renders.

## Learning your style

Send Claude a link to an edit you admire. `fetchReference` downloads it on your machine with yt-dlp (720p, captions in the original language), and `breakdownReference` measures every frame: each cut and its kind (hard cut, punch-in, jump cut, cut under a flash), shot lengths and pacing per 5 s, zooms, pans and shake per shot, flashes, sound hits and how many cuts land on them, and words per minute. Claude watches one frame per shot (`referenceShots`) and writes a breakdown card (hook, structure, captions, motion, sound, three rules to borrow) with `saveBreakdown`.

Cards and your taste live in `%APPDATA%\chopshopMCP\style` as Markdown. Every correction you give ("captions smaller", "never whip pans") becomes a rule there through `rememberStyle`, and `getStyle` reads it back at the start of each session.

Only public videos you point to are fetched, for analysis on your machine. Nothing from a reference ends up in your edits except the principles.

## Clip library

`indexLibrary` indexes a folder of memes, reactions, b-roll, sound effects or music once: duration, thumbnails, and a transcript when there are words. Claude looks at the clips on sheets (`librarySheet`) and writes a description and tags for each (`tagLibrary`). `searchLibrary` then finds clips by what they show or say, and plans reference them by path in `cover` entries or `media` segments.

## Configuration

Settings live in `%APPDATA%\chopshopMCP\config.json`. The panel writes most of them; the rest you can add by hand.

| Key | Default | Meaning |
| --- | --- | --- |
| `pythonPath` | `"python"` | Interpreter the panel runs; `install.bat` sets it |
| `voiceTrack` | `1` | Audio track Whisper listens to |
| `language` | `"auto"` | Transcription language |
| `whisperModel` | `"auto"` | `large-v3-turbo` on a GPU, `small` on a CPU, or any faster-whisper model name |
| `whisperDevice` | `"auto"` | `auto`, `cuda` or `cpu` |
| `padMs`, `minSilenceMs`, `thresholdDb` | `120`, `350`, `null` | Silence cuts; a `null` threshold is measured on the rush |
| `removeFillers` | `true` | Cut filler words in talk cuts |
| `maskMode` | `"native"` | `native`, `matte` or `baked` |
| `maskFeather`, `maskExpansion` | `30`, `12` | Pen mask feather and expansion, pixels at 1080p |
| `cutoutQuality` | `"auto"` | Subject cutout: `auto`, `best` or `fast` |
| `matteOffset` | `null` | Track Matte Key's Matte value as track number plus an offset; `null` probes it in Premiere |
| `graphicsAccent` | `null` | Accent colour for every motion graphic template, for example `"#00c2ff"` |
| `presetPaths` | `[]` | Extra `.prfpset` files or folders |
| `mogrtPaths` | `[]` | Extra folders of `.mogrt` templates |
| `captionStyles` | `{}` | Your caption styles (see [Captions](#captions)) |
