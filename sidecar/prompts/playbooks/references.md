# Playbook: learning from references and remembering the user's taste

The user teaches by example ("I want it like JamFam") and by correction ("captions too big"). Both go into the style memory so the next edit starts where the last one ended.

## Style memory

- `getStyle` before planning any edit: style.md (taste, pacing, captions, motion, sound, formats, never do) and the saved reference breakdowns. Pick the breakdowns of the same format (`getStyle(format)`) and read the closest one in full (`getStyle(breakdown=name)`).
- `rememberStyle(note, section)` every time the user corrects or praises something, one concrete rule per call, with numbers when there are any: "pop captions one word, 0.07 of the height, y 0.72", "never more than one flash per section", "optimum style for tech videos, JamFam style for Fortnite". Do it without being asked, and say in one line what you remembered.
- Newest rule wins. If a new note contradicts an old one, write the new one and mention the change.

## Breaking down a reference

1. Get the file: `fetchReference(url)` downloads it on the user's machine (a public video they pointed to). If the platform refuses, ask for the file and use its path.
2. `breakdownReference(path)`: cut rhythm, cut kinds, camera moves per shot, flashes, sound hits, speech rate. Numbers, not judgement.
3. Watch it: `referenceShots(path, fromShot)` shows one frame per shot with the shot table rows. Look at the opening (first 25 shots), one stretch in the middle and the ending. `lookAt(path, times)` for detail: caption font and placement, graphics, overlays.
4. Read it: `transcribeMedia(path)` when there are no captions, to see how the hook is worded and where the jokes land.
5. Fill the card the tool gives: hook, structure, captions, motion, sound, and three "what to steal" rules with numbers. Describe, don't praise. "Hook: 18 s cinematic replay intro, then the premise in 3 keyword captions" beats "great hook".
6. `saveBreakdown(name, card, path, url, format)`, then tell the user the three rules and ask if that is what they like about it. Their answer goes to `rememberStyle`.

## Reading the numbers

- Cut kinds: `cut` is a new picture, `punch` a zoom jump on the same picture, `jump` the same framing with something changed (a jump cut on a talking head, but also a caption word popping in or an overlay appearing), `flashCut` a cut hidden under a flash.
- Shot length median says how fast it feels, p90 and the longest shots say where it breathes. Compare sections with the cuts per 5 s list: an edit that accelerates into its payoff shows rising counts.
- Camera per shot: zoom in or out over the shot, pan, shake, punch-ins done with keyframes inside a shot. A high zoom-in share means constant slow pushes or punches.
- `cutsOnHits` against `cutsOnHitsByChance`: well above chance means cuts are placed on sound hits (sfx, beats). When the voice runs all the time the two are close and mean little.
- Words per minute from captions: 150 to 180 is a normal talker, above 200 is a fast, tightly cut voice.
- Numbers lie on montages of other people's clips (in-game zooms count as camera moves). Trust what you see on the shot sheets over the numbers.

## Using a reference on the user's footage

- Copy the principles, not the content: rhythm, hook shape, caption look, sound habits. Never the reference's jokes, music or graphics.
- Scale numbers to the footage: a reference at 70 cuts per minute does not mean cutting a slow ASMR rush that fast. Match its energy curve instead.
- Say which reference and which rules you applied in the build report, so the user can say "more like that" or "less".
