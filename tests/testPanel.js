const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');

const { makePremiere, makeClip, makeMotionClip, makeNestClip, makeInnerSequence, loadHost, hostCall } = require('./hostMock');
const { createBridge } = require('../panel/js/bridgeServer');
const { splitCommand } = require('../panel/js/sidecar');

const rushPath = 'C:\\Rushes\\take "01".mp4';

function setup() {
    // rush sits at 10s on the timeline, trimmed to source [5, 65]
    const premiere = makePremiere({ clips: [makeClip(rushPath, 10, 5, 65)] });
    return { premiere, ctx: loadHost(premiere) };
}

test('ping and json escaping survive the es3 round trip', () => {
    const { ctx } = setup();
    const res = hostCall(ctx, 'ping', { weird: 'quote " slash \\ line\u2028sep' });
    assert.strictEqual(res.ok, true);
    assert.strictEqual(res.result.version, '25.0.0');
});

test('unknown functions and host errors come back as ok:false', () => {
    const { ctx, premiere } = setup();
    assert.strictEqual(hostCall(ctx, 'deleteEverything').ok, false);
    premiere.app.project.activeSequence = null;
    const res = hostCall(ctx, 'setPlayhead', { seconds: 1 });
    assert.strictEqual(res.ok, false);
    assert.match(res.error, /no active sequence/);
});

test('getContext lists the rush with sequence settings', () => {
    const { ctx } = setup();
    const res = hostCall(ctx, 'getContext');
    assert.strictEqual(res.ok, true);
    const c = res.result;
    assert.strictEqual(c.sequence.ticksPerFrame, '8475667200');
    assert.strictEqual(c.sequence.width, 1080);
    assert.strictEqual(c.media.length, 1);
    assert.strictEqual(c.media[0].path, rushPath);
    assert.strictEqual(c.media[0].inSequence, true);
    assert.strictEqual(c.media[0].clipIn, 5);
});

function fileItem(p) { return { name: path.basename(p), type: 1, getMediaPath() { return p; } }; }
function binItem(name, items) {
    const children = { numItems: items.length };
    items.forEach((it, i) => { children[i] = it; });
    return { name, type: 2, children, getMediaPath() { return ''; } };
}

test('getContext opens a bin selected in the project panel and lists what it holds', () => {
    const music = binItem('music', [fileItem('C:/Music/calm.wav'), binItem('sfx', [fileItem('C:/Music/whoosh.wav')])]);
    const premiere = makePremiere({ clips: [makeClip(rushPath, 10, 5, 65)], projectSelection: [music, fileItem('C:/Rushes/b-roll.mp4')] });
    const ctx = loadHost(premiere);
    const c = hostCall(ctx, 'getContext').result;
    assert.deepStrictEqual(c.media.map((m) => [m.name, m.source, m.bin || null]), [
        ['calm.wav', 'project', 'music'], ['whoosh.wav', 'project', 'music/sfx'], ['b-roll.mp4', 'project', null], [path.basename(rushPath), 'sequence', null]]);
    assert.strictEqual(c.truncated, undefined);
});

test('getContext says when a big selection was cut short', () => {
    const many = binItem('library', Array.from({ length: 70 }, (_, i) => fileItem(`C:/Lib/clip${i}.wav`)));
    const premiere = makePremiere({ clips: [makeClip(rushPath, 10, 5, 65)], projectSelection: [many] });
    const c = hostCall(loadHost(premiere), 'getContext').result;
    assert.strictEqual(c.media.length, 60);
    assert.strictEqual(c.truncated, true);
});

test('markers map source time to sequence time through the clip', () => {
    const { ctx, premiere } = setup();
    const res = hostCall(ctx, 'addMarkersForMedia', {
        mediaPath: rushPath.toLowerCase().replace(/\\/g, '/'),
        markers: [
            { start: 20, end: 22, name: '[chop] badTake', comment: 'kept 12' },
            { start: 1, end: 3, name: 'outside', comment: 'before the in point' },
            { start: 64, end: 70, name: 'meta', comment: 'crosses the out point' }
        ],
        color: 1
    });
    assert.strictEqual(res.ok, true, res.error);
    assert.strictEqual(res.result.created, 2);
    const list = premiere.seq.markers.list;
    assert.strictEqual(list[0].start.seconds, 25); // 10 + (20 - 5)
    assert.strictEqual(list[0].end, 27);
    assert.strictEqual(list[0].color, 1);
    assert.strictEqual(list[1].name, '[chop] meta');
    assert.strictEqual(list[1].end, 70); // clamped to the clip out point (10 + 60)

    const cleared = hostCall(ctx, 'clearReviewMarkers');
    assert.strictEqual(cleared.result.removed, 2);
    assert.strictEqual(list.length, 0);
});

test('importSequenceXml opens the new sequence', () => {
    const { ctx, premiere } = setup();
    const xmlPath = path.join(os.tmpdir(), 'chopImport.xml');
    fs.writeFileSync(xmlPath, '<xmeml><sequence><name>rush [chop ai]</name></sequence></xmeml>');
    const res = hostCall(ctx, 'importSequenceXml', { xmlPath });
    assert.strictEqual(res.ok, true, res.error);
    assert.strictEqual(res.result.sequenceName, 'rush [chop ai]');
    assert.strictEqual(premiere.project.opened, res.result.sequenceId);
});

test('setPlayhead converts seconds to ticks', () => {
    const { ctx, premiere } = setup();
    hostCall(ctx, 'setPlayhead', { seconds: 2 });
    assert.strictEqual(premiere.seq.position, '508032000000');
});

test('exportFrame moves the playhead and renders through qe with premiere\'s own timecode', () => {
    const { ctx, premiere } = setup();
    const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'chopFrame')), 'frame.png');
    const res = hostCall(ctx, 'exportFrame', { seconds: 2, path: out });
    assert.strictEqual(res.ok, true, res.error);
    assert.strictEqual(premiere.seq.position, '508032000000');
    assert.deepStrictEqual(premiere.qe.exported, [{ sequence: 'Rush seq', timecode: '00;00;02;00', path: out }]);
    assert.strictEqual(res.result.timecode, '00;00;02;00');
    assert.strictEqual(res.result.sequence, 'Rush seq');
    assert.strictEqual(fs.existsSync(out), true);
});

test('getClips reports a sped nest clip in source seconds, undoing premiere\'s speed scaling of nests', () => {
    const nest = makeNestClip('n1', 'inner', 0, 45, 50);
    nest.getSpeed = () => 6;
    const premiere = makePremiere({ clips: [nest] });
    const ctx = loadHost(premiere);
    const res = hostCall(ctx, 'getClips', { targets: [{ track: 'V1', index: 0 }] });
    assert.strictEqual(res.ok, true, res.error);
    const clip = res.result.clips[0];
    assert.strictEqual(clip.speed, 6);
    assert.strictEqual(clip.inPoint, 270);
    assert.strictEqual(clip.outPoint, 300);
});

test('effect parameters are found by name even when premiere shows them with a trailing colon', () => {
    const { ctx } = setup();
    const res = hostCall(ctx, 'applyEffects', {
        targets: [{ track: 0, index: 0 }], mode: 'add',
        effects: [{ effect: 'Track Matte Key', params: [{ name: 'Matte', kind: 'value', value: 1 }, { name: 'Composite Using', kind: 'value', value: 1 }] }]
    });
    assert.strictEqual(res.ok, true, res.error);
    assert.deepStrictEqual(res.result.clips[0].issues, []);
    const fx = hostCall(ctx, 'clipEffects', { targets: [{ track: 'V1', index: 0 }] }).result.clips[0].components;
    const key = fx.find((c) => c.name === 'Track Matte Key');
    assert.deepStrictEqual(key.properties.map((p) => [p.name, p.value]).slice(0, 2), [['Matte:', 1], ['Composite Using:', 1]]);
});

test('exportSequence renders the whole sequence through the preset and reports the file', () => {
    const { ctx, premiere } = setup();
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'chopRender'));
    const preset = path.join(dir, 'review.epr');
    fs.writeFileSync(preset, '<epr/>');
    const out = path.join(dir, 'renders', 'edit.mp4');
    const res = hostCall(ctx, 'exportSequence', { sequenceId: 'seq-0', path: out, presetPath: preset });
    assert.strictEqual(res.ok, true, res.error);
    assert.deepStrictEqual(premiere.seq.exports, [{ path: out, preset, range: 0 }]);
    assert.strictEqual(res.result.sequence, 'Rush seq');
    assert.strictEqual(res.result.written, true);
    assert.ok(res.result.bytes > 0);
    const inOut = hostCall(ctx, 'exportSequence', { sequenceId: 'seq-0', path: out, presetPath: preset, range: 'inout' });
    assert.strictEqual(inOut.ok, true, inOut.error);
    assert.strictEqual(premiere.seq.exports[1].range, 1);
});

test('exportSequence refuses a preset that is not there', () => {
    const { ctx, premiere } = setup();
    const res = hostCall(ctx, 'exportSequence', { sequenceId: 'seq-0', path: path.join(os.tmpdir(), 'x.mp4'), presetPath: 'C:/nowhere/review.epr' });
    assert.strictEqual(res.ok, false);
    assert.match(res.error, /no export preset at C:\/nowhere\/review.epr/);
    assert.deepStrictEqual(premiere.seq.exports, []);
});

test('exportFrame is reachable through the bridge whitelist', () => {
    const { allowedFns } = require('../panel/js/bridgeServer');
    assert.ok(allowedFns.indexOf('exportFrame') >= 0);
    assert.ok(allowedFns.indexOf('exportSequence') >= 0);
});

test('saveProject writes the open project and reports its path', () => {
    const { ctx, premiere } = setup();
    const res = hostCall(ctx, 'saveProject', {});
    assert.strictEqual(res.ok, true, res.error);
    assert.strictEqual(premiere.project.saved, 1);
    assert.strictEqual(res.result.path, 'C:/proj/demo.prproj');
});

test('importProjectSequences brings sequences in from another project and reports the newcomers', () => {
    const { ctx, premiere } = setup();
    const res = hostCall(ctx, 'importProjectSequences', { path: 'C:/proj/donor.prproj', sequenceIds: ['donor-1'] });
    assert.strictEqual(res.ok, true, res.error);
    assert.deepStrictEqual(premiere.project.imported, ['C:/proj/donor.prproj']);
    assert.deepStrictEqual(res.result.sequences, [{ name: 'from donor.prproj donor-1', id: 'donor-1' }]);
});

test('importProjectSequences keeps the sequences to drop when the project brought nothing in', () => {
    const { ctx, premiere } = setup();
    hostCall(ctx, 'importProjectSequences', { path: 'C:/proj/stage.prproj', sequenceIds: ['stage-1'] });
    // premiere answers ok but imports nothing when it cannot load the sequence asked for
    const res = hostCall(ctx, 'importProjectSequences', { path: 'C:/proj/broken.prproj', sequenceIds: [], open: 'donor-1', drop: ['stage-1'] });
    assert.strictEqual(res.ok, true, res.error);
    assert.deepStrictEqual(res.result.sequences, []);
    assert.deepStrictEqual(res.result.dropped, { deleted: [], left: [{ id: 'stage-1' }], error: 'nothing was imported, nothing dropped' });
    assert.deepStrictEqual(premiere.sequences.map((s) => s.sequenceID), ['seq-0', 'stage-1']);
});

test('importProjectSequences can open one of the newcomers and drop the stage it replaces', () => {
    const { ctx, premiere } = setup();
    hostCall(ctx, 'importProjectSequences', { path: 'C:/proj/stage.prproj', sequenceIds: ['stage-1'] });
    const res = hostCall(ctx, 'importProjectSequences', { path: 'C:/proj/donor.prproj', sequenceIds: ['donor-1'], open: 'donor-1', drop: ['stage-1', 'gone'] });
    assert.strictEqual(res.ok, true, res.error);
    assert.strictEqual(res.result.opened, 'donor-1');
    assert.strictEqual(premiere.project.activeSequence.sequenceID, 'donor-1');
    assert.deepStrictEqual(res.result.dropped, { deleted: [{ name: 'from stage.prproj stage-1', id: 'stage-1' }], left: [] });
    assert.deepStrictEqual(premiere.sequences.map((s) => s.sequenceID), ['seq-0', 'donor-1']);
    // no path: nothing comes in, the drop alone happens
    const only = hostCall(ctx, 'importProjectSequences', { drop: ['donor-1'] });
    assert.strictEqual(only.ok, true, only.error);
    assert.deepStrictEqual(only.result.sequences, []);
    assert.deepStrictEqual(only.result.dropped.deleted.map((s) => s.id), ['donor-1']);
    assert.deepStrictEqual(premiere.sequences.map((s) => s.sequenceID), ['seq-0']);
});

test('deleteSequences drops sequences by id and skips the ones already gone', () => {
    const { ctx, premiere } = setup();
    hostCall(ctx, 'importProjectSequences', { path: 'C:/proj/donor.prproj', sequenceIds: ['donor-1', 'donor-2'] });
    const res = hostCall(ctx, 'deleteSequences', { ids: ['donor-1', 'nope', 'donor-2'] });
    assert.strictEqual(res.ok, true, res.error);
    assert.deepStrictEqual(res.result.deleted.map((s) => s.id), ['donor-1', 'donor-2']);
    assert.deepStrictEqual(res.result.left, []);
    assert.deepStrictEqual(premiere.sequences.map((s) => s.sequenceID), ['seq-0']);
});

test('deleteSequences reports what this premiere cannot delete', () => {
    const { ctx, premiere } = setup();
    delete premiere.project.deleteSequence;
    const res = hostCall(ctx, 'deleteSequences', { ids: ['seq-0'] });
    assert.deepStrictEqual(res.result.left, [{ id: 'seq-0' }]);
    assert.match(res.result.error, /unavailable/);
    assert.strictEqual(premiere.sequences.length, 1);
});

test('the fake premiere saves a project file the mask graft can read, and imports a sequence back with its nest', () => {
    const { projectXml, readProjectFile } = require('./hostMock');
    const projectPath = path.join(os.tmpdir(), 'chopFake.prproj');
    const { premiere, ctx, imp } = importMatte({ projectPath });
    hostCall(ctx, 'applyMatteEffects', { sequenceId: imp.sequenceId, nests: [{ name: 'hero (chop comp)', id: imp.nests[0].id, effects: compEffects }] });
    const saved = hostCall(ctx, 'saveProject', {});
    assert.strictEqual(saved.result.path, projectPath);
    const text = require('zlib').gunzipSync(fs.readFileSync(projectPath)).toString('utf8');
    assert.ok(text.startsWith('<?xml version="1.0" encoding="UTF-8" ?>\n<PremiereData Version="3">'));
    assert.strictEqual(text.length, projectXml(premiere.sequences).length);
    assert.ok(text.indexOf('<Name>hero &amp; co</Name>') > 0);
    const read = readProjectFile(projectPath);
    assert.deepStrictEqual(read[imp.sequenceId].nests, [imp.nests[0].id]);
    const comp = read[imp.nests[0].id];
    assert.strictEqual(comp.name, 'hero (chop comp)');
    assert.deepStrictEqual(comp.tracks[1].map((c) => c.effects.map((e) => e.matchName)),
        [['AE.ADBE Mosaic', 'AE.ADBE Track Matte Key'], ['AE.ADBE Gaussian Blur', 'AE.ADBE Track Matte Key']]);
    assert.deepStrictEqual(comp.tracks[1].map((c) => [c.start, c.end]), [[1, 2], [3, 4]]);
    assert.strictEqual(read[imp.sequenceId].tracks[0][0].in, 1);
    // once gone, both come back from the file when the main one is asked for
    hostCall(ctx, 'deleteSequences', { ids: [imp.sequenceId, imp.nests[0].id] });
    assert.strictEqual(premiere.sequences.length, 1);
    const back = hostCall(ctx, 'importProjectSequences', { path: projectPath, sequenceIds: [imp.sequenceId], open: imp.sequenceId });
    assert.deepStrictEqual(back.result.sequences.map((s) => s.name), ['hero (chop comp)', 'hero & co']);
    assert.strictEqual(premiere.project.activeSequence.name, 'hero & co');
    const nest = premiere.sequences.find((s) => s.name === 'hero (chop comp)');
    assert.strictEqual(nest.videoTracks.numTracks, 3);
    assert.strictEqual(premiere.project.activeSequence.videoTracks[0].clips[0].nestId, nest.sequenceID);
});

test('project save, sequence import and delete are reachable through the bridge whitelist', () => {
    const { allowedFns } = require('../panel/js/bridgeServer');
    assert.ok(allowedFns.indexOf('saveProject') >= 0);
    assert.ok(allowedFns.indexOf('importProjectSequences') >= 0);
    assert.ok(allowedFns.indexOf('deleteSequences') >= 0);
});

test('splitCommand handles launchers and paths with spaces', () => {
    assert.deepStrictEqual(splitCommand('python'), ['python', []]);
    assert.deepStrictEqual(splitCommand('py -3'), ['py', ['-3']]);
    assert.deepStrictEqual(splitCommand('C:\\Program Files\\Python312\\python.exe'), ['C:\\Program Files\\Python312\\python.exe', []]);
});

function post(port, body, headers) {
    return new Promise((resolve, reject) => {
        const data = Buffer.from(typeof body === 'string' ? body : JSON.stringify(body));
        const req = http.request({ host: '127.0.0.1', port, path: '/call', method: 'POST', headers: Object.assign({ 'Content-Length': data.length }, headers) }, (res) => {
            let raw = '';
            res.on('data', (c) => { raw += c; });
            res.on('end', () => resolve({ status: res.statusCode, body: raw ? JSON.parse(raw) : null }));
        });
        req.on('error', reject);
        req.end(data);
    });
}

test('bridge enforces token, origin, content type and whitelist', async () => {
    const { ctx } = setup();
    const bridgeFile = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'chopBridge')), 'bridge.json');
    const bridge = createBridge({ bridgeFile, evalHost: (fn, args) => hostCall(ctx, fn, args) });
    const port = await bridge.start();
    try {
        const info = JSON.parse(fs.readFileSync(bridgeFile, 'utf8'));
        assert.strictEqual(info.port, port);
        const json = { 'Content-Type': 'application/json' };
        const good = Object.assign({ 'X-Flash-Token': info.token }, json);

        assert.strictEqual((await post(port, { fn: 'ping' }, json)).status, 401);
        assert.strictEqual((await post(port, { fn: 'ping' }, Object.assign({ Origin: 'https://evil.example' }, good))).status, 403);
        assert.strictEqual((await post(port, { fn: 'ping' }, { 'X-Flash-Token': info.token, 'Content-Type': 'text/plain' })).status, 415);
        assert.strictEqual((await post(port, { fn: 'eval', args: {} }, good)).status, 400);
        assert.strictEqual((await post(port, '{nope', good)).status, 400);

        const ok = await post(port, { fn: 'getContext' }, good);
        assert.strictEqual(ok.status, 200);
        assert.strictEqual(ok.body.ok, true);
        assert.strictEqual(ok.body.result.media[0].path, rushPath);
    } finally {
        bridge.stop();
    }
    assert.strictEqual(fs.existsSync(bridgeFile), false);
});

test('reloadHost re-evaluates host.jsx through the panel instead of calling the host', async () => {
    const bridgeFile = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'chopBridge')), 'bridge.json');
    let reloads = 0;
    let hostCalls = 0;
    const bridge = createBridge({
        bridgeFile,
        evalHost: () => { hostCalls += 1; return { ok: true }; },
        reloadHost: () => { reloads += 1; return Promise.resolve('ok'); }
    });
    const port = await bridge.start();
    try {
        const info = JSON.parse(fs.readFileSync(bridgeFile, 'utf8'));
        const res = await post(port, { fn: 'reloadHost' }, { 'X-Flash-Token': info.token, 'Content-Type': 'application/json' });
        assert.strictEqual(res.status, 200);
        assert.strictEqual(res.body.ok, true);
        assert.strictEqual(reloads, 1);
        assert.strictEqual(hostCalls, 0);
    } finally {
        bridge.stop();
    }
});

test('reloadPanel answers, then reloads the panel itself so a new whitelist loads without the user', async () => {
    const bridgeFile = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'chopBridge')), 'bridge.json');
    let reloads = 0;
    let hostCalls = 0;
    const bridge = createBridge({ bridgeFile, evalHost: () => { hostCalls += 1; return { ok: true }; }, reloadPanel: () => { reloads += 1; } });
    const port = await bridge.start();
    try {
        const info = JSON.parse(fs.readFileSync(bridgeFile, 'utf8'));
        const res = await post(port, { fn: 'reloadPanel' }, { 'X-Flash-Token': info.token, 'Content-Type': 'application/json' });
        assert.strictEqual(res.status, 200);
        assert.deepStrictEqual(res.body, { ok: true, result: { reloading: true } });
        await new Promise((r) => setTimeout(r, 300));
        assert.strictEqual(reloads, 1);
        assert.strictEqual(hostCalls, 0);
    } finally {
        bridge.stop();
    }
});

test('every panel script parses', () => {
    const vm = require('vm');
    const dir = path.join(__dirname, '..', 'panel', 'js');
    for (const name of fs.readdirSync(dir)) {
        const src = fs.readFileSync(path.join(dir, name), 'utf8');
        assert.doesNotThrow(() => new vm.Script(src, { filename: name }), name);
    }
});

test('every element the panel script looks up is in the page, and no api key is asked anywhere', () => {
    const panelDir = path.join(__dirname, '..', 'panel');
    const html = fs.readFileSync(path.join(panelDir, 'index.html'), 'utf8');
    const main = fs.readFileSync(path.join(panelDir, 'js', 'main.js'), 'utf8');
    const ids = new Set(Array.from(html.matchAll(/\bid="([^"]+)"/g), (m) => m[1]));
    const used = new Set(Array.from(main.matchAll(/\$\('([A-Za-z]+)'\)/g), (m) => m[1]));
    const looped = main.match(/settingIds = \[([^\]]+)\]/)[1].match(/'([A-Za-z]+)'/g).map((s) => s.slice(1, -1));
    for (const id of [...used, ...looped]) { assert.ok(ids.has(id), `#${id} missing from index.html`); }
    for (const href of html.matchAll(/href="#([^"]+)"/g)) { assert.ok(ids.has(href[1]), `icon #${href[1]} missing from the sprite`); }
    assert.doesNotMatch(html + main, /apiKey|api key|anthropic|modelInput/i);
});

test('a nested clip resolves to the file inside, but markers are refused', () => {
    const inner = makeInnerSequence('node-7', 'unboxing', [makeClip(rushPath, 0, 0, 1569)]);
    const premiere = makePremiere({ clips: [makeNestClip('node-7', 'unboxing [V]', 0, 0, 1569)], extraSequences: [inner] });
    const ctx = loadHost(premiere);
    const res = hostCall(ctx, 'getContext');
    assert.strictEqual(res.ok, true, res.error);
    const media = res.result.media;
    assert.strictEqual(media.length, 1);
    assert.strictEqual(media[0].path, rushPath);
    assert.strictEqual(media[0].nested, true);
    assert.strictEqual(media[0].inSequence, false);
    assert.strictEqual(media[0].onTimeline, true);
    assert.strictEqual(media[0].via, 'unboxing');

    const marked = hostCall(ctx, 'addMarkersForMedia', { mediaPath: rushPath, markers: [{ start: 1, end: 2 }] });
    assert.strictEqual(marked.ok, false);

    const diag = hostCall(ctx, 'diagnose');
    assert.strictEqual(diag.ok, true, diag.error);
    assert.strictEqual(diag.result.clips[0].item.isSequence, true);
    assert.strictEqual(diag.result.clips[0].item.resolved, rushPath);
});

function motionParams(clip) {
    const motion = clip.components[1];
    return { position: motion.properties[0], scale: motion.properties[1] };
}

test('applyMotion sets static framing and keyframed pushes in media time', () => {
    const clips = [makeMotionClip(0, 10, 12), makeMotionClip(2, 30, 34)];
    const premiere = makePremiere({ clips });
    const ctx = loadHost(premiere);
    const res = hostCall(ctx, 'applyMotion', {
        sequenceId: 'seq-0',
        clips: [
            { index: 0, from: { scalePct: 177.78, posX: 0.2, posY: 0.5 }, to: null },
            { index: 1, from: { scalePct: 100, posX: 0.5, posY: 0.5 }, to: { scalePct: 125, posX: 0.45, posY: 0.4 } }
        ]
    });
    assert.strictEqual(res.ok, true, res.error);
    assert.strictEqual(res.result.applied, 2);
    const a = motionParams(clips[0]);
    assert.strictEqual(a.scale.value, 177.78);
    assert.deepStrictEqual(Array.from(a.position.value), [0.2, 0.5]);
    const b = motionParams(clips[1]);
    assert.strictEqual(b.scale.timeVarying, true);
    assert.deepStrictEqual(b.scale.keys.map((k) => k.t), [30, b.scale.keys[1].t]);
    assert.ok(b.scale.keys[1].t > 33.9 && b.scale.keys[1].t < 34);
    assert.deepStrictEqual(b.scale.keys.map((k) => k.v), [100, 125]);
    assert.deepStrictEqual(Array.from(b.position.keys[1].v), [0.45, 0.4]);
});

test('applyMotion converts to pixels when premiere reports pixel positions', () => {
    const clips = [makeMotionClip(0, 0, 2, true)];
    const ctx = loadHost(makePremiere({ clips }));
    hostCall(ctx, 'applyMotion', { clips: [{ index: 0, from: { scalePct: 120, posX: 0.25, posY: 0.5 }, to: null }] });
    assert.deepStrictEqual(Array.from(motionParams(clips[0]).position.value), [270, 960]);
});

test('importCaptions builds a caption track from the srt', () => {
    const premiere = makePremiere({});
    const ctx = loadHost(premiere);
    const srtPath = path.join(os.tmpdir(), 'chopCaptions.srt');
    fs.writeFileSync(srtPath, '1\n00:00:00,000 --> 00:00:01,000\nhello\n');
    const res = hostCall(ctx, 'importCaptions', { srtPath, sequenceId: 'seq-0' });
    assert.strictEqual(res.ok, true, res.error);
    assert.deepStrictEqual(premiere.seq.captionTracks, [{ item: 'chopCaptions.srt', start: 0 }]);
});

test('autoReframe returns the new vertical sequence', () => {
    const premiere = makePremiere({});
    const ctx = loadHost(premiere);
    const res = hostCall(ctx, 'autoReframe', { numerator: 9, denominator: 16 });
    assert.strictEqual(res.ok, true, res.error);
    assert.strictEqual(res.result.sequenceId, 'seq-0-reframed');
    assert.match(res.result.sequenceName, /9x16/);
});

test('applyMotion reaches clips on upper tracks', () => {
    const premiere = makePremiere({});
    const upper = makeMotionClip(0, 0, 1);
    premiere.seq.videoTracks = { numTracks: 3, length: 3, 0: { clips: { numItems: 0, length: 0 } }, 1: { clips: { numItems: 0, length: 0 } }, 2: { clips: { numItems: 1, length: 1, 0: upper } } };
    const ctx = loadHost(premiere);
    const res = hostCall(ctx, 'applyMotion', { clips: [{ track: 2, index: 0, from: { scalePct: 150, posX: 0.5, posY: 0.5 }, to: null }] });
    assert.strictEqual(res.ok, true, res.error);
    assert.strictEqual(res.result.applied, 1);
    assert.strictEqual(motionParams(upper).scale.value, 150);
});

const matteXml = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4"><sequence id="sequence-1"><name>hero &amp; co</name><rate><timebase>30</timebase></rate><media><video>
<track><clipitem id="clipitem-v1"><name>n</name><start>0</start><end>30</end><in>30</in><out>60</out>
  <sequence id="sequence-2"><name>hero (chop comp)</name><rate><timebase>30</timebase></rate><media><video>
    <track><clipitem id="a"><name>src</name><start>0</start><end>300</end><in>0</in><out>300</out></clipitem><enabled>TRUE</enabled></track>
    <track>
      <clipitem id="b"><name>src</name><start>30</start><end>60</end><in>30</in><out>60</out></clipitem>
      <clipitem id="c"><name>src</name><start>90</start><end>120</end><in>90</in><out>120</out>
        <filter><effect><name>Gaussian Blur</name><effectid>Gaussian Blur</effectid></effect></filter></clipitem>
      <enabled>TRUE</enabled></track>
    <track><clipitem id="d"><name>m</name><start>30</start><end>60</end><in>0</in><out>30</out></clipitem>
      <clipitem id="e"><name>m</name><start>90</start><end>120</end><in>0</in><out>30</out></clipitem><enabled>FALSE</enabled></track>
  </video></media></sequence>
</clipitem><enabled>TRUE</enabled></track></video></media></sequence></xmeml>`;

function importMatte(opts) {
    const premiere = makePremiere(opts || {});
    const ctx = loadHost(premiere);
    const xmlPath = path.join(os.tmpdir(), 'chopMatte.xml');
    fs.writeFileSync(xmlPath, matteXml);
    const imp = hostCall(ctx, 'importSequenceXml', { xmlPath, sequenceName: 'hero & co', nestNames: ['hero (chop comp)'] });
    assert.strictEqual(imp.ok, true, imp.error);
    return { premiere, ctx, imp: imp.result };
}

const compEffects = [
    { track: 1, index: 0, effect: 'Mosaic', params: [{ name: 'Horizontal Blocks', index: 0, value: 64 }] },
    { track: 1, index: 0, effect: 'Track Matte Key', params: [{ name: 'Matte', index: 0, matteTrack: 3 }] },
    { track: 1, index: 1, effect: 'Gaussian Blur', params: [{ name: 'Blurriness', index: 0, value: 45 },
        { name: 'Repeat Edge Pixels', index: 2, value: true }] },
    { track: 1, index: 1, effect: 'Track Matte Key', params: [{ name: 'Matte', index: 0, matteTrack: 3 }] }
];

test('importSequenceXml picks the main sequence among the nests it creates', () => {
    const { premiere, imp } = importMatte();
    assert.strictEqual(imp.sequenceName, 'hero & co');
    assert.deepStrictEqual(imp.nests.map((n) => n.name), ['hero (chop comp)']);
    assert.strictEqual(premiere.project.activeSequence.name, 'hero & co');
});

test('applyMatteEffects keys the nest through qe and reads the values back', () => {
    const { premiere, ctx, imp } = importMatte();
    const res = hostCall(ctx, 'applyMatteEffects', {
        sequenceId: imp.sequenceId, nests: [{ name: 'hero (chop comp)', id: imp.nests[0].id, effects: compEffects }]
    });
    assert.strictEqual(res.ok, true, res.error);
    const nest = res.result.nests[0];
    assert.strictEqual(res.result.ok, true, JSON.stringify(nest.issues));
    assert.strictEqual(nest.applied, 4);
    // probe clamped 99 to the 3 tracks: 0 = none, 3 = video 3
    assert.strictEqual(nest.matteProbe, 3);
    assert.strictEqual(nest.matteOffset, 0);
    assert.deepStrictEqual(nest.tracks, ['Video 1', 'Video 2', 'Video 3']);
    // gaussian blur came from the xml, mosaic and both keys through qe, landing on the right clips past the gaps
    assert.deepStrictEqual(premiere.qe.added.map((a) => [a.track, a.index, a.effect]),
        [[1, 0, 'Mosaic'], [1, 0, 'Track Matte Key'], [1, 1, 'Track Matte Key']]);
    const comp = premiere.sequences.find((s) => s.name === 'hero (chop comp)');
    const blurred = comp.videoTracks[1].clips[1];
    const names = [];
    for (let i = 0; i < blurred.components.numItems; i++) { names.push(blurred.components[i].displayName); }
    assert.deepStrictEqual(names, ['Opacity', 'Motion', 'Gaussian Blur', 'Track Matte Key']);
    assert.strictEqual(blurred.components[2].properties[0].value, 45);
    assert.strictEqual(blurred.components[2].properties[2].value, true);
    assert.strictEqual(blurred.components[3].properties[0].value, 3);
    // back on the edit when done
    assert.strictEqual(premiere.project.activeSequence.name, 'hero & co');
});

test('applyMatteEffects says so when the matte popup does not move', () => {
    const { ctx, imp } = importMatte({ refuseMatte: true });
    const res = hostCall(ctx, 'applyMatteEffects', {
        sequenceId: imp.sequenceId, nests: [{ name: 'hero (chop comp)', id: imp.nests[0].id, effects: compEffects }]
    });
    assert.strictEqual(res.ok, true, res.error);
    assert.strictEqual(res.result.ok, false);
    assert.match(res.result.nests[0].issues.join(' '), /stayed at 0 instead of 3/);
});

test('applyMatteEffects honours a configured offset and flags a missing nest', () => {
    const { ctx, imp } = importMatte();
    const res = hostCall(ctx, 'applyMatteEffects', {
        sequenceId: imp.sequenceId, matteOffset: 0,
        nests: [{ name: 'hero (chop comp)', id: imp.nests[0].id, effects: compEffects.slice(0, 2) },
            { name: 'hero (chop subject)', id: null, effects: [] }]
    });
    assert.strictEqual(res.result.nests[0].matteProbe, null);
    assert.strictEqual(res.result.ok, false);
    assert.match(res.result.nests[1].issues[0], /not found/);
});

test('diagnoseEffects lists components, values and track names', () => {
    const { ctx, imp } = importMatte();
    hostCall(ctx, 'applyMatteEffects', { sequenceId: imp.sequenceId, nests: [{ name: 'hero (chop comp)', id: imp.nests[0].id, effects: compEffects }] });
    const res = hostCall(ctx, 'diagnoseEffects', { sequenceId: imp.nests[0].id });
    assert.strictEqual(res.ok, true, res.error);
    const keyed = res.result.clips.find((c) => c.track === 'V2' && c.index === 1);
    const tmk = keyed.components.find((c) => c.name === 'Track Matte Key');
    assert.deepStrictEqual(tmk.properties[0], { name: 'Matte:', value: 3 });
    assert.strictEqual(res.result.qe.trackMatteKey, true);
});

function effectWorld() {
    const premiere = makePremiere({});
    const a = makeMotionClip(0, 10, 14);
    const b = makeMotionClip(4, 0, 2);
    const voice = makeMotionClip(0, 0, 6);
    voice.components = { numItems: 0, length: 0 };
    premiere.seq.videoTracks = { numTracks: 1, length: 1, 0: { clips: { numItems: 2, length: 2, 0: a, 1: b } } };
    premiere.seq.audioTracks = { numTracks: 1, length: 1, 0: { clips: { numItems: 1, length: 1, 0: voice } } };
    return { premiere, ctx: loadHost(premiere), a, b, voice };
}

function names(clip) {
    const out = [];
    for (let i = 0; i < clip.components.numItems; i++) { out.push(clip.components[i].displayName); }
    return out;
}

test('getClips resolves the selection, a track index or a time', () => {
    const { ctx, b, voice } = effectWorld();
    b.selected = true;
    voice.selected = true;
    let res = hostCall(ctx, 'getClips', { targets: 'selection' });
    assert.strictEqual(res.ok, true, res.error);
    assert.deepStrictEqual(res.result.clips.map((c) => c.label), ['V1 #1', 'A1 #0']);
    assert.strictEqual(res.result.clips[0].inPoint, 0);
    assert.ok(Math.abs(res.result.fps - 29.97) < 0.01);
    res = hostCall(ctx, 'getClips', { targets: [{ track: 'V1', at: 4.5 }, { track: 'V1', index: 0 }] });
    assert.deepStrictEqual(res.result.clips.map((c) => c.label), ['V1 #1', 'V1 #0']);
    assert.strictEqual(hostCall(ctx, 'getClips', { targets: [{ track: 'V3', index: 0 }] }).ok, false);
});

test('applyEffects replays a preset plan: qe add, statics, colors, keys', () => {
    const { premiere, ctx, a } = effectWorld();
    const target = hostCall(ctx, 'getClips', { targets: [{ track: 'V1', index: 0 }] }).result.clips[0];
    const plan = [
        { effect: 'Transform', matchName: 'AE.ADBE Geometry2', params: [
            { name: 'Position', index: 1, kind: 'point', value: [0.5, 0.45] },
            { name: 'Scale Height', index: 3, kind: 'float', keys: [[10, 75], [10.0667, 110], [10.1167, 100]] },
            { name: 'Opacity', index: 5, kind: 'float', value: 80 }] },
        { effect: 'Drop Shadow', matchName: 'AE.ADBE Drop Shadow', params: [
            { name: 'Shadow Color', index: 0, kind: 'color', value: [255, 255, 0, 0] }], skipped: [] }
    ];
    const res = hostCall(ctx, 'applyEffects', { targets: [target], plans: [plan], mode: 'add' });
    assert.strictEqual(res.ok, true, res.error);
    assert.strictEqual(res.result.ok, true, JSON.stringify(res.result.clips[0].issues));
    assert.deepStrictEqual(names(a), ['Opacity', 'Motion', 'Transform', 'Drop Shadow']);
    const transform = a.components[2];
    // "Scale Height" shows as "Scale" with uniform scale on: matched by position
    assert.deepStrictEqual(transform.properties[3].keys.map((k) => [k.t, k.v]), [[10, 75], [10.0667, 110], [10.1167, 100]]);
    assert.strictEqual(transform.properties[3].timeVarying, true);
    // opacity found by name although the preset had it elsewhere
    assert.strictEqual(transform.properties[8].value, 80);
    assert.deepStrictEqual(Array.from(transform.properties[1].value), [0.5, 0.45]);
    assert.deepStrictEqual(Array.from(a.components[3].properties[0].value), [255, 255, 0, 0]);
    assert.deepStrictEqual(res.result.clips[0].effects.map((e) => [e.effect, e.params, e.keys]), [['Transform', 2, 3], ['Drop Shadow', 1, 0]]);
    assert.deepStrictEqual(premiere.qe.added.map((x) => x.effect), ['Transform', 'Drop Shadow']);
});

test('applyEffects update mode edits motion in place and reports what it could not find', () => {
    const { ctx, a, voice } = effectWorld();
    const targets = hostCall(ctx, 'getClips', { targets: [{ track: 'V1', index: 0 }, { track: 'A1', index: 0 }] }).result.clips;
    const res = hostCall(ctx, 'applyEffects', { targets: targets, mode: 'update', plans: [
        [{ effect: 'Motion', intrinsic: true, params: [{ name: 'Scale', kind: 'value', value: 120 }] },
            { effect: 'Gaussian Blur', params: [{ name: 'Blurriness', kind: 'value', value: 10 }] }],
        [{ effect: 'DeNoise', audio: true, params: [{ name: 'Amount', kind: 'value', value: 0.6 }] }]
    ] });
    assert.strictEqual(res.ok, true, res.error);
    assert.strictEqual(a.components[1].properties[1].value, 120);
    assert.match(res.result.clips[0].issues.join(' '), /no Gaussian Blur to update/);
    assert.match(res.result.clips[1].issues.join(' '), /no DeNoise to update/);
    const added = hostCall(ctx, 'applyEffects', { targets: [targets[1]], mode: 'add', plans: [
        [{ effect: 'DeNoise', audio: true, params: [{ name: 'Amount', kind: 'value', value: 0.6 }] }]] });
    assert.strictEqual(added.result.ok, true, JSON.stringify(added.result));
    assert.deepStrictEqual(names(voice), ['DeNoise']);
    assert.strictEqual(voice.components[0].properties[0].value, 0.6);
});

test('clipEffects lists every parameter with its animation state', () => {
    const { ctx } = effectWorld();
    const target = hostCall(ctx, 'getClips', { targets: [{ track: 'V1', index: 0 }] }).result.clips[0];
    hostCall(ctx, 'applyEffects', { targets: [target], mode: 'add', plans: [[{ effect: 'Transform', params: [
        { name: 'Rotation', index: 7, kind: 'float', keys: [[10, 0], [11, 90]] }] }]] });
    const res = hostCall(ctx, 'clipEffects', { targets: [{ track: 'V1', index: 0 }] });
    assert.strictEqual(res.ok, true, res.error);
    const transform = res.result.clips[0].components.find((c) => c.name === 'Transform');
    const rotation = transform.properties.find((p) => p.name === 'Rotation');
    assert.deepStrictEqual([rotation.animated, rotation.keys, rotation.firstKey, rotation.lastKey], [true, 2, 10, 11]);
});

test('addMogrt places a template, swaps its text and keeps the style json', () => {
    const premiere = makePremiere({});
    premiere.seq.videoTracks = { numTracks: 2, length: 2, 0: { clips: { numItems: 0, length: 0 } }, 1: { clips: { numItems: 0, length: 0 } } };
    const ctx = loadHost(premiere);
    const mogrtPath = path.join(os.tmpdir(), 'Lower Third.mogrt');
    fs.writeFileSync(mogrtPath, 'zip');
    const res = hostCall(ctx, 'addMogrt', { path: mogrtPath, at: 2, duration: 1.5, track: 1, values: { 'Main Text': 'KEYBOARD "75"', Nope: 1 } });
    assert.strictEqual(res.ok, true, res.error);
    assert.deepStrictEqual([res.result.track, res.result.index], ['V2', 0]);
    assert.match(res.result.issues[0], /no control named Nope/);
    const clip = premiere.seq.videoTracks[1].clips[0];
    assert.strictEqual(clip.end.seconds, 3.5);
    const text = clip.getMGTComponent().properties[0].value;
    assert.strictEqual(JSON.parse(text).textEditValue, 'KEYBOARD "75"');
    assert.deepStrictEqual(JSON.parse(text).fontEditValue, ['Montserrat-Black']);
    // later edits go through the same text swap
    const target = hostCall(ctx, 'getClips', { targets: [{ track: 'V2', index: 0 }] }).result.clips[0];
    assert.strictEqual(target.mgt, true);
    const upd = hostCall(ctx, 'applyEffects', { targets: [target], mode: 'update', plans: [[{ effect: 'mogrt', params: [
        { name: 'Main Text', kind: 'value', value: 'NEW' }, { name: 'Fill Color', kind: 'color', value: [255, 255, 0, 0] }] }]] });
    assert.strictEqual(upd.result.ok, true, JSON.stringify(upd.result));
    assert.strictEqual(JSON.parse(clip.getMGTComponent().properties[0].value).textEditValue, 'NEW');
    const listed = hostCall(ctx, 'clipEffects', { targets: [{ track: 'V2', index: 0 }] }).result.clips[0];
    assert.deepStrictEqual(listed.mogrtControls.map((c) => c.name), ['Main Text', 'Fill Color']);
});

test('duckAudio keys the rush level down under the voice, relative to its own level', () => {
    const premiere = makePremiere({});
    const rush = makeClip('C:/rush.mp4', 0, 10, 20);
    rush.components = { numItems: 1, length: 1, 0: { displayName: 'Volume', matchName: 'audio volume', properties: {
        numItems: 2, length: 2, 0: { displayName: 'Bypass', getValue() { return false; } }, 1: null } } };
    const level = { displayName: 'Level', value: 0.17782794, keys: [], timeVarying: false,
        getValue() { return this.value; }, isTimeVarying() { return this.timeVarying; },
        setTimeVarying(v) { this.timeVarying = v; if (!v) { this.keys = []; } },
        addKey(t) { this.keys.push({ t: t.seconds }); }, setValueAtKey(t, v) { this.keys.find((k) => k.t === t.seconds).v = v; } };
    rush.components[0].properties[1] = level;
    const later = makeClip('C:/rush.mp4', 20, 40, 45);
    premiere.seq.audioTracks = { numTracks: 1, length: 1, 0: { clips: { numItems: 2, length: 2, 0: rush, 1: later } } };
    const ctx = loadHost(premiere);
    const res = hostCall(ctx, 'duckAudio', { tracks: 1, ranges: [[2, 4], [6, 8]], db: -20, ramp: 0.5 });
    assert.strictEqual(res.ok, true, res.error);
    assert.deepStrictEqual([res.result.clips, res.result.issues], [1, []]);
    // media time = in point + sequence offset; level drops to a tenth inside the voice
    const at = (t) => level.keys.find((k) => Math.abs(k.t - t) < 1e-9).v;
    assert.ok(Math.abs(at(11.5) - 0.17782794) < 1e-9);
    assert.ok(Math.abs(at(12) - 0.017782794) < 1e-9 && Math.abs(at(14) - 0.017782794) < 1e-9);
    assert.ok(Math.abs(at(14.5) - 0.17782794) < 1e-9);
    assert.deepStrictEqual(level.keys.map((k) => k.t), [10, 11.5, 12, 14, 14.5, 15.5, 16, 18, 18.5, 20]);
});

function volumeClip(path, start, inPoint, outPoint) {
    const clip = makeClip(path, start, inPoint, outPoint);
    const level = { displayName: 'Level', value: 0.17782794, keys: [], timeVarying: false,
        getValue() { return this.value; }, setValue(v) { this.value = v; }, isTimeVarying() { return this.timeVarying; },
        setTimeVarying(v) { this.timeVarying = v; if (!v) { this.keys = []; } },
        addKey(t) { this.keys.push({ t: t.seconds }); }, setValueAtKey(t, v) { this.keys.find((k) => k.t === t.seconds).v = v; } };
    clip.components = { numItems: 1, length: 1, 0: { displayName: 'Volume', matchName: 'audio volume', properties: {
        numItems: 2, length: 2, 0: { displayName: 'Bypass', getValue() { return false; } }, 1: level } } };
    return { clip, level };
}

test('duckAudio takes a track list and a level per range, setVolume a fixed gain', () => {
    const premiere = makePremiere({});
    const a = volumeClip('C:/rush.mp4', 0, 0, 10);
    const b = volumeClip('C:/cam2.mp4', 0, 0, 10);
    const sfx = volumeClip('C:/whoosh.wav', 2, 0, 0.5);
    const track = (clip) => ({ clips: { numItems: 1, length: 1, 0: clip } });
    premiere.seq.audioTracks = { numTracks: 3, length: 3, 0: track(a.clip), 1: track(b.clip), 2: track(sfx.clip) };
    const ctx = loadHost(premiere);
    const res = hostCall(ctx, 'duckAudio', { trackList: [1], db: -14, ramp: 0.5, ranges: [[2, 3, -20], [6, 7]] });
    assert.strictEqual(res.ok, true, res.error);
    assert.deepStrictEqual([res.result.clips, a.level.keys.length], [1, 0]);
    const at = (t) => b.level.keys.find((k) => Math.abs(k.t - t) < 1e-9).v;
    assert.ok(Math.abs(at(2) - 0.017782794) < 1e-9 && Math.abs(at(3) - 0.017782794) < 1e-9, 'own -20 dB');
    assert.ok(Math.abs(at(6) - 0.17782794 * Math.pow(10, -14 / 20)) < 1e-9, 'the call level');
    const vol = hostCall(ctx, 'setVolume', { clips: [{ track: 2, index: 0, db: -6 }, { track: 5, index: 0, db: 1 }] });
    assert.deepStrictEqual([vol.result.clips, vol.result.issues], [1, ['no clip A6 #0']]);
    assert.ok(Math.abs(sfx.level.value - 0.17782794 * Math.pow(10, -6 / 20)) < 1e-9);
});

test('applyMotion keys a label on every frame of a track', () => {
    const clips = [makeMotionClip(2, 0, 1)];
    const ctx = loadHost(makePremiere({ clips }));
    const res = hostCall(ctx, 'applyMotion', { clips: [{ index: 0, from: { scalePct: 100, posX: 0.4, posY: 0.3 }, to: null,
        keys: [[0, 100, 0.4, 0.3], [0.0333, 100, 0.41, 0.3], [0.0667, 104, 0.42, 0.31]] }] });
    assert.strictEqual(res.ok, true, res.error);
    const { position, scale } = motionParams(clips[0]);
    assert.deepStrictEqual(position.keys.map((k) => [k.t, Array.from(k.v)]), [[0, [0.4, 0.3]], [0.0333, [0.41, 0.3]], [0.0667, [0.42, 0.31]]]);
    assert.deepStrictEqual(scale.keys.map((k) => k.v), [100, 100, 104]);
});

test('applyEffects turns normalized corner pins into layer pixels when premiere stores pixels', () => {
    const { ctx, a } = effectWorld();
    const target = hostCall(ctx, 'getClips', { targets: [{ track: 'V1', index: 0 }] }).result.clips[0];
    const res = hostCall(ctx, 'applyEffects', { targets: [target], mode: 'add', plans: [[{ effect: 'Corner Pin', params: [
        { name: 'Upper Left', index: 0, kind: 'point', keys: [[10, [0.5, 0.25]], [10.5, [0.55, 0.25]]], layerSize: [320, 180] },
        { name: 'Lower Right', index: 3, kind: 'point', value: [0.9, 0.9], layerSize: [320, 180] }] }]] });
    assert.strictEqual(res.result.ok, true, JSON.stringify(res.result));
    const pin = a.components[2];
    assert.deepStrictEqual(pin.properties[0].keys.map((k) => Array.from(k.v)), [[160, 45], [176, 45]]);
    assert.deepStrictEqual(Array.from(pin.properties[3].value), [288, 162]);
});

// the first interpreter that has the sidecar's packages, like the one the panel is set to
function sidecarPython() {
    const { execFileSync } = require('child_process');
    const candidates = process.env.CHOPSHOP_PYTHON ? [splitCommand(process.env.CHOPSHOP_PYTHON)]
        : (process.platform === 'win32' ? [['py', ['-3']], ['python', []]] : [['python3', []]]);
    for (const [cmd, args] of candidates) {
        try {
            execFileSync(cmd, args.concat(['-c', 'import av, numpy, PIL']), { stdio: 'ignore' });
            return [cmd, args];
        } catch (e) { /* next one */ }
    }
    return candidates[0];
}

test('self-test runs end to end on the simulated premiere with real assets from the sidecar', async () => {
    const { execFileSync } = require('child_process');
    const { runSelfTest } = require('../panel/js/selfTest');
    const premiere = makePremiere({});
    const ctx = loadHost(premiere);
    const out = fs.mkdtempSync(path.join(os.tmpdir(), 'chopSelfTest'));
    const report = await runSelfTest({
        host: async (fn, args) => { const r = hostCall(ctx, fn, args); if (!r.ok) { throw new Error(r.error); } return r.result; },
        makeAssets: async () => {
            const [cmd, args] = sidecarPython();
            return JSON.parse(execFileSync(cmd, args.concat([path.join(__dirname, '..', 'sidecar', 'selftest.py'), out])).toString());
        }
    });
    assert.deepStrictEqual(report.checks.filter((c) => !c.ok), [], JSON.stringify(report.checks, null, 1));
    assert.strictEqual(report.passed, 9);
    assert.strictEqual(report.raw.matte.nests[0].matteProbe, 3);
});

test('template text with dollar signs stays literal', () => {
    const premiere = makePremiere({});
    premiere.seq.videoTracks = { numTracks: 1, length: 1, 0: { clips: { numItems: 0, length: 0 } } };
    const ctx = loadHost(premiere);
    const mogrtPath = path.join(os.tmpdir(), 'Price.mogrt');
    fs.writeFileSync(mogrtPath, 'zip');
    hostCall(ctx, 'addMogrt', { path: mogrtPath, at: 0, duration: 1, track: 0, values: { 'Main Text': "Win $$$ now, it$'s $& more" } });
    const text = premiere.seq.videoTracks[0].clips[0].getMGTComponent().properties[0].value;
    assert.strictEqual(JSON.parse(text).textEditValue, "Win $$$ now, it$'s $& more");
});

test('getRange reports the in and out points and the selected video clips of the active sequence', () => {
    const { ctx, premiere } = setup();
    // nothing set, nothing selected: the sidecar decides from the sketch itself
    let res = hostCall(ctx, 'getRange', {});
    assert.strictEqual(res.ok, true, res.error);
    assert.strictEqual(res.result.sequenceId, 'seq-0');
    assert.strictEqual(res.result.in, null);
    assert.strictEqual(res.result.out, null);
    assert.deepStrictEqual(res.result.clips, []);
    assert.ok(Math.abs(res.result.fps - 29.97) < 0.01);
    premiere.seq.inPoint = 12;
    premiere.seq.outPoint = 14.5;
    premiere.seq.videoTracks[0].clips[0].selected = true;
    res = hostCall(ctx, 'getRange', {});
    assert.strictEqual(res.result.in, 12);
    assert.strictEqual(res.result.out, 14.5);
    assert.strictEqual(res.result.clips.length, 1);
    const clip = res.result.clips[0];
    assert.strictEqual(clip.track, 0);
    assert.strictEqual(clip.index, 0);
    assert.strictEqual(clip.start, 10);
    assert.strictEqual(clip.inPoint, 5);
    assert.strictEqual(clip.speed, 1);
    assert.strictEqual(clip.mediaPath, rushPath);
    assert.strictEqual(hostCall(ctx, 'getRange', { sequenceId: 'nope' }).ok, false);
});

test('getRange is reachable through the bridge whitelist', () => {
    const { allowedFns } = require('../panel/js/bridgeServer');
    assert.ok(allowedFns.indexOf('getRange') >= 0);
});

test('the fake premiere keeps a pen mask and the media path of a clip across a save and an import', () => {
    const { projectXml, readProjectFile } = require('./hostMock');
    const projectPath = path.join(os.tmpdir(), 'chopMask.prproj');
    const keyframes = '127008000000,MmNpbgIAAAAAAAAABAAAAAEAAAC8u7s+QLIuP6kUvT7Bgy4/z2K6PsDgLj8BAAAAAAAAAM3Mfj/PlQc+zcx+P8+VBz7NzH4/z5UHPgEAAAAA'
        + 'AAAAmpl/P/VJfz+amX8/9Ul/P5qZfz/1SX8/AQAAAAAAAABVVdU+6ZN+P1VV1T7pk34/VVXVPumTfj8BAAAA;';
    const clip = makeClip(rushPath, 10, 5, 65);
    clip.components = { numItems: 1, length: 1, 0: { displayName: 'Mosaic', matchName: 'AE.ADBE Mosaic', properties: { numItems: 0, length: 0 },
        masks: [{ instance: '1', keyframes }] } };
    const premiere = makePremiere({ clips: [clip], projectPath });
    const ctx = loadHost(premiere);
    const saved = hostCall(ctx, 'saveProject', {});
    assert.strictEqual(saved.ok, true, saved.error);
    const read = readProjectFile(projectPath);
    const main = read['seq-0'].tracks[0][0];
    assert.strictEqual(main.mediaPath, rushPath);
    assert.deepStrictEqual(main.effects.map((e) => [e.matchName, e.masks]), [['AE.ADBE Mosaic', [{ instance: '1', keyframes }]]]);
    const text = require('zlib').gunzipSync(fs.readFileSync(projectPath)).toString('utf8');
    assert.ok(text.indexOf('<MatchName>AE.ADBE AEMask</MatchName>') > 0 && text.indexOf('<Name>Mask Path</Name>') > 0);
    assert.strictEqual(text.length, projectXml(premiere.sequences).length);
    hostCall(ctx, 'deleteSequences', { ids: ['seq-0'] });
    const back = hostCall(ctx, 'importProjectSequences', { path: projectPath, sequenceIds: ['seq-0'], open: 'seq-0' });
    assert.strictEqual(back.ok, true, back.error);
    const imported = premiere.project.activeSequence.videoTracks[0].clips[0];
    assert.strictEqual(imported.projectItem.getMediaPath(), rushPath);
    const comps = [];
    for (let i = 0; i < imported.components.numItems; i++) { comps.push(imported.components[i]); }
    assert.deepStrictEqual(comps.find((c) => c.matchName === 'AE.ADBE Mosaic').masks, [{ instance: '1', keyframes }]);
});

test('the PY light checks python as the panel opens, without waiting on it', () => {
    const panelDir = path.join(__dirname, '..', 'panel');
    const main = fs.readFileSync(path.join(panelDir, 'js', 'main.js'), 'utf8');
    const css = fs.readFileSync(path.join(panelDir, 'css', 'panel.css'), 'utf8');
    const init = main.slice(main.indexOf('async function init()'));
    assert.match(init, /\n\s+checkPython\(true\);/);
    assert.doesNotMatch(init, /await checkPython/);
    assert.match(main, /setDot\('statusPython', 'busy'/);
    assert.match(css, /\.dot\.busy\s*\{/);
});
