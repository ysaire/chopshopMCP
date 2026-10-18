# Playbook: fast meme edits

The JamFam, Finzar, Phyz and Max0r school: gameplay or commentary as the canvas, the edit as the comedy. Every second earns its place with a joke, a reaction, a zoom or a sound.

## Measured on references

| per video | JamFam (Fortnite, 7 min) | Phyz (montage, 3 min) |
|---|---|---|
| cuts per minute | 73 | 61 |
| median shot | 0.38 s | 0.22 s |
| shots under 0.1 s | 22 % | 23 % |
| longest shot | 19 s (the payoff) | 11 s |
| shots with a zoom in | 40 % | 26 % |
| keyframed punch-ins inside shots | 231 in 506 shots | 70 in 175 |
| flashes per minute | 0.1 | 7.8 |

A "shot" here is any visual change the detector caught, word pops and fake interface steps included, so read the table as visual events. Faster than it feels: the median shot is under half a second, but long shots (2 to 4 s) carry the actual gameplay between bursts. Rhythm is burst, breathe, burst.

Finzar in story mode (a 26 min talk about his YouTube income) runs slower, 21 cuts per minute with a 1.1 s median shot and a-roll shots up to 36 s, but no number or claim goes by without a designed graphic: clips on tilted cards over a sky, 3D money props with the figure, himself cut out on a grid, years as 3D titles on location. Same energy, carried by motion design instead of cuts.

## Structure

- **Cold open (3 to 25 s)**: a cinematic intro (replay camera, dramatic light, split screens that multiply 1 > 2 > 3 panels) or the funniest moment of the video. Then the premise in one sentence, its key words captioned one at a time ("intro", "3 times", "face reveal") with a meme picture on each.
- **Chapters**: big bold title ("GAME 1", "ROUND 2") over a short meme clip, 1 to 1.5 s, with a hit sound.
- **Body**: gameplay with commentary. Dead time is cut without mercy; travel becomes 3 to 5 cuts of 0.2 to 0.5 s. Every fail, bold claim or awkward silence gets a reaction.
- **Payoff with contrast**: the promised moment is played against the style. JamFam ends a chaotic meme video with a black screen, silence, then a slow, letterboxed, warmly graded 19 s shot. Contrast is what makes it land.
- **End screen**: subscribe line and the next video, no recap.

## The comedy toolkit

- **Meme cutaway** (0.3 to 1.5 s): a real-life clip or a reaction face answering what just happened. It brings its own sound, then snaps back to the game. On fails, overconfident lines, absurd moments, after a punchline as punctuation.
- **Zoom punch**: a fast keyframed zoom (1.0 to 1.3 in 2 to 4 frames) on a face, a number, a detail (an FPS counter, a kill feed, a price) to stress it. Repeat the punch on each word of a line for a stutter effect.
- **Freeze, slow down, black and white** on a fail or a dramatic beat, often with a record scratch or a sad violin cue.
- **Image overlays**: a meme picture inset next to the subject (galaxy brain, clown, a face), 0.5 to 2 s, popping in.
- **Fake interface**: a mock video thumbnail, a notification, a chat message, animated in 3D tilt, then the camera pushes into a detail.
- **Letterbox** (black bars) for a mock dramatic moment, typewriter text on black ("I survived that") for deadpan.
- **Flashes**: white flash frames on impacts and section changes, fading over 3 to 10 frames (Phyz uses about 8 a minute, JamFam almost none).
- **Text stacking** (Phyz): a quote spelled out word by word, each word a hard pop with a flash, the whole line held 1 to 2 s once complete.

## Captions

- One word at a time, on the beat of the speech. Big, heavy rounded font, white with a thick dark outline, lower centre (about 0.7 of the height), lowercase or uppercase by channel. JamFam: one lowercase word. Phyz: uppercase, glow, stacked.
- Plan: `captions.style` = `pop` with `words: 1`, `uppercase` per the reference, the user's pop preset on each.
- Key words of the premise get their own caption even when nobody says them.

## Sound

- Music changes per section and drops out for jokes. Sound effects on almost every visual event: hit on text, whoosh on zooms, boom on chapter cards, vine boom on reaction faces.
- The meme's own audio replaces the game sound for its duration.
- Silence is a weapon: 0.5 to 4 s of nothing before the payoff.

## With chopshopMCP

- Meme cutaways: from the clip library (`searchLibrary("reaction shocked")`), as a segment with `media` for a smash cut that takes the screen and the sound, or as a `cover` with `audio: "replace"` over the gameplay so the game picture keeps its place in the cut.
- Reaction faces in a corner: `cover` with a `box`, `audio: "duck"` or `"mix"`.
- Sound effects on every visual event: `cover` of an audio file (`searchLibrary("whoosh", kind="audio")`), `volume` -6 to -10 dB, placed on the frame of the cut or the zoom.
- Zoom punches: segment `zoom` from / to, or the user's zoom presets with `applyPreset`. Punch stutter: several short segments of the same moment at rising scales.
- Captions one word at a time: plan `captions` (above). Image memes riding on the action: segment `attach` with an `image`.
- Flash, shake, black and white: the user's presets (`listPresets("flash")`, `"shake"`, `"black"`).
- Chapter cards over a meme: a meme segment with a `title` in front.

## Self check for this style

- Something changes on screen at least every 2 s, except the deliberate long shots.
- No joke without a beat after it, no meme without a reason in the story.
- The payoff is the slowest, cleanest moment of the video.
