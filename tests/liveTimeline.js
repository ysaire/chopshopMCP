// a timeline that changes when host.jsx edits it, as premiere 25 does: start keeps the in point, remove never
// ripples, no setter touches the linked items, insertClip and overwriteClip bring picture and sound, an insert
// pushes every track

const path = require('path');

const ticksPerSecond = 254016000000;

function time(sec) {
    return { seconds: sec, ticks: String(Math.round(sec * ticksPerSecond)) };
}

function collection(items, countKey) {
    const coll = { length: items.length };
    coll[countKey] = items.length;
    items.forEach((item, i) => { coll[i] = item; });
    return coll;
}

function makeProjectItem(mediaPath, audioTracks) {
    return {
        name: path.basename(mediaPath),
        type: 1,
        audioTracks: audioTracks === undefined ? 1 : audioTracks,
        marks: { in: null, out: null },
        getMediaPath() { return mediaPath; },
        setInPoint(sec) { this.marks.in = Number(sec); return true; },
        setOutPoint(sec) { this.marks.out = Number(sec); return true; },
        clearInPoint() { this.marks.in = null; return true; },
        clearOutPoint() { this.marks.out = null; return true; },
        moveBin(bin) { bin.adopt(this); return true; }
    };
}

// premiere hands out a new object on each read (clips[0] !== clips[0]): only nodeId identifies an item
function view(clip) {
    return new Proxy(clip, {
        get(target, key) {
            const v = Reflect.get(target, key, target);
            return typeof v === 'function' ? v.bind(target) : v;
        },
        set(target, key, value) { return Reflect.set(target, key, value, target); }
    });
}

let nodes = 0x0f4000;

function makeLiveSequence(name, id, spec, registry) {
    let links = 0;
    const seq = { name, sequenceID: id, timebase: '4233600000', frameSizeHorizontal: 1920, frameSizeVertical: 1080, position: '0' };

    function makeClip(track, s) {
        const clip = {
            _s: s.start, _e: s.start + (s.out - s.in), _in: s.in, _out: s.out, link: s.link || null, track,
            projectItem: s.item, name: s.item.name, mediaType: track.audio ? 'Audio' : 'Video', disabled: false,
            nodeId: (++nodes).toString(16).padStart(8, '0'),
            get parentTrackIndex() { return (track.audio ? seq.audioTracks : seq.videoTracks).indexOf(track); },
            get start() { return time(this._s); },
            set start(t) { this._s = t.seconds; },
            get end() { return time(this._e); },
            set end(t) { this._e = t.seconds; },
            get inPoint() { return time(this._in); },
            set inPoint(t) { this._in = t.seconds; },
            // premiere 25 reports the out point the clip was made with: the setters below leave it stale
            get outPoint() { return time(this._out); },
            get duration() { return time(this._e - this._s); },
            components: collection([], 'numItems'),
            getSpeed() { return 1; },
            isSelected() { return false; },
            move(t) { this._s += t.seconds; this._e += t.seconds; },
            remove() { track.items.splice(track.items.indexOf(this), 1); return true; },
            getLinkedItems() {
                const all = this.link ? seq.allItems().filter((x) => x.link === this.link) : [this];
                return collection(all.map(view), 'numItems');
            }
        };
        return clip;
    }

    function makeTrack(audio) {
        const track = {
            audio, items: [], cuts: [],
            get clips() { return collection(this.items.slice().sort((a, b) => a._s - b._s).map(view), 'numItems'); },
            get transitions() {
                const list = this.cuts.slice().sort((a, b) => a.s - b.s).map((x) => ({
                    name: x.name, matchName: 'ADBE ' + x.name, start: time(x.s), end: time(x.e),
                    remove: () => { this.cuts.splice(this.cuts.indexOf(x), 1); return true; }
                }));
                return collection(list, 'numItems');
            },
            // the qe call: duration as the sequence's timecode (two seconds without one), alignment 0 starts at the cut
            addTransitionAt(name, cut, duration, alignment) {
                const fps = Math.round(ticksPerSecond / Number(seq.timebase));
                let dur = 2;
                if (duration) {
                    const p = String(duration).split(/[;:]/).map(Number);
                    dur = ((p[0] * 60 + p[1]) * 60 + p[2]) + p[3] / fps;
                }
                const a = alignment === undefined ? 0.5 : Number(alignment);
                this.cuts.push({ name, s: cut - dur * a, e: cut - dur * a + dur });
                return true;
            },
            insertClip(item, seconds) { return place(this, item, Number(seconds), true); },
            overwriteClip(item, seconds) { return place(this, item, Number(seconds), false); }
        };
        return track;
    }

    function splitAt(track, t, rightLinks) {
        track.items.slice().forEach((c) => {
            if (c._s < t - 1e-9 && t < c._e - 1e-9) {
                const key = c.link || ('solo' + track.items.indexOf(c));
                if (!rightLinks[key]) { rightLinks[key] = 'link' + (++links); }
                const right = makeClip(track, { item: c.projectItem, start: t, in: c._in + (t - c._s), out: c._in + (c._e - c._s), link: c.link ? rightLinks[key] : null });
                c._e = t;
                c._out = c._in + (t - c._s);
                track.items.push(right);
            }
        });
    }

    function clear(track, a, b) {
        const rightLinks = {};
        splitAt(track, a, rightLinks);
        splitAt(track, b, rightLinks);
        track.items = track.items.filter((c) => !(c._s >= a - 1e-9 && c._e <= b + 1e-9));
    }

    function place(target, item, at, insert) {
        const dur = item.marks.out - item.marks.in;
        const targets = [target];
        for (let k = 0; k < (item.audioTracks || 0) && k < seq.audioTracks.length; k++) { targets.push(seq.audioTracks[k]); }
        if (insert) {
            const rightLinks = {};
            const every = Array.from(seq.videoTracks).concat(Array.from(seq.audioTracks));
            every.forEach((tr) => splitAt(tr, at, rightLinks));
            every.forEach((tr) => tr.items.forEach((c) => { if (c._s >= at - 1e-9) { c._s += dur; c._e += dur; } }));
        } else {
            targets.forEach((tr) => clear(tr, at, at + dur));
        }
        const link = 'link' + (++links);
        targets.forEach((tr) => tr.items.push(makeClip(tr, { item, start: at, in: item.marks.in, out: item.marks.out, link })));
        return true;
    }

    const video = (spec.video || []).map(() => makeTrack(false));
    const audio = (spec.audio || []).map(() => makeTrack(true));
    seq.videoTracks = Object.assign(video.slice(), { numTracks: video.length });
    seq.audioTracks = Object.assign(audio.slice(), { numTracks: audio.length });
    (spec.video || []).forEach((clips, t) => clips.forEach((s) => video[t].items.push(makeClip(video[t], s))));
    (spec.audio || []).forEach((clips, t) => clips.forEach((s) => audio[t].items.push(makeClip(audio[t], s))));
    seq.allItems = () => video.concat(audio).reduce((all, tr) => all.concat(tr.items), []);
    seq.addVideoTracks = (n) => {
        for (let k = 0; k < n; k++) { video.push(makeTrack(false)); }
        seq.videoTracks = Object.assign(video.slice(), { numTracks: video.length });
    };
    seq.getSelection = () => collection([], 'numItems');
    seq.getPlayerPosition = () => time(Number(seq.position) / ticksPerSecond);
    seq.setPlayerPosition = (ticks) => { seq.position = ticks; return true; };
    seq.getInPointAsTime = () => time(-400000);
    seq.getOutPointAsTime = () => time(-400000);
    seq.markers = { numMarkers: 0 };
    // premiere names the copy "<name> Copy" and adds it to the project
    seq.clone = () => {
        const copySpec = {
            video: video.map((tr) => tr.items.map((c) => ({ item: c.projectItem, start: c._s, in: c._in, out: c._in + (c._e - c._s), link: c.link }))),
            audio: audio.map((tr) => tr.items.map((c) => ({ item: c.projectItem, start: c._s, in: c._in, out: c._in + (c._e - c._s), link: c.link })))
        };
        const copy = makeLiveSequence(name + ' Copy', id + '-copy' + (registry ? registry.length : 0), copySpec, registry);
        if (registry) { registry.push(copy); }
        return true;
    };
    return seq;
}

module.exports = { makeLiveSequence, makeProjectItem };
