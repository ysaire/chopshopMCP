# Changelog

All notable changes to chopshopMCP are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.2.1] - 2026-10-18

First public release. Windows, Premiere Pro 25.x with the English UI, Claude Desktop through MCP. The panel needs no API key: Claude Desktop drives every edit.

### Added

- Rush analysis: motion, hard cuts, sound transients per track, speech and beats in one local pass, with contact sheets, full-resolution stills and a shot log Claude reuses for every edit.
- Edit plans built as Premiere sequences: cuts in any order, timelapses, punch-ins, vertical, square and portrait framing, titles, labels, captions and voice over, with a storyboard preview before building.
- Edits from several files: second cameras, b-roll, memes, sound effects and music as covers, J and L cuts, and a clip library searched by meaning.
- Editing in place with `readEdit` and `editSequence`: trims, removals, inserts, overwrites, moves and transitions applied to an existing sequence, each shot with its linked sound, without losing hand edits.
- Shot matching with `matchShots`: Lumetri Exposure, Temperature and Tint adjusted render after render until every shot sits with a reference.
- Motion graphics rendered by HyperFrames to ProRes 4444 with alpha: six templates (product, specs, chapter, callout, stat, verdict), custom HTML compositions, a preview blended the way Premiere blends, and a channel accent setting.
- Native masks: blur and pixelate zones written as Premiere pen masks keyed on every frame, grafted into a copy of the project file. Tracked zones follow the object's silhouette with SAM 2, or MobileSAM when `torch` is not installed.
- `refineMask` and the panel's Refine mask button: a rough pen mask turned into the object's silhouette over the In to Out range.
- Rendered matte and baked modes as fallbacks, and titles behind the subject with GPU matting.
- Effect presets read from `.prfpset` files and replayed with their eased curves, effect and keyframe editing on any clip, and motion graphics templates (`.mogrt`).
- 2D tracking: labels on objects, screen replacement with Corner Pin, stabilization.
- Reference breakdowns and a style memory that keeps your taste and corrections between sessions.
- Result checks: `sequenceFrame`, `watchEdit` (rhythm, black, frozen and silent stretches, loudness) and `exportEdit` for the master.
- The panel: PR, PY and MCP status lights, rush selection, Analyze for Claude, silence cuts, settings, Check setup, Diagnose and Self-test.
- `install.bat` and `uninstall.bat`, a user guide, a technical overview and contribution notes.

[Unreleased]: https://github.com/ysaire/chopshopMCP/compare/v1.2.1...HEAD
[1.2.1]: https://github.com/ysaire/chopshopMCP/releases/tag/v1.2.1
