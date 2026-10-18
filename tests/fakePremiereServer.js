// runs the real bridge + real host.jsx against a fake premiere, for python integration tests
// usage: node fakePremiereServer.js <bridgeFile> <rushPath>
// stdin: "?" prints what premiere received, "refuseMatte on|off" makes the matte popup ignore setValue,
// "activate <name>" switches the active sequence, "select V1 0" selects a clip of it, "marks <in> <out>" sets its in and out points

const { makePremiere, makeClip, loadHost, hostCall } = require('./hostMock');
const { createBridge } = require('../panel/js/bridgeServer');

const path = require('path');

const [bridgeFile, rushPath] = process.argv.slice(2);
// the fake saves a real project file next to the bridge file, so the mask graft has something to patch
const opts = { clips: [makeClip(rushPath, 0, 0, 7)], projectPath: path.join(path.dirname(bridgeFile), 'fake project.prproj'), renderSource: rushPath };
// a sequence that changes when host.jsx edits it: three shots of the rush, their sound on A1
const { makeLiveSequence, makeProjectItem } = require('./liveTimeline');
const rushItem = makeProjectItem(rushPath, 1);
const liveRegistry = [];
const shot = (start, src, len, link) => ({ item: rushItem, start, in: src, out: src + len, link });
opts.extraSequences = [makeLiveSequence('Live seq', 'live-1', {
    video: [[shot(0, 0, 2, 'a'), shot(2, 3, 1, 'b'), shot(3, 5, 2, 'c')], []],
    audio: [[shot(0, 0, 2, 'a'), shot(2, 3, 1, 'b'), shot(3, 5, 2, 'c')]]
}, liveRegistry)];
opts.projectItems = [rushItem];
const premiere = makePremiere(opts);
liveRegistry.push = (x) => premiere.sequences.push(x);
const ctx = loadHost(premiere);
const bridge = createBridge({ bridgeFile, evalHost: (fn, args) => hostCall(ctx, fn, args) });

bridge.start().then((port) => {
    process.stdout.write(JSON.stringify({ ready: true, port }) + '\n');
});

let pending = '';
process.stdin.on('data', (chunk) => {
    pending += chunk;
    let cut;
    while ((cut = pending.indexOf('\n')) >= 0) {
        const line = pending.slice(0, cut).trim();
        pending = pending.slice(cut + 1);
        const toggle = line.match(/^refuseMatte (on|off)$/);
        if (toggle) { opts.refuseMatte = toggle[1] === 'on'; }
        const activate = line.match(/^activate (.+)$/);
        if (activate) { premiere.project.activeSequence = premiere.sequences.find((s) => s.name === activate[1]) || premiere.seq; }
        const select = line.match(/^select V(\d+) (\d+)$/);
        if (select) { premiere.project.activeSequence.videoTracks[Number(select[1]) - 1].clips[Number(select[2])].selected = true; }
        const marks = line.match(/^marks (\S+) (\S+)$/);
        if (marks) { premiere.project.activeSequence.inPoint = Number(marks[1]); premiere.project.activeSequence.outPoint = Number(marks[2]); }
        process.stdout.write(JSON.stringify({
            imported: premiere.project.imported,
            opened: premiere.project.opened || null,
            active: premiere.project.activeSequence ? premiere.project.activeSequence.name : null,
            sequences: premiere.sequences.map((s) => s.name),
            qeAdded: premiere.qe.added,
            markers: premiere.seq.markers.list.map((m) => ({ name: m.name, start: m.start.seconds, end: m.end }))
        }) + '\n');
    }
});
process.on('SIGTERM', () => { bridge.stop(); process.exit(0); });
