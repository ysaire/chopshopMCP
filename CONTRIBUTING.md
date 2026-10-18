# Contributing to chopshopMCP

Bug reports, playbook tweaks and pull requests are welcome. This page covers the development setup, the tests and the conventions the code follows.

## Project layout

| Path | What it is |
| --- | --- |
| `panel/` | The CEP panel: `index.html`, `css/`, `js/` (UI, bridge server, sidecar runner, self-test) and `jsx/host.jsx` (ExtendScript and QE inside Premiere) |
| `sidecar/` | The Python side: `mcpServer.py` (MCP tools), `cli.py` (commands the panel runs) and the modules behind them |
| `sidecar/prompts/playbooks/` | Editing know-how Claude reads through `getPlaybook`, in Markdown |
| `motion/` | HyperFrames, pinned in `package.json`, and the motion graphic templates in `templates/` |
| `skill/chopshop-editor/` | The optional Claude Desktop skill |
| `tests/` | Python tests, Node tests for the panel and host script, and the simulated Premiere they run against |
| `docs/` | User guide and technical overview |

## Setup

You need Python 3.10 or newer (3.12 to 3.14 are tested) and Node.js 20 or newer for the panel tests. The real motion graphics render test needs Node.js 22 or newer.

```bash
git clone https://github.com/ysaire/chopshopMCP.git
cd chopshopMCP
python -m pip install -r sidecar/requirements.txt pytest
npm ci --prefix motion
```

`npm ci --prefix motion` installs HyperFrames and GSAP. Without it, the test that renders a real graphic is skipped and everything else runs.

To work against a real Premiere, run `install.bat` from your clone: it links `panel/` into Premiere's extensions folder, so edits in the clone are what Premiere loads.

## Running the tests

```bash
python -m pytest
node --test tests/testPanel.js tests/testEditInPlace.js
```

The Python suite covers perception, plans, the XML writer, presets, captions, voice over, tracking, the mask graft, shot matching, graphics and the MCP tools end to end against a fake Premiere. The Node suite loads `panel/jsx/host.jsx` in a simulated Premiere DOM (QE included), checks the bridge's security rules and the self-test, and replays editing in place on a live timeline.

The simulated Premiere lives in `tests/hostMock.js`, `tests/liveTimeline.js` and `tests/fakePremiereServer.js`. It reproduces behaviour observed in Premiere 25 (how trims, ripples and linked clips behave, a new object on every read of a track item), and it saves a real project file and reads sequences back from one, so the native mask round trip runs offline. `tests/data/projectExtract.xml` is a structural extract of a Premiere 25 project with a hand-drawn mask that the graft tests parse and patch.

Some tests are opt-in or need extra tools:

- `CHOPSHOP_REAL_MODELS=1` runs the tests that load the real segmentation models (they download on first use).
- `CHOPSHOP_PYTHON` picks the interpreter the panel's self-test check runs. Without it the test tries `py -3`, then `python`.

GitHub Actions runs both suites on Windows and Ubuntu with Python 3.12 and 3.14 on every push and pull request.

## Working on the panel

- With the panel open in Premiere, browse to `http://localhost:8088` in Chrome to debug it (the port is set in `panel/.debug`).
- After editing `panel/jsx/host.jsx`, have the panel re-evaluate it in place through the bridge:

  ```bash
  python -c "import sys; sys.path.insert(0, 'sidecar'); from bridgeClient import callPremiere; print(callPremiere('reloadHost'))"
  ```

- Changes under `panel/js`, the bridge whitelist included, load with `reloadPanel` the same way: the panel answers, reloads itself and writes a new `bridge.json`. HTML and CSS changes need it too.
- A new host function must be added to the whitelist in `panel/js/bridgeServer.js` before the sidecar can call it.
- Run **Settings > Self-test** in a real Premiere before sending a change to `host.jsx`, and say in the pull request which Premiere version you tested.

## Conventions

- camelCase everywhere, Python included.
- Comment only what the code cannot say: a workaround, a Premiere quirk, a security boundary, a magic value. One short lowercase line. No comments that restate the code or describe its history.
- `host.jsx` is ExtendScript, so ES3: no `let`, arrow functions or array methods newer than ES3, and no global `JSON` (use the file's own `toJson` and `parseJson`).
- The panel runs in CEF on Chrome 99: no `:has()`, `color-mix()` or container queries.
- Tool descriptions in `sidecar/mcpServer.py` are what Claude reads to decide how to call a tool. Keep them precise about units (source or sequence seconds, fractions of the frame) and about what a call returns.
- Playbooks are prompts. Keep them short, concrete and in numbers where possible.

## Pull requests

1. Keep a pull request to one change, with tests for new behaviour.
2. Make sure both test suites pass.
3. Describe what changed for the user and how you checked it, in Premiere if the change touches it.
4. Add a line to the `Unreleased` section of `CHANGELOG.md`.

## Reporting bugs

Use the issue template. The Self-test report (**Settings > Self-test**) and, for a problem with one sequence, the Diagnose report (**Settings > Diagnose**) usually tell more than a description.
