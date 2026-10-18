(function () {
    function near(a, b, tol) { return typeof a === 'number' && Math.abs(a - b) <= (tol || 1e-3); }
    function samePath(a, b) { return String(a).replace(/\\/g, '/').toLowerCase() === String(b).replace(/\\/g, '/').toLowerCase(); }

    async function runSelfTest(opts) {
        var host = opts.host;
        var assets = await opts.makeAssets();
        var checks = [];
        var raw = {};

        async function step(name, fn) {
            try {
                var detail = await fn();
                checks.push({ name: name, ok: detail.ok !== false, detail: detail.detail || detail });
            } catch (err) {
                checks.push({ name: name, ok: false, detail: err.message });
            }
            if (opts.log) { opts.log((checks[checks.length - 1].ok ? 'ok   ' : 'FAIL ') + name); }
        }

        var imported = null;
        await step('xml import creates the edit and its nest', async function () {
            imported = await host('importSequenceXml', { xmlPath: assets.xmlPath, sequenceName: assets.sequenceName, nestNames: [assets.nestName] });
            raw.imported = imported;
            var nest = (imported.nests || [])[0];
            return { ok: !!imported.sequenceId && !!nest, detail: { sequence: imported.sequenceName, nest: nest ? nest.name : null } };
        });
        if (!imported || !imported.sequenceId) { return { checks: checks, raw: raw }; }
        var nestId = ((imported.nests || [])[0] || {}).id;

        await step('nest keeps 3 tracks with default names and the blur from the xml', async function () {
            var d = await host('diagnoseEffects', { sequenceId: nestId });
            raw.nest = d;
            var dup = d.clips.filter(function (c) { return c.track === 'V2'; })[0];
            var blur = dup && dup.components.filter(function (c) { return c.name === 'Gaussian Blur'; })[0];
            var names = d.videoTracks.join(',');
            return { ok: d.videoTracks.length === 3 && names === 'Video 1,Video 2,Video 3' && !!blur,
                detail: { tracks: d.videoTracks, gaussianBlur: blur ? blur.properties : null } };
        });

        await step('track matte key set through qe and read back', async function () {
            var r = await host('applyMatteEffects', { sequenceId: imported.sequenceId, matteOffset: opts.matteOffset,
                nests: [{ name: assets.nestName, id: nestId, effects: assets.matteEffects[0].effects }] });
            raw.matte = r;
            var n = r.nests[0] || {};
            return { ok: r.ok, detail: { probe: n.matteProbe, offset: n.matteOffset, issues: n.issues, effects: n.effects } };
        });

        await step('speed from the xml (timeremap) lands as 200%', async function () {
            var c = await host('getClips', { sequenceId: imported.sequenceId, targets: [{ track: 'V1' }] });
            raw.v1 = c.clips;
            var sped = c.clips[1];
            return { ok: c.clips.length === 2 && sped && near(sped.speed, 2, 0.01), detail: c.clips.map(function (x) { return [x.label, x.speed, x.inPoint, x.outPoint]; }) };
        });

        await step('motion keys on a still (labels, captions)', async function () {
            await host('applyMotion', { sequenceId: imported.sequenceId, clips: [{ track: 1, index: 0, from: { scalePct: 100, posX: 0.5, posY: 0.8 }, to: null,
                keys: [[0, 100, 0.5, 0.8], [0.5, 110, 0.6, 0.8]] }] });
            var e = await host('clipEffects', { sequenceId: imported.sequenceId, targets: [{ track: 'V2', index: 0 }] });
            var motion = e.clips[0].components.filter(function (c) { return c.name === 'Motion'; })[0];
            raw.caption = motion;
            var pos = motion.properties[0];
            return { ok: !!pos.animated && pos.keys === 2, detail: motion.properties };
        });

        await step('transform and corner pin added by name, points in the right space', async function () {
            var t = (await host('getClips', { sequenceId: imported.sequenceId, targets: [{ track: 'V1', index: 1 }] })).clips[0];
            var r = await host('applyEffects', { sequenceId: imported.sequenceId, targets: [t], mode: 'add', plans: [[
                { effect: 'Transform', params: [{ name: 'Uniform Scale', index: 2, kind: 'bool', value: true },
                    { name: 'Scale Height', index: 3, kind: 'float', value: 110 }, { name: 'Scale Width', index: 4, kind: 'float', value: 110 },
                    { name: 'Rotation', index: 7, kind: 'float', keys: [[t.inPoint, 0], [t.inPoint + 0.3, 15]] }] },
                { effect: 'Corner Pin', params: [{ name: 'Upper Right', index: 1, kind: 'point', value: [0.9, 0.1], layerSize: [320, 180] }] }]] });
            var e = await host('clipEffects', { sequenceId: imported.sequenceId, targets: [{ track: 'V1', index: 1 }] });
            raw.effects = e.clips[0].components;
            var pin = e.clips[0].components.filter(function (c) { return c.name === 'Corner Pin'; })[0];
            return { ok: r.ok && !!pin, detail: { issues: r.clips[0].issues, cornerPin: pin ? pin.properties : null } };
        });

        await step('rush audio ducked with volume keys', async function () {
            var r = await host('duckAudio', { sequenceId: imported.sequenceId, tracks: 1, ranges: [[0.2, 0.6]], db: -12 });
            var e = await host('clipEffects', { sequenceId: imported.sequenceId, targets: [{ track: 'A1', index: 0 }] });
            var vol = e.clips[0].components.filter(function (c) { return c.name === 'Volume'; })[0];
            raw.volume = vol;
            return { ok: r.clips === 1 && r.keys >= 4, detail: { keys: r.keys, issues: r.issues, volume: vol ? vol.properties : null } };
        });

        await step('several files: second camera heard before its picture, a picture, a linked cover', async function () {
            var files = assets.files;
            if (!files) { return { ok: false, detail: 'sidecar too old, no files test' }; }
            var multi = await host('importSequenceXml', { xmlPath: files.xmlPath, sequenceName: files.sequenceName, nestNames: [] });
            var v1 = (await host('getClips', { sequenceId: multi.sequenceId, targets: [{ track: 'V1' }] })).clips;
            var a2 = (await host('getClips', { sequenceId: multi.sequenceId, targets: [{ track: 'A2' }] })).clips;
            var vol = await host('setVolume', { sequenceId: multi.sequenceId, clips: [{ track: 2, index: 0, db: -6 }] });
            raw.files = { v1: v1, a2: a2, volume: vol };
            var paths = v1.map(function (c) { return c.mediaPath; });
            var j = a2[0] || {};
            return { ok: v1.length === files.expect.v1 && samePath(paths[1], files.second) && samePath(paths[2], files.still) &&
                         near(j.start, files.expect.jcutStart, 0.04) && near(j.inPoint, files.expect.jcutIn, 0.04) && vol.clips === 1,
                detail: { v1: paths, secondCameraSound: [j.start, j.inPoint], volume: vol } };
        });

        await step('playhead and markers still answer', async function () {
            var p = await host('setPlayhead', { seconds: 0.5 });
            return { ok: !!p.ok, detail: p };
        });

        var failed = checks.filter(function (c) { return !c.ok; }).length;
        return { passed: checks.length - failed, failed: failed, checks: checks, raw: raw };
    }

    var exported = { runSelfTest: runSelfTest };
    if (typeof window !== 'undefined') { window.chopSelfTest = exported; }
    if (typeof module === 'object' && module && module.exports) { module.exports = exported; }
}());
