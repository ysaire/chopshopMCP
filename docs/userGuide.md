# chopshopMCP user guide

This guide takes you from a fresh Windows machine to a finished edit, then covers grading, motion graphics, notes on an existing sequence and what to do when something does not connect.

1. [Install](#1-install)
2. [Set up Claude Desktop](#2-set-up-claude-desktop)
3. [The panel](#3-the-panel)
4. [A first edit](#4-a-first-edit)
5. [Grading with matchShots](#5-grading-with-matchshots)
6. [Motion graphics with addGraphic](#6-motion-graphics-with-addgraphic)
7. [Editing in place](#7-editing-in-place)
8. [Troubleshooting](#8-troubleshooting)

## 1. Install

### Before you start

- Premiere Pro 25.x with the English UI. Effects are added by their English names, so another UI language breaks them.
- Python 3.10 or newer from [python.org](https://www.python.org/downloads/windows/). On the first screen of the installer, tick **Add python.exe to PATH**. The `python` shortcut from the Microsoft Store does not count; you can switch it off under **Settings > Apps > Advanced app settings > App execution aliases**.
- Node.js 22 or newer from [nodejs.org](https://nodejs.org) if you want motion graphics. Without it everything else works.
- Claude Desktop, signed in.

### Run the installer

1. Put the repository in a folder you will keep, for example `C:\Tools\chopshopMCP`. Premiere loads the panel from this folder and Claude Desktop starts the server from it, so moving or renaming it later means running `install.bat` again and updating Claude Desktop's config.
2. Double-click `install.bat`. It runs seven steps:
   1. Allows unsigned CEP panels (`PlayerDebugMode` for CSXS 11 and 12).
   2. Links `panel\` into `%APPDATA%\Adobe\CEP\extensions\chopshopMCP`.
   3. Finds a Python 3.10+ interpreter, trying the `py` launcher first.
   4. Installs the Python packages from `sidecar\requirements.txt` and saves the interpreter's path in `%APPDATA%\chopshopMCP\config.json`. The first run takes a few minutes.
   5. On an NVIDIA GPU, installs the CUDA libraries Whisper uses.
   6. Installs the GPU runtime for the matting models: CUDA on NVIDIA drivers 580 and newer, DirectML on any other DirectX 12 GPU, the CPU build otherwise.
   7. With Node.js 22+, installs HyperFrames in `motion\` and downloads the Chrome it renders with.
3. At the end it prints a block that starts with `"chopshopMCP": { "command": ...`. Keep that window open; you need the block in the next section.

The first transcription and the first masked edit download their models from Hugging Face (Whisper, MobileSAM, BiRefNet, SAM 2), from a few hundred MB to about 1.6 GB for the large Whisper model on a GPU.

### Check it in Premiere

1. Restart Premiere and open a project.
2. Open **Window > Extensions > chopshopMCP**.
3. Look at the three lights in the panel header. PR, PY and MCP should all turn green within a few seconds. If one stays orange or red, see [Troubleshooting](#8-troubleshooting).
4. Click the gear icon, then **Self-test**. The panel imports a tiny test edit into the open project and checks every Premiere feature the editor relies on: nested sequences and Gaussian Blur from XML, Track Matte Key set through QE and read back, speed from XML, motion keyframes on stills, Transform and Corner Pin by name, audio ducking and edits from several files. The report is copied to your clipboard. Delete the "chopshopMCP self-test" sequences afterwards.

To remove the panel later, run `uninstall.bat`. It leaves the Python packages and your settings, cache and style memory in `%APPDATA%\chopshopMCP`; the Chrome that renders motion graphics stays in `%USERPROFILE%\.cache\hyperframes`. Delete those folders by hand if you want them gone.

## 2. Set up Claude Desktop

1. In Claude Desktop, open **Settings > Developer > Edit Config**. This opens `claude_desktop_config.json` in the right place for your install.
2. Paste the block `install.bat` printed inside `mcpServers`. With nothing else configured, the file looks like this:

   ```json
   {
     "mcpServers": {
       "chopshopMCP": {
         "command": "C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python313\\python.exe",
         "args": ["C:\\Tools\\chopshopMCP\\sidecar\\mcpServer.py"]
       }
     }
   }
   ```

   Backslashes are doubled in JSON. Use the interpreter `install.bat` found, not just `python`: it is the one with the packages installed.
3. Quit Claude Desktop from its icon in the system tray (closing the window leaves it running) and open it again.
4. Start a new chat and open the tools menu in the message box. chopshopMCP should be listed with its 52 tools.

### The editor skill (optional)

`skill/chopshop-editor/SKILL.md` gives Claude an editor's working method: read your style first, watch before cutting, plan with numbers, review its own edit before reporting. Zip the `skill/chopshop-editor` folder and add it in Claude Desktop under **Settings > Capabilities > Skills**. The MCP server also sends its own instructions and the playbooks, so the skill is a convenience, not a requirement.

### Prompts

The server adds two prompts to Claude Desktop's prompt menu: `chopshop` starts an edit (footage type and goal as arguments) and `reviewEdit` applies your notes to the last edit.

## 3. The panel

The panel is both a small control surface and the bridge Claude's hands go through. Keep it open while Claude works.

### Header

| Light | Green means | Hover it to see |
| --- | --- | --- |
| PR | The host script is loaded in Premiere | The Premiere version, or the error |
| PY | Python runs the sidecar with every package installed | The Python version, or the missing packages |
| MCP | The bridge for Claude is listening on `127.0.0.1` | The port, or the error |

A grey pulsing light is still checking. The gear icon opens the settings.

### Main view

- Rush: the clip selected in Premiere's timeline or Project panel. The folder icon picks a file yourself, the refresh icon re-reads Premiere's selection.
- Footage type: Unboxing (also desk setups and ASMR), Gaming, Podcast, or Other. It picks the playbook Claude reads first.
- Goal: one line, for example "45 s vertical flash edit + a long version".
- **Analyze for Claude**: analyses the rush locally (motion, cuts, sound, speech, beats) and copies a prompt to paste in Claude Desktop. The analysis is cached, so Claude does not run it again.
- Copy prompt: copies the prompt again.
- Refine mask: turns a mask you sketched on a clip into the object's silhouette on every frame (see [How it works](howItWorks.md#refining-a-sketched-mask)).
- Cut silences: cuts the dead air of the rush into a new sequence, with the silence settings below.
- Clear markers: removes the review markers Claude left on the active timeline.

The status line under the tiles shows the current job and a stop button while it runs; the log below it keeps the last messages.

### Settings

Settings save as you change them, into `%APPDATA%\chopshopMCP\config.json`.

| Section | Setting | What it does |
| --- | --- | --- |
| Masks | Mode | Native: Premiere pen masks grafted into the project (default). Mattes: rendered mattes Premiere composites. Baked: pixels rendered by chopshopMCP. |
| | Feather, Expansion | Pen mask feather and expansion, in pixels at 1080p |
| | Subject cutout | Quality of the matte behind titles: Auto, Best or Fast |
| Voice | Voice track | The audio track Whisper listens to (A1 to A4) |
| | Language, Whisper | Transcription language and model; Auto uses large-v3-turbo on a GPU, small on a CPU |
| Silence cut | Padding, Min silence, Threshold | Used by Cut silences; an empty threshold is measured on the rush |
| Graphics | Channel accent | The colour every motion graphic template uses for its accent; empty keeps each template's own |
| Python | Interpreter | The python.exe the panel runs; **Check setup**, **Diagnose** and **Self-test** |

## 4. A first edit

1. Open your project in Premiere and select the rush, in the timeline or the Project panel.
2. In the panel, pick the footage type, write the goal and click **Analyze for Claude**. A 25-minute 1080p rush takes a few minutes the first time; the progress bar shows each stage.
3. Paste the prompt in Claude Desktop.

From there Claude works through the tools on its own. A typical session:

1. It reads your style memory and the playbooks for this kind of video.
2. It reads the beat table (`rushOverview`), watches the active and spoken stretches on contact sheets (`viewSheet`, `lookAt`), and writes a shot log (`logShots`). If there is speech, it reads the transcript.
3. It asks once whether you want it to propose angles or follow your structure. In propose mode it pitches two or three angles with stills of the key moments; you pick one.
4. It shows a storyboard of the plan (`previewPlan`) and builds it (`buildEdit`). The new sequence appears in your project under the plan's name, for example `keyboard | flash 45s`. A build with masks or titles behind the subject takes longer, since mattes and silhouettes are computed first.
5. It renders the sequence through Premiere and watches it (`watchEdit`): shot lengths and rhythm, black, frozen and silent stretches, loudness. It checks blur zones, titles and transitions on single frames (`sequenceFrame`) and fixes what does not match the plan.
6. It reports what it kept and why.

Things you can ask along the way:

- "Make a 9:16 version for Shorts from the same log."
- "Pixelate the monitor on the left for the whole edit."
- "Put pop captions on it, two words at a time, in Montserrat Black."
- "Here is a link to an edit I like, use its pacing." Claude downloads it locally, measures it and keeps a breakdown card in your style memory.

Every correction or compliment you give becomes a rule in your style memory (`%APPDATA%\chopshopMCP\style`), plain Markdown you can read and edit. Claude reads it at the start of every session.

When the edit is final, ask Claude to export it. `exportEdit` renders a high-bitrate H.264 master at the sequence's size into an `exports` folder next to the project and never overwrites an existing file.

## 5. Grading with matchShots

Once the cut is locked, `matchShots` makes the shots look like one video. Ask: "match the shots to the longest one", or name a reference: "match everything to V1 #4".

What happens:

1. Premiere renders a few frames of every V1 shot, effects included.
2. For each shot, the sidecar measures the mean brightness and the colour cast of the pixels that are neutral grey under that shot's own light (a white box, a desk, a keyboard).
3. It moves the Exposure, Temperature and Tint of the clip's Lumetri Color, renders again and measures again, until the shot sits with the reference within 0.04 stop and a cast you cannot see. One or two renders per shot, a few seconds each.

Pieces razored from one continuous take count as one shot and get the same values. If a clip already carries a Lumetri Color, its last one is adjusted rather than replaced, so a look you set stays. Premiere is busy while it renders.

Claude answers with a line per shot (stops and cast against the reference, before and after, the values set) and a sheet showing each shot before and after. A shot that is still off, or that hit the limit of a slider, is worth replacing or at least flagging.

The look for the whole video goes on afterwards, with the same values on every shot. For a tech channel the playbook keeps it light: "Lumetri on every V1 shot: Contrast 15, Vibrance 15, Saturation 105." Claude applies it with `setClipEffect` and checks two shots with `sequenceFrame`.

## 6. Motion graphics with addGraphic

Motion graphics need Node.js 22+ and the HyperFrames install that `install.bat` runs. To install them by hand: `npm ci --prefix motion` from the repository folder, then `node motion\node_modules\hyperframes\bin\hyperframes.mjs browser ensure`.

The bundled templates share one look: solid panels, one accent colour, Bahnschrift.

| Template | What it shows |
| --- | --- |
| `product` | Lower third: the product name and one line of detail |
| `specs` | A spec sheet of 2 to 6 label and value rows, drawn in one after the other |
| `chapter` | A chapter number and title in large condensed capitals |
| `callout` | A dot on one point of the frame, a leader line and a label |
| `stat` | One key number that counts up, with its unit and a short label |
| `verdict` | A score that counts up, then the good and bad points |

Each one sizes itself to the sequence, 16:9 or 9:16; in a vertical frame the bottom margin clears the Shorts interface.

A typical request: "Add a spec sheet at 12 s: 75% layout, gasket mount, 1.6 kg." Claude then:

1. Calls `listGraphics` for the templates and their variables.
2. Calls `previewGraphic`, which draws three moments of the graphic (entrance done, middle, just before it leaves) over the sequence's own frames. Nothing is rendered yet, so this is the moment to change wording or placement.
3. Calls `addGraphic`. HyperFrames renders the graphic at the sequence's size and frame rate to ProRes 4444 with alpha, in 20 to 40 s for a few seconds of graphic. The file is saved in a `chopshop graphics` folder next to your project and placed at the requested time on the lowest video track above V1 that is free for its whole length; a new track is added on top when none is. Claude then checks the frame Premiere renders.

Set **Settings > Graphics > Channel accent** to your channel's colour and every template uses it. Claude can also write a composition of its own in HTML with the same kit (`motion/templates/chop.css` and `chop.js`), for a one-off graphic the templates do not cover.

Premiere blends an alpha clip over the shot in linear light, not the way a browser does: a black panel at 86 % opacity lets about three times as much of the shot through as it does in Chrome. That is why the templates use solid panels, and why the preview blends the way Premiere does.

## 7. Editing in place

After the first build, Claude does not rebuild a sequence you may have touched. Notes are applied where the sequence stands, and changes you made by hand stay.

Give notes the way you would to an editor:

- "Shorten the lid shot by half a second at the head."
- "Remove the second keyboard close-up and close the gap."
- "Insert two seconds of the overhead camera before the sound test."
- "Dip to black between the unboxing and the sound test."

Claude first calls `readEdit`, which lists what the sequence really holds now: every clip in sequence and source seconds, the sound linked to each shot, the gaps and the transitions. It then calls `editSequence` with a list of steps:

| Step | Effect |
| --- | --- |
| `trim` | Makes a shot shorter or longer at the head or the tail. With ripple (the default), everything after it moves on every track. |
| `remove` | Removes a shot and its sound and closes the gap. |
| `insert` | Adds a stretch of a file at a time and pushes every track. |
| `overwrite` | Lays a stretch of a file over what is there. |
| `move` | Moves a shot onto free space. |
| `transition` | Puts a transition on one cut: a half-second Cross Dissolve by default, or any transition Premiere has by name (Dip to Black, Film Dissolve, Push...), with a Constant Power crossfade on the linked sound. |
| `removeTransition` | Removes the transitions over a point in time. |

Each step takes the shot's linked sound along, so picture and sound never drift apart. A clip on another track that crosses an edit point, such as music or a title, is left whole and reported. To keep your version untouched, ask Claude to work on a copy ("apply these notes to a copy called v2"); it passes `copyAs` and edits the copy.

On a tech channel the playbook keeps transitions rare: hard cuts by default, a short dissolve where time jumps inside one setup, Dip to Black between sections, never on a jump cut that is meant to be felt.

Claude checks the result with `watchEdit` after a batch of notes. Ask for a full rebuild only when you want a new version from scratch.

## 8. Troubleshooting

### Claude Desktop does not list the chopshopMCP tools

Symptom: the tools menu has no chopshopMCP entry, or Claude says it cannot reach Premiere. If you added the skill, Claude may still answer as an editor without touching Premiere.

1. Open **Settings > Developer > Edit Config** and check the `chopshopMCP` entry. Both paths must exist: `command` is a `python.exe` and `args` points to `sidecar\mcpServer.py` in the folder where the repository is now. A moved or renamed folder is the most common cause. Backslashes must be doubled.
2. Quit Claude Desktop from the system tray, not just the window, and open it again.
3. Read the server's log in `%APPDATA%\Claude\logs\mcp-server-chopshopMCP.log`. A Python traceback there names the problem, usually a missing package.
4. Check the interpreter from a terminal, with the same path as in the config:

   ```bat
   "C:\Path\To\python.exe" C:\Tools\chopshopMCP\sidecar\cli.py check
   ```

   It prints a JSON report; any value starting with `missing` is a package to install. Run `install.bat` again, or install the packages into that interpreter:

   ```bat
   "C:\Path\To\python.exe" -m pip install -r C:\Tools\chopshopMCP\sidecar\requirements.txt
   ```

5. Start the server by hand: `"C:\Path\To\python.exe" C:\Tools\chopshopMCP\sidecar\mcpServer.py`. It should wait silently for input (Ctrl+C to stop). An error at this point is the error Claude Desktop sees.

### The PY light is orange or red

- Orange: Python runs but some packages are missing. Hover the light to see which, then run `install.bat` again or install `sidecar\requirements.txt` into that interpreter as above.
- Red: the panel could not run Python at all, or could not find the sidecar. Open **Settings > Python**, put the full path to `python.exe` in **Interpreter** (the same one Claude Desktop uses) and click **Check setup**. A bare `python` may resolve to the Microsoft Store alias or to an older interpreter.

Python must be 3.10 or newer. `install.bat` skips older versions and the Store alias, and prints what it found when nothing qualifies.

### The Premiere bridge

Claude reaches Premiere only through the open panel. When the panel starts, it opens a port on `127.0.0.1` and writes the port and a fresh token to `%APPDATA%\chopshopMCP\bridge.json`; the MCP server reads that file on every call.

| What you see | Cause and fix |
| --- | --- |
| Claude says "premiere panel not running" | The panel has never been opened since the last restart. Open **Window > Extensions > chopshopMCP**. |
| Claude says "premiere panel not reachable" | Premiere or the panel was closed after it wrote the bridge file. Reopen the panel. |
| "bridge refused ...: 401 bad token" | Another Premiere instance or an older panel wrote the bridge file. Close extra Premiere windows and reopen the panel. |
| "function not allowed: ..." | The panel runs older code than the sidecar, typically right after updating the repository. Close and reopen the panel, or restart Premiere. |
| PR light red | The host script did not load. Close and reopen the panel. If it stays red, click **Diagnose** and include the report in an issue. |
| MCP light red | The bridge could not start. Hover the light or read the line under **Settings > Python** for the error, then reopen the panel. |

### The panel is missing from Window > Extensions

1. Run `install.bat` again. It sets `PlayerDebugMode` for CSXS 11 and 12, which Premiere 22 to 25 need to load an unsigned panel.
2. Check that `%APPDATA%\Adobe\CEP\extensions\chopshopMCP` exists and points to the repository's `panel` folder. If `install.bat` reports that the folder exists and is not a link, delete that folder and run it again.
3. Restart Premiere.

### Long jobs

`buildEdit`, `matchShots`, `addGraphic`, `previewGraphic`, `watchEdit` and the first `analyzeRush` run in the background. Claude calls them again until they finish, and you see "running" or "rendering" with a stage and a percentage in the meantime. Premiere is busy while it renders frames or a sequence, so leave it alone until the call returns.

### Reporting a bug

Open an issue with:

- what you asked Claude, what you expected and what you got;
- your Premiere version and UI language;
- the Self-test report (**Settings > Self-test**, then paste);
- for a problem with one sequence, the Diagnose report (open the sequence, **Settings > Diagnose**, then paste).
