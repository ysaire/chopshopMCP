# Playbook: the craft

How a good editor thinks, whatever the genre. Read once per session, before the genre playbook.

## Vocabulary

Use the words editors use, the user speaks this language.
- **Stringout**: every usable piece of the rush in order. **Selects**: the best of it. **Rough cut**: first assembly of the story. **Fine cut**: rhythm, trims and polish. **Picture lock**: the picture no longer changes, sound and grade follow.
- **A-roll**: what carries the story (talking head, voice, gameplay commentary). **B-roll**: what illustrates it (product shots, details, screen captures, inserts). **Cutaway**: a b-roll shot laid over the a-roll while its sound keeps running.
- **J-cut**: the next shot's sound starts before its picture. **L-cut**: the current shot's sound keeps going over the next picture. Both hide cuts and keep a voice flowing over b-roll.
- **Jump cut**: same framing, time skipped (talking heads). **Punch-in**: the same shot, cut to a tighter scale (1.2 to 1.5) to hide a jump cut or stress a word. **Smash cut**: abrupt cut to a contrasting scene, loud to quiet or calm to chaos. **Match cut**: two shots linked by a shape, a movement or a sound. **Montage**: many short shots condensing time or an idea.
- **Hook**: the first 1 to 5 s that earn the next 30. **Open loop**: a question raised and answered later. **Pattern interrupt**: a change (angle, meme, graphic, sound, speed) that resets attention. **Payoff**: the moment the video promised. **Callback**: a joke or image from earlier coming back.
- **Speed ramp**: speed changing inside a shot (fast into slow on the impact). **Freeze frame**, **impact frame** (one or two frames of flash or inverted colours on a hit), **screen shake**, **zoom punch**, **whip** (fast blurred move between shots).
- **Lower third**: name or info text in the lower part. **Overlay**: graphic or clip above the picture (a facecam, a meme, an arrow). **Chroma key**: green screen removal. **Grade**: colour for mood; **correction** first, then the look.
- **Room tone**: the silence of the place, never cut to digital silence under a voice. **Ducking**: music dipped under speech. **SFX**: whoosh on moves, hit on impacts, riser before a reveal, pop on text.

## Six things a cut must serve, in this order

Story beats rhythm beats looks. When two rules fight, the higher one wins.
1. Emotion: what should the viewer feel right now.
2. Story: does the cut move it forward.
3. Rhythm: does it land at the right moment (on the beat, on the breath, on the action).
4. Eye trace: where the eye is at the end of the shot is where it should find something on the next one.
5. Screen direction and framing: no accidental flips, no two near identical framings back to back (that reads as a mistake, unless it is a deliberate jump cut).
6. Space: the viewer must know where things are.

## Cutting

- Cut on action: in the middle of a movement, the motion hides the cut. Cut on sound: on a transient, a consonant, a beat.
- Enter late, leave early: start a shot when the action starts, leave when the information is delivered. The 0.1 to 0.3 s after a sound decays is enough.
- Every shot answers one question. If you can't say what a shot adds, cut it.
- Vary shot lengths. A steady 2 s rhythm feels mechanical; long, short, short, long feels edited. Accelerate into a payoff, breathe after it (0.3 to 0.8 s on a reaction or a still frame).
- Comedy: the punchline lands, hold a beat (0.3 to 0.6 s) for the reaction, then cut. A meme or sound effect after a joke is punctuation, not a replacement for the joke. Cutting a beat too early kills a laugh; a beat too late kills the energy.
- Speech: never cut inside a word, remove breaths and fillers only when the rhythm needs it, keep a tiny pause (80 to 150 ms) between sentences so it still sounds human.
- Jump cuts on a talking head: alternate scale (1.0, then 1.15 to 1.3) so each cut reads as intentional.

## Retention

- Hook: open on the most interesting image or sentence, a result before the process, a question, a conflict, or the end teased. No logo intro, no "hey guys", no context before the hook.
- Something new on screen every few seconds: shorts every 1 to 3 s, energetic youtube every 3 to 8 s, premium long form every 5 to 15 s. New does not mean a cut: a zoom, a graphic, a b-roll insert, a sound all count.
- Keep an open loop running: tease what comes, pay it off later, open the next one before closing the last.
- End on the payoff or on a loop back to the first frame (shorts). Kill the wind-down.

## Sound is half the picture

- Bad sound ends a video faster than bad picture. Voice first: clear, steady level, no clipped peaks.
- J and L cuts on every voice that runs over b-roll, so the voice never stops at a picture cut.
- SFX on what moves: whoosh on zooms and slides, hit on impacts and text slams, riser before a reveal, a short silence (0.2 to 0.5 s) right before a big moment makes it hit harder.
- Music: change or drop it on section changes, cut on its beats, duck it 12 to 18 dB under speech.

## Picture

- Correct before you grade: exposure and white balance consistent from shot to shot, then the look.
- Motion: slow pushes (1.0 to 1.1 over the shot) keep static shots alive; punches and shakes only on real impacts.
- Text: readable in under a second, high contrast, safe margins (vertical: keep text between 15 % and 70 % of the height, away from the platform buttons).

## Self review before reporting

Watch the result with `previewFrame` and the build report, then check:
- The first 3 s: would a stranger keep watching.
- Any shot longer than the genre allows without something new happening.
- Any cut inside a word, any sound that stops dead at a cut.
- The payoff: is it the strongest moment, is it given room.
- The style memory (`getStyle`): did you follow every rule the user gave, especially "never do".
Say what you would still improve if you had the footage or the tools.
