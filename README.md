# chopshopMCP

An AI video editor for Adobe Premiere Pro. Claude watches and listens to your footage, logs what happens in it and builds the edit in your Premiere project: a fast flash edit for Shorts and a tightened long version from the same analysis, with your own effect presets, captions in your style, voice over, motion graphics, masks and 2D tracking. It works on footage without a voice (unboxing, desk setups, ASMR, gameplay) as well as on talk (podcasts, interviews).

Claude Desktop is the brain, connected through the [Model Context Protocol](https://modelcontextprotocol.io) on your own Claude plan. Everything else runs on your machine. Your footage never leaves it: Claude sees small thumbnails, the stills it asks for, transcripts and measurements.

> Version 1.2.1. Windows, Premiere Pro 25.x with the English UI. The test suite runs against a simulated Premiere; run **Settings > Self-test** once in yours after installing.

## Features

| | |
| --- | --- |
| Watches and listens | One local pass per rush: motion, hard cuts, sound transients per track, speech, beats and thumbnails. Claude reads contact sheets with vision and writes a shot log it reuses for every edit. |
| Edits | Cuts in any order, timelapses, punch-ins and slow pushes, vertical, square or portrait framing, and a storyboard before anything renders. Claude can pitch angles for you to pick, or follow the structure you give it. |
| Edits in place | Notes on a built sequence are applied where it stands, so changes you made by hand stay: trims, removals, inserts, moves and transitions, each shot with its linked sound. |
| Several files | Second cameras, b-roll, memes, reactions in a corner, sound effects and music over a shot (mixed, ducked or replacing the sound), J and L cuts. A clip library indexed once and searched by what each clip shows or says. |
| Shot matching | Every shot of a sequence brought to one reference with Lumetri Exposure, Temperature and Tint, measured on frames Premiere renders. |
| Motion graphics | Six templates rendered by HyperFrames to ProRes 4444 with alpha (lower third, spec sheet, chapter title, callout, key number, verdict), or a composition Claude writes. Previewed over the real shot before rendering. |
| Your presets | Reads your Premiere effect presets and replays them on clips, eased curves included. Reads and changes any effect parameter or keyframe. Places motion graphics templates (.mogrt) and edits their text. |
| Captions | Pop (one to three big words), karaoke or plain lines, in your font, one clip per caption so your own pop-in preset animates each one. |
| Voice over | Voice first (retakes and dead air cleaned out, shots cut on it) or edit first (slots with a word budget). The rush is ducked under the voice. |
| Masks | Blur or pixelate private things as Premiere's own pen masks, tracked and keyframed on every frame and editable with the pen tool. A rough mask you draw can be refined into the object's silhouette. Titles behind the subject with GPU matting. |
| 2D tracking | Labels riding on objects, screen replacement with Corner Pin keyed on every frame, stabilization. |
| Learns your style | Breaks down edits you admire frame by frame and remembers your taste and corrections between sessions. |
| Checks its work | Renders the sequence through Premiere and watches the result: shot rhythm, black, frozen or silent stretches, loudness against YouTube's -14 LUFS. |

## How it works

```
Premiere ── chopshopMCP panel (CEP) ── host.jsx (ExtendScript + QE)
                 │  localhost bridge, token
Claude Desktop ── mcpServer.py ── Python sidecar
                                   perceive.py    motion, cuts, transients, speech, beats, thumbnails
                                   sheets.py      contact sheets; storyboard.py previews a plan
                                   edl.py         plans: cuts, order, speed, zoom, titles, labels
                                   xmeml.py       FCP XML modelled on Premiere 25's own export
                                   presets.py     .prfpset presets read and replayed
                                   captions.py    words on the edit, rendered in your font
                                   masks.py       outlines (SAM 2), mattes (MobileSAM, BiRefNet)
                                   maskGraft.py   pen masks written into a copy of the project
                                   tracking.py    objects, flat surfaces, camera shake
                                   grade.py       shot matching on rendered frames
                                   graphics.py    HyperFrames motion graphics
```

The panel runs inside Premiere and exposes a fixed list of host functions on `127.0.0.1`. Claude Desktop starts the MCP server, which does the heavy work in Python and calls Premiere through the panel. Editing know-how lives in plain Markdown playbooks under `sidecar/prompts/playbooks/` (tool rules, editing craft, references, and genres: fast meme edits, premium tech, unboxing and ASMR, gaming, podcast). The optional `chopshop-editor` skill in `skill/` tells Claude how to use all of it.

More detail on each part: [docs/howItWorks.md](docs/howItWorks.md).

## Requirements

- Windows 10 or 11.
- Adobe Premiere Pro 25.x with the English UI. The panel loads in Premiere 22 and later, but only 25 is tested.
- [Claude Desktop](https://claude.ai/download) signed in to a Claude plan.
- Python 3.10 or newer. 3.12 to 3.14 are tested.
- Node.js 22 or newer for motion graphics (optional).

Optional extras:

- An NVIDIA GPU makes transcription, matting and outline masks much faster. Matting also runs on any DirectX 12 GPU through DirectML.
- `ffmpeg` and Node.js (or Deno) on the PATH let Claude download reference videos with yt-dlp.
- `torch` and `transformers` enable SAM 2 for outline masks; without them MobileSAM is used. See `sidecar/requirements.txt`.

## Install

1. Clone or download this repository to a permanent folder, for example `C:\Tools\chopshopMCP`. Premiere and Claude Desktop both point at this folder, so keep it where it is.
2. Double-click `install.bat`. It allows unsigned CEP panels, links `panel/` into Premiere, finds a working Python, installs the Python packages, picks the GPU runtime for the matting models, installs HyperFrames when Node.js 22+ is present, checks the setup and prints the entry for Claude Desktop.
3. Restart Premiere and open **Window > Extensions > chopshopMCP**. The three lights in the panel header (PR, PY, MCP) should turn green.
4. Open **Settings > Self-test** in the panel. It imports a tiny test edit into the open project and checks every Premiere feature the editor relies on. Delete the "chopshopMCP self-test" sequences afterwards.

The [user guide](docs/userGuide.md) walks through each step, including what to do when a light stays red.

## Quick start with Claude Desktop

1. In Claude Desktop, open **Settings > Developer > Edit Config** and add the entry `install.bat` printed under `mcpServers`:

   ```json
   {
     "mcpServers": {
       "chopshopMCP": {
         "command": "C:\\Path\\To\\Python\\python.exe",
         "args": ["C:\\Tools\\chopshopMCP\\sidecar\\mcpServer.py"]
       }
     }
   }
   ```

2. Quit Claude Desktop from the system tray and open it again. The chopshopMCP tools now show in the chat's tools menu.
3. Optional: zip the `skill/chopshop-editor` folder and add it under **Settings > Capabilities > Skills**.
4. In Premiere, open your project and select the rush in the timeline or the Project panel.
5. In the chopshopMCP panel, pick the footage type, write what you want ("45 s vertical flash edit + a long version") and click **Analyze for Claude**. The analysis runs locally once and a ready prompt is copied to your clipboard.
6. Paste the prompt in Claude Desktop. Keep the panel open: Claude reaches Premiere through it.

You can also start from Claude Desktop with the `chopshop` prompt, or simply ask: "edit the selected rush as an unboxing flash edit, propose three angles first". Notes on the result ("cut the second box shot shorter, dissolve into the outro") are applied to the sequence in place.

## Tools

| Tool | Role |
| --- | --- |
| `premiereContext`, `getPlaybook` | Where the rush is; the editing rules |
| `analyzeRush`, `rushOverview`, `viewSheet`, `lookAt` | Perception, beat table, contact sheets, full-quality stills with a coordinate grid |
| `transcribeMedia`, `logShots`, `getShotLog` | Words of a rush or a voice over; Claude's shot notes |
| `previewFrame`, `previewPlan` | One frame as the viewer will see it; a storyboard of a whole plan |
| `buildEdit` | A new sequence from a plan, rendered in the background |
| `readEdit`, `editSequence` | What a sequence holds now, and notes applied in place: trim, remove, insert, overwrite, move, transitions |
| `matchShots` | Every V1 shot matched to a reference shot with Lumetri Color |
| `listGraphics`, `previewGraphic`, `addGraphic` | The bundled motion graphics, previewed over the shot, rendered with alpha and placed on a free track |
| `listPresets`, `describePreset`, `applyPreset` | Your effect presets |
| `clipEffects`, `setClipEffect` | Read and change any effect or keyframe on clips |
| `listMogrts`, `addMogrt` | Your motion graphics templates |
| `listCaptionStyles`, `addCaptions` | Caption styles; Premiere's plain caption track |
| `layoutVoiceOver`, `voiceOverSlots`, `timelineClips` | Voice over, in both directions |
| `checkTracking`, `refineMask` | Track an object or a screen before building; turn a sketched mask into a silhouette keyed on every frame |
| `reframeVertical`, `buildRoughCut`, `markTakes` | Auto reframe; talk cuts by utterance |
| `indexLibrary`, `librarySheet`, `tagLibrary`, `searchLibrary` | Your folders of memes, b-roll, sound effects and music, found by meaning |
| `fetchReference`, `breakdownReference`, `referenceShots`, `saveBreakdown` | Download an edit you admire, measure it frame by frame, keep the breakdown |
| `getStyle`, `rememberStyle` | Your style memory: taste, rules, reference cards |
| `sequenceFrame`, `watchEdit`, `exportEdit` | One rendered frame; the whole sequence rendered and checked; the final master |
| `diagnoseEffects`, `seek`, `clearReviewMarkers`, `detectSilences` | Utilities |

## Documentation

- [User guide](docs/userGuide.md): install, Claude Desktop setup, the panel, a first edit, grading, motion graphics, editing in place, troubleshooting.
- [How it works](docs/howItWorks.md): edit plans, presets, masks, tracking, references, configuration.
- [Contributing](CONTRIBUTING.md): development setup and tests.
- [Changelog](CHANGELOG.md).

## Privacy and security

- Transcription, perception, matting, tracking and motion graphics run locally. Claude sees thumbnails, the stills it asks for and transcripts, inside your own Claude conversation.
- The bridge between the panel and the MCP server listens on `127.0.0.1` only, requires a random token stored in `%APPDATA%\chopshopMCP\bridge.json`, refuses requests from browsers and exposes a fixed list of functions. It cannot run arbitrary scripts.
- HyperFrames runs with telemetry, update checks and AI frame descriptions turned off.

## Known limits

- Premiere's English UI is expected: effects are added by their English names.
- Hands passing in front of a replaced screen are not cut out yet. Pick stretches where nothing covers the screen.
- Sped-up segments are muted, and labels, titles behind the subject and captions skip them.
- Without a GPU runtime the subject matte runs in fast quality (0.3 to 1 s per frame), so keep titles behind the subject short.
- Blur zones, replaced screens and titles behind the subject follow the main rush only, not the other files of an edit.
- Reference breakdowns count anything that changes the whole frame as a cut, so word-by-word captions and interface animations show up as jump cuts.

## Roadmap

- Music and sound effects placed on transients and beats automatically
- Masks and tracking on every file of an edit
- Callouts that follow a tracked object
- Multicam podcast switching on who speaks
- Occlusion for replaced screens
- Other Premiere UI languages
- macOS, and UXP once Premiere's UXP API covers editing

## License

[MIT](LICENSE).

chopshopMCP is an independent project. It is not affiliated with or endorsed by Adobe or Anthropic, and it contains no code or presets from any commercial Premiere plugin or preset pack. Premiere Pro is a trademark of Adobe; Claude is a trademark of Anthropic.
