// editing a sequence the user may have touched: only what is asked changes, picture and linked sound together

const test = require('node:test');
const assert = require('node:assert');

const { makePremiere, makeClip, loadHost, hostCall } = require('./hostMock');
const { makeLiveSequence, makeProjectItem } = require('./liveTimeline');

const rush = makeProjectItem('C:/Rushes/rush.mp4', 1);
const music = makeProjectItem('C:/Music/calm.wav', 1);
const broll = makeProjectItem('C:/Rushes/broll.mp4', 1);

function setup() {
    const registry = [];
    // three shots of the rush with their sound on A1, music under everything on A2
    const seq = makeLiveSequence('Edit seq', 'edit-1', {
        video: [[{ item: rush, start: 0, in: 10, out: 14, link: 'a' }, { item: rush, start: 4, in: 20, out: 22, link: 'b' },
                 { item: rush, start: 6, in: 30, out: 33, link: 'c' }]],
        audio: [[{ item: rush, start: 0, in: 10, out: 14, link: 'a' }, { item: rush, start: 4, in: 20, out: 22, link: 'b' },
                 { item: rush, start: 6, in: 30, out: 33, link: 'c' }],
                [{ item: music, start: 0, in: 0, out: 9 }]]
    }, registry);
    const premiere = makePremiere({ clips: [makeClip('C:/Rushes/rush.mp4', 0, 0, 1)], extraSequences: [seq], projectItems: [rush, music, broll] });
    registry.push = (x) => premiere.sequences.push(x);
    premiere.project.activeSequence = seq;
    return { ctx: loadHost(premiere), seq, premiere };
}

function edit(ctx, ops) {
    const res = hostCall(ctx, 'editSequence', { sequenceId: 'edit-1', ops });
    assert.strictEqual(res.ok, true, res.error);
    return res.result;
}

function spans(state, label) {
    const list = label.charAt(0) === 'V' ? state.video : state.audio;
    const track = list[Number(label.substring(1)) - 1];
    return track.clips.map((c) => [round(c.start), round(c.end), round(c.inPoint)]);
}

function round(x) { return Math.round(x * 1000) / 1000; }

test('readSequence shows every track, which sound belongs to which picture, and the gaps', () => {
    const { ctx, seq } = setup();
    seq.videoTracks[0].items[1]._e = 5.5;
    const res = hostCall(ctx, 'readSequence', { sequenceId: 'edit-1' });
    assert.strictEqual(res.ok, true, res.error);
    const s = res.result;
    assert.strictEqual(s.name, 'Edit seq');
    assert.deepStrictEqual(spans(s, 'V1'), [[0, 4, 10], [4, 5.5, 20], [6, 9, 30]]);
    assert.deepStrictEqual(s.video[0].clips[0].linked, ['A1 #0']);
    assert.deepStrictEqual(s.audio[1].clips[0].linked, []);
    assert.strictEqual(s.audio[1].clips[0].mediaPath, 'C:/Music/calm.wav');
    assert.deepStrictEqual(s.video[0].gaps, [[5.5, 6]]);
    assert.strictEqual(s.duration, 9);
});

test('trimming the tail of a shot trims its sound and pulls what follows, music left whole', () => {
    const { ctx } = setup();
    const r = edit(ctx, [{ op: 'trim', clip: 'V1 #1', end: 5.5 }]);
    assert.deepStrictEqual(spans(r.state, 'V1'), [[0, 4, 10], [4, 5.5, 20], [5.5, 8.5, 30]]);
    assert.deepStrictEqual(spans(r.state, 'A1'), [[0, 4, 10], [4, 5.5, 20], [5.5, 8.5, 30]]);
    assert.deepStrictEqual(spans(r.state, 'A2'), [[0, 9, 0]]);
    assert.deepStrictEqual(r.results[0], { op: 'trim', clip: 'V1 #1', ok: true, moved: 2, spanning: ['A2 #0'] });
});

test('the source out point read back is the one the trim gives, not the stale one premiere reports', () => {
    const { ctx } = setup();
    const r = edit(ctx, [{ op: 'trim', clip: 'V1 #1', end: 5.5 }, { op: 'trim', clip: 'V1 #2', start: 7 }]);
    assert.deepStrictEqual(r.state.video[0].clips.map((c) => round(c.outPoint)), [14, 21.5, 33]);
});

test('trimming the head of a shot drops its first frames and closes the hole', () => {
    const { ctx } = setup();
    const r = edit(ctx, [{ op: 'trim', clip: 'V1 #2', start: 7 }]);
    assert.deepStrictEqual(spans(r.state, 'V1'), [[0, 4, 10], [4, 6, 20], [6, 8, 31]]);
    assert.deepStrictEqual(spans(r.state, 'A1'), [[0, 4, 10], [4, 6, 20], [6, 8, 31]]);
});

test('lengthening a head reveals earlier frames and pushes what follows', () => {
    const { ctx } = setup();
    const r = edit(ctx, [{ op: 'trim', clip: 'V1 #1', start: 3 }]);
    assert.deepStrictEqual(spans(r.state, 'V1'), [[0, 4, 10], [4, 7, 19], [7, 10, 30]]);
    assert.deepStrictEqual(spans(r.state, 'A1'), [[0, 4, 10], [4, 7, 19], [7, 10, 30]]);
});

test('a trim can name source times, which earlier steps never move', () => {
    const { ctx } = setup();
    const r = edit(ctx, [{ op: 'remove', clip: 'V1 #0' }, { op: 'trim', clip: 'V1 #2', in: 30.5, out: 32 }]);
    assert.deepStrictEqual(spans(r.state, 'V1'), [[0, 2, 20], [2, 3.5, 30.5]]);
    assert.deepStrictEqual(spans(r.state, 'A1'), [[0, 2, 20], [2, 3.5, 30.5]]);
});

test('a trim without ripple leaves the hole', () => {
    const { ctx } = setup();
    const r = edit(ctx, [{ op: 'trim', clip: 'V1 #1', end: 5, ripple: false }]);
    assert.deepStrictEqual(spans(r.state, 'V1'), [[0, 4, 10], [4, 5, 20], [6, 9, 30]]);
    assert.deepStrictEqual(r.state.video[0].gaps, [[5, 6]]);
});

test('lengthening a tail pushes what follows to make room', () => {
    const { ctx } = setup();
    const r = edit(ctx, [{ op: 'trim', clip: 'V1 #1', end: 7 }]);
    assert.deepStrictEqual(spans(r.state, 'V1'), [[0, 4, 10], [4, 7, 20], [7, 10, 30]]);
    assert.deepStrictEqual(spans(r.state, 'A1'), [[0, 4, 10], [4, 7, 20], [7, 10, 30]]);
});

test('removing a shot removes its sound and closes the gap', () => {
    const { ctx } = setup();
    const r = edit(ctx, [{ op: 'remove', clip: 'V1 #1' }]);
    assert.deepStrictEqual(spans(r.state, 'V1'), [[0, 4, 10], [4, 7, 30]]);
    assert.deepStrictEqual(spans(r.state, 'A1'), [[0, 4, 10], [4, 7, 30]]);
    assert.deepStrictEqual(r.results[0].spanning, ['A2 #0']);
});

test('inserting a file pushes every track, its sound included', () => {
    const { ctx } = setup();
    const r = edit(ctx, [{ op: 'insert', media: 'C:/Rushes/broll.mp4', in: 100, out: 101.5, at: 4 }]);
    assert.deepStrictEqual(spans(r.state, 'V1'), [[0, 4, 10], [4, 5.5, 100], [5.5, 7.5, 20], [7.5, 10.5, 30]]);
    assert.deepStrictEqual(spans(r.state, 'A1'), [[0, 4, 10], [4, 5.5, 100], [5.5, 7.5, 20], [7.5, 10.5, 30]]);
    assert.deepStrictEqual(r.results[0], { op: 'insert', ok: true, media: 'C:/Rushes/broll.mp4', at: 4, length: 1.5, mode: 'insert' });
    assert.deepStrictEqual([broll.marks.in, broll.marks.out], [null, null]);
});

test('a file not in the project yet is imported before it is placed', () => {
    const { ctx, premiere } = setup();
    const r = edit(ctx, [{ op: 'overwrite', media: 'C:/Rushes/new take.mp4', in: 5, out: 6, at: 4.5 }]);
    assert.deepStrictEqual(premiere.project.imported, ['C:/Rushes/new take.mp4']);
    assert.deepStrictEqual(spans(r.state, 'V1'), [[0, 4, 10], [4, 4.5, 20], [4.5, 5.5, 5], [5.5, 6, 21.5], [6, 9, 30]]);
});

test('the rush can be named by a clip that already shows it', () => {
    const { ctx } = setup();
    const r = edit(ctx, [{ op: 'insert', sameAs: 'V1 #0', in: 50, out: 51, at: 9 }]);
    assert.deepStrictEqual(spans(r.state, 'V1').pop(), [9, 10, 50]);
    assert.strictEqual(r.state.video[0].clips[3].mediaPath, 'C:/Rushes/rush.mp4');
});

test('moving a shot takes its sound along and refuses a spot that is taken', () => {
    const { ctx } = setup();
    let r = edit(ctx, [{ op: 'move', clip: 'V1 #2', to: 12 }]);
    assert.deepStrictEqual(spans(r.state, 'V1'), [[0, 4, 10], [4, 6, 20], [12, 15, 30]]);
    assert.deepStrictEqual(spans(r.state, 'A1'), [[0, 4, 10], [4, 6, 20], [12, 15, 30]]);
    const res = hostCall(ctx, 'editSequence', { sequenceId: 'edit-1', ops: [{ op: 'move', clip: 'V1 #2', to: 5 }] });
    assert.strictEqual(res.ok, true, res.error);
    assert.strictEqual(res.result.results[0].ok, false);
    assert.match(res.result.results[0].error, /V1 #1 already sits at 4-6/);
    assert.deepStrictEqual(spans(res.result.state, 'V1'), [[0, 4, 10], [4, 6, 20], [12, 15, 30]]);
});

test('every label in one call names the shots as they were before it', () => {
    const { ctx } = setup();
    const r = edit(ctx, [{ op: 'remove', clip: 'V1 #0' }, { op: 'trim', clip: 'V1 #2', end: 4.5 }]);
    assert.deepStrictEqual(spans(r.state, 'V1'), [[0, 2, 20], [2, 4.5, 30]]);
});

test('a failed step stops the call and says which', () => {
    const { ctx } = setup();
    const res = hostCall(ctx, 'editSequence', { sequenceId: 'edit-1', ops: [{ op: 'remove', clip: 'V1 #1' }, { op: 'trim', clip: 'V3 #0', end: 1 }, { op: 'remove', clip: 'V1 #0' }] });
    assert.strictEqual(res.ok, true, res.error);
    assert.deepStrictEqual(res.result.results.map((x) => x.ok), [true, false]);
    assert.match(res.result.results[1].error, /no clip V3 #0/);
    assert.deepStrictEqual(spans(res.result.state, 'V1'), [[0, 4, 10], [4, 7, 30]]);
});

test('copyAs edits a copy under a new name and leaves the version of the user alone', () => {
    const { ctx, premiere } = setup();
    const res = hostCall(ctx, 'editSequence', { sequenceId: 'edit-1', copyAs: 'Edit seq v2', ops: [{ op: 'remove', clip: 'V1 #1' }] });
    assert.strictEqual(res.ok, true, res.error);
    assert.strictEqual(res.result.state.name, 'Edit seq v2');
    assert.notStrictEqual(res.result.state.sequenceId, 'edit-1');
    assert.deepStrictEqual(spans(res.result.state, 'V1'), [[0, 4, 10], [4, 7, 30]]);
    const original = hostCall(ctx, 'readSequence', { sequenceId: 'edit-1' }).result;
    assert.deepStrictEqual(spans(original, 'V1'), [[0, 4, 10], [4, 6, 20], [6, 9, 30]]);
    assert.ok(premiere.sequences.some((x) => x.name === 'Edit seq v2'));
});

test('editSequence and readSequence go through the bridge whitelist', () => {
    const { allowedFns } = require('../panel/js/bridgeServer');
    assert.ok(allowedFns.indexOf('editSequence') >= 0 && allowedFns.indexOf('readSequence') >= 0);
});

function transitions(state, label) {
    const list = label.charAt(0) === 'V' ? state.video : state.audio;
    return list[Number(label.substring(1)) - 1].transitions.map((t) => [t.name, round(t.start), round(t.end)]);
}

test('a transition on a cut dissolves the picture and the linked sound together', () => {
    const { ctx } = setup();
    const r = edit(ctx, [{ op: 'transition', clip: 'V1 #1' }]);
    assert.deepStrictEqual(r.results[0], { op: 'transition', clip: 'V1 #1', ok: true, name: 'Cross Dissolve', side: 'start', at: 4, duration: 0.5, audio: ['A1 #1'] });
    assert.deepStrictEqual(transitions(r.state, 'V1'), [['Cross Dissolve', 3.75, 4.25]]);
    assert.deepStrictEqual(transitions(r.state, 'A1'), [['Constant Power', 3.75, 4.25]]);
    assert.deepStrictEqual(transitions(r.state, 'A2'), []);
});

test('a transition can sit at the end of a shot, start at the cut and leave the sound alone', () => {
    const { ctx } = setup();
    const r = edit(ctx, [{ op: 'transition', clip: 'V1 #1', side: 'end', name: 'Dip to Black', duration: 1, align: 'start', audio: false }]);
    assert.deepStrictEqual(transitions(r.state, 'V1'), [['Dip to Black', 6, 7]]);
    assert.deepStrictEqual(transitions(r.state, 'A1'), []);
});

test('an unknown transition fails the step and names some real ones', () => {
    const { ctx } = setup();
    const res = hostCall(ctx, 'editSequence', { sequenceId: 'edit-1', ops: [{ op: 'transition', clip: 'V1 #1', name: 'Swoosh' }] });
    assert.strictEqual(res.result.results[0].ok, false);
    assert.match(res.result.results[0].error, /no transition named Swoosh: Cross Dissolve, Dip to Black/);
});

test('removeTransition takes away what sits on a cut, on every track by default', () => {
    const { ctx } = setup();
    edit(ctx, [{ op: 'transition', clip: 'V1 #1' }]);
    const r = edit(ctx, [{ op: 'removeTransition', at: 4 }]);
    assert.deepStrictEqual(r.results[0].removed, ['V1 Cross Dissolve 3.75-4.25', 'A1 Constant Power 3.75-4.25']);
    assert.deepStrictEqual(transitions(r.state, 'V1'), []);
    assert.deepStrictEqual(transitions(r.state, 'A1'), []);
});

test('a second transition on the same cut replaces the first', () => {
    const { ctx } = setup();
    edit(ctx, [{ op: 'transition', clip: 'V1 #1' }]);
    const r = edit(ctx, [{ op: 'transition', clip: 'V1 #0', side: 'end', name: 'Dip to White', duration: 1 }]);
    assert.deepStrictEqual(r.results[0].replaced, ['V1 Cross Dissolve 3.75-4.25', 'A1 Constant Power 3.75-4.25']);
    assert.deepStrictEqual(transitions(r.state, 'V1'), [['Dip to White', 3.5, 4.5]]);
    assert.deepStrictEqual(transitions(r.state, 'A1'), [['Constant Power', 3.5, 4.5]]);
});

test('placing a transition leaves the sequence the user had open', () => {
    const { ctx, premiere } = setup();
    const other = premiere.sequences.find((s) => s.sequenceID !== 'edit-1');
    premiere.project.activeSequence = other;
    edit(ctx, [{ op: 'transition', clip: 'V1 #1' }]);
    assert.strictEqual(premiere.project.activeSequence, other);
});

test('a shot whose sound sits on two tracks gets the audio transition on both', () => {
    const premiereSeq = makeLiveSequence('Stereo seq', 'stereo-1', {
        video: [[{ item: rush, start: 0, in: 10, out: 14, link: 'a' }, { item: rush, start: 4, in: 20, out: 22, link: 'b' }]],
        audio: [[{ item: rush, start: 0, in: 10, out: 14, link: 'a' }, { item: rush, start: 4, in: 20, out: 22, link: 'b' }],
                [{ item: rush, start: 0, in: 10, out: 14, link: 'a' }, { item: rush, start: 4, in: 20, out: 22, link: 'b' }]]
    });
    const premiere = makePremiere({ clips: [makeClip('C:/Rushes/rush.mp4', 0, 0, 1)], extraSequences: [premiereSeq], projectItems: [rush] });
    const ctx = loadHost(premiere);
    const res = hostCall(ctx, 'editSequence', { sequenceId: 'stereo-1', ops: [{ op: 'transition', clip: 'V1 #1' }] });
    assert.deepStrictEqual(res.result.results[0].audio, ['A1 #1', 'A2 #1']);
    assert.deepStrictEqual(transitions(res.result.state, 'A1'), [['Constant Power', 3.75, 4.25]]);
    assert.deepStrictEqual(transitions(res.result.state, 'A2'), [['Constant Power', 3.75, 4.25]]);
});

test('a short move along its own track is not blocked by the clip itself', () => {
    const { ctx } = setup();
    edit(ctx, [{ op: 'remove', clip: 'V1 #1', ripple: false }]);
    const r = edit(ctx, [{ op: 'move', clip: 'V1 #1', to: 5 }]);
    assert.strictEqual(r.results[0].ok, true);
    assert.deepStrictEqual(spans(r.state, 'V1'), [[0, 4, 10], [5, 8, 30]]);
});

test('readSequence gives the frame size and rate a graphic is rendered at', () => {
    const { ctx } = setup();
    const res = hostCall(ctx, 'readSequence', { sequenceId: 'edit-1' });
    assert.deepStrictEqual([res.result.width, res.result.height, res.result.fps], [1920, 1080, 60]);
});

test('laying a clip on a video track the sequence does not have yet adds the tracks up to it', () => {
    const { ctx, premiere } = setup();
    const other = premiere.sequences.find((s) => s.sequenceID !== 'edit-1');
    premiere.project.activeSequence = other;
    const r = edit(ctx, [{ op: 'overwrite', media: 'C:/graphics/product.mov', in: 0, out: 2, at: 1, track: 'V3' }]);
    assert.strictEqual(r.results[0].ok, true, r.results[0].error);
    assert.strictEqual(r.results[0].addedTracks, 2);
    assert.deepStrictEqual(r.state.video.map((t) => t.name), ['V1', 'V2', 'V3']);
    assert.deepStrictEqual(spans(r.state, 'V3'), [[1, 3, 0]]);
    assert.deepStrictEqual(spans(r.state, 'A1'), [[0, 4, 10], [4, 6, 20], [6, 9, 30]]);
    assert.strictEqual(premiere.project.activeSequence, other);
});

test('dropMedia takes unused files out of the project and keeps the ones a sequence still plays', () => {
    const { ctx, premiere } = setup();
    const res = hostCall(ctx, 'dropMedia', { paths: ['C:/Rushes/broll.mp4', 'C:/Rushes/rush.mp4', 'C:/nowhere/x.mov'] });
    assert.strictEqual(res.ok, true, res.error);
    assert.deepStrictEqual(res.result.removed, ['broll.mp4']);
    assert.deepStrictEqual(res.result.left, [
        { path: 'C:/Rushes/rush.mp4', name: 'rush.mp4', why: 'used in Rush seq' },
        { path: 'C:/nowhere/x.mov', why: 'not in the project' }
    ]);
    const names = Array.from({ length: premiere.project.rootItem.children.numItems }, (_, i) => premiere.project.rootItem.children[i].name);
    assert.deepStrictEqual(names, ['rush.mp4', 'calm.wav']);
});

test('dropMedia is on the bridge whitelist', () => {
    const { allowedFns } = require('../panel/js/bridgeServer');
    assert.ok(allowedFns.indexOf('dropMedia') >= 0);
});
