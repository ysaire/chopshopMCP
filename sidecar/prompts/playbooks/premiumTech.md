# Playbook: premium tech and unboxing

The optimum school: a mature audience, trust built on clean images, a clear voice and real explanations. The edit serves the explanation; polish comes from light, colour, framing and motion graphics, not from effects.

## Measured on references

| per video | Glass mousepad (9.5 min) | Mouse accel (8.6 min) |
|---|---|---|
| cuts per minute | 27 | 39 |
| median shot | 1.1 s | 0.7 s |
| shots of 3 s or more | 28 % | 13 % |
| longest shot | 15 s | 16 s |
| voice | 220 words/min, over the whole video | 210 words/min, over the whole video |
| flashes | none to speak of | under 1 per minute |

The voice never stops: picture cuts happen under a continuous voice (a-roll on camera or voice over), so every cut is a J or L cut by construction. Pace comes from the words, not from effects.

## Structure

- **Cold open (5 to 15 s)**: fast, tactile product shots cut on hand movements (0.1 to 0.4 s each: the pad flexed, unboxed, placed), then the question of the video in one sentence on camera. "Glass pads are getting popular, but are they actually better?" No logo, no channel intro.
- **Context**: why it matters, what he used before, his stake in it (30 days, his own aim, his friends' clips). Viewers trust a test with stakes.
- **Chapters by question**: feel, speed, control, durability, noise, price. Each chapter: claim on camera, proof in b-roll or gameplay, verdict.
- **Proof**: gameplay with a hand cam inset in a corner (the hand on the pad while aiming), screen recordings of settings and graphs, measurements on screen.
- **Verdict**: a clear answer, who it is for and who should skip it. Then out, no long outro.

## Images

- A-roll: medium shot at the desk, dark background, soft key light from the side, one colour accent (a red pad on black and white). The camera does not move; cuts change the size (wide, medium, hands).
- B-roll: macro and hands. Texture close-ups with shallow depth of field, slow slides along the product, the product handled in real time. 2 to 6 s per shot, cut on the hand's movement.
- Colour: consistent and moody, practical lights in frame (RGB strips, a monitor glow), deep blacks, neutral skin. Every shot graded to match; no shot looks like a different day.
- Gameplay: kills and flicks cut to the beat of the music under the voice, 0.3 to 1.5 s per shot, the best clips first.

## Motion graphics

- Information appears as graphics, not as text dumps: stat cards that slide in with a blur (hours played, sensitivity, prices), graphs that draw themselves, a punch into the part of a settings window being discussed.
- Clean sans serif, white or the accent colour, generous margins, everything aligned to the same grid. Animation 0.2 to 0.4 s with ease out, never bounce.
- A screen recording sits on a coloured background card with a soft shadow, and the camera pushes into the relevant control.

## Sound

- The voice is the spine: clean, close, compressed, no room echo. Fillers and breaths cut, pacing tight but natural.
- Music low under the voice (about 18 dB down), changes between chapters, a few soft whooshes on graphics. Product sound (clicks, glide, the pad tapping the desk) brought forward on b-roll.

## With chopshopMCP

- Voice first is the natural workflow here: `transcribeMedia` the a-roll or voice over, keep the sentences, then cover them with b-roll and gameplay chosen by what is said (`plan.voiceOver` with `layoutVoiceOver`).
- A-roll with b-roll: the a-roll as segments, b-roll as `cover` with the default `audio: "mute"`, so the voice never breaks. B-roll from other cameras or files: `media` on the cover.
- J and L cuts on every change of camera or scene (`jcut` / `lcut` 0.2 to 0.6 s): the next voice starts before its picture, or the sentence ends over the next shot.
- Hand cam over gameplay: `cover` with a `box` in a corner, muted.
- Slow pushes (`zoom` from 1.0 to 1.08 over the shot) on b-roll and stills, cuts between a-roll sizes with `zoom` 1.0 and 1.25 to hide jump cuts.
- Graphics: `addGraphic` with the plugin's templates (`specs`, `stat`, `callout`, `chapter`, `verdict`) in the channel's accent, or the user's MOGRT templates (`listMogrts`) when they have their own, `attach` labels on products, `screens` to replace or clean up a monitor.
- Music: a `cover` of the track at -18 dB spanning the chapter; product sounds kept from the rush.
- Grade: `matchShots` first so the shots agree, then the user's Lumetri presets with `applyPreset`, one look for the whole video.

## Self check for this style

- Every claim has a proof on screen within a few seconds.
- No shot holds more than about 15 s without a new image, even on camera.
- Nothing flashy that a viewer could notice as an effect: if it calls attention to itself, it is wrong here.
