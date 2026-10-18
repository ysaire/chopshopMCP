// fake premiere dom, enough to run host.jsx in a node vm

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const zlib = require('zlib');

const ticksPerSecond = 254016000000;
const videoMediaType = '228cda18-3625-4d2d-951e-348879e4ed93';

function time(sec) {
    return { seconds: sec, ticks: String(Math.round(sec * ticksPerSecond)) };
}

function collection(items, countKey) {
    const coll = { length: items.length };
    coll[countKey] = items.length;
    items.forEach((item, i) => { coll[i] = item; });
    return coll;
}

function makeMarkers() {
    const list = [];
    return {
        list,
        get numMarkers() { return list.length; },
        createMarker(sec) {
            const m = { start: time(sec), end: time(sec), name: '', comments: '', color: 0, setColorByIndex(c) { this.color = c; } };
            list.push(m);
            return m;
        },
        getFirstMarker() { return list[0]; },
        getNextMarker(m) { return list[list.indexOf(m) + 1]; },
        deleteMarker(m) { list.splice(list.indexOf(m), 1); return true; }
    };
}

// clip of mediaPath placed at seqStart, using source [srcIn, srcOut]
function makeClip(mediaPath, seqStart, srcIn, srcOut) {
    return {
        components: collection([
            { displayName: 'Opacity', matchName: 'AE.ADBE Opacity', properties: collection([makeParam(100, 'Opacity')], 'numItems') },
            { displayName: 'Motion', matchName: 'AE.ADBE Motion', properties: collection([makeParam([0.5, 0.5], 'Position'), makeParam(100, 'Scale')], 'numItems') }
        ], 'numItems'),
        selected: false,
        isSelected() { return this.selected; },
        projectItem: { name: path.basename(mediaPath), getMediaPath() { return mediaPath; } },
        start: time(seqStart),
        end: time(seqStart + (srcOut - srcIn)),
        inPoint: time(srcIn),
        outPoint: time(srcOut),
        getSpeed() { return 1; }
    };
}

// timeline clip pointing at a nested sequence instead of a file
function makeNestClip(nodeId, name, seqStart, srcIn, srcOut) {
    return {
        name: name,
        projectItem: { name: name, nodeId: nodeId, type: 1, getMediaPath() { return ''; }, isSequence() { return true; } },
        start: time(seqStart),
        end: time(seqStart + (srcOut - srcIn)),
        inPoint: time(srcIn),
        outPoint: time(srcOut),
        getSpeed() { return 1; }
    };
}

function makeInnerSequence(nodeId, name, clips) {
    return {
        name: name,
        sequenceID: 'inner-' + nodeId,
        projectItem: { nodeId: nodeId, name: name },
        videoTracks: collection([{ clips: collection(clips, 'numItems') }], 'numTracks'),
        audioTracks: collection([], 'numTracks')
    };
}

function makeParam(initial, displayName) {
    return {
        displayName: displayName || '',
        value: initial,
        keys: [],
        timeVarying: false,
        getValue() { return this.value; },
        setValue(v) { this.value = v; return 0; },
        setColorValue(a, r, g, b) { this.value = [a, r, g, b]; return 0; },
        isTimeVarying() { return this.timeVarying; },
        getKeys() { return this.keys.map((k) => ({ seconds: k.t })); },
        setTimeVarying(v) { this.timeVarying = v; if (!v) { this.keys = []; } return 0; },
        addKey(t) { this.keys.push({ t: t.seconds }); return 0; },
        setValueAtKey(t, v) {
            const key = this.keys.find((k) => k.t === t.seconds);
            if (!key) { throw new Error('no key at ' + t.seconds); }
            key.v = v;
            return 0;
        }
    };
}

// effects premiere adds through qe (or reads from the xml), english ui names
function makeEffect(name, owner, opts) {
    const props = {
        'Gaussian Blur': () => [makeParam(0, 'Blurriness'), makeParam(1, 'Blur Dimensions'), makeParam(false, 'Repeat Edge Pixels')],
        'Mosaic': () => [makeParam(10, 'Horizontal Blocks'), makeParam(10, 'Vertical Blocks'), makeParam(false, 'Sharp Colors')],
        'Transform': () => [makeParam([0.5, 0.5], 'Anchor Point'), makeParam([0.5, 0.5], 'Position'), makeParam(true, 'Uniform Scale'),
            makeParam(100, 'Scale'), makeParam(100, 'Scale Width'), makeParam(0, 'Skew'), makeParam(0, 'Skew Axis'),
            makeParam(0, 'Rotation'), makeParam(100, 'Opacity')],
        'Drop Shadow': () => [makeParam([255, 0, 0, 0], 'Shadow Color'), makeParam(50, 'Opacity'), makeParam(135, 'Direction'),
            makeParam(5, 'Distance'), makeParam(0, 'Softness'), makeParam(false, 'Shadow Only')],
        'Lumetri Color': () => [makeParam(0, 'Exposure'), makeParam(0, 'Contrast')],
        'DeNoise': () => [makeParam(0.4, 'Amount')],
        // this build stores corner pin points in layer pixels
        'Corner Pin': () => [makeParam([0, 0], 'Upper Left'), makeParam([320, 0], 'Upper Right'), makeParam([0, 180], 'Lower Left'),
            makeParam([320, 180], 'Lower Right')],
        'Warp Stabilizer': () => [makeParam(0, 'Analyze'), makeParam(50, 'Smoothness')],
        'Track Matte Key': () => {
            // popup: 0 = none, n = video n; out of range clamps to the last track
            // premiere names these with a trailing colon in effect controls
            const matte = makeParam(0, 'Matte:');
            matte.setValue = function (v) {
                if (opts && opts.refuseMatte) { return 0; }
                this.value = Math.max(0, Math.min(owner().videoTracks.numTracks, v));
                return 0;
            };
            return [matte, makeParam(0, 'Composite Using:'), makeParam(false, 'Reverse')];
        }
    }[name];
    return { displayName: name, matchName: 'AE.ADBE ' + name, properties: collection(props(), 'numItems') };
}

function addComponent(clip, comp) {
    const list = [];
    for (let i = 0; i < clip.components.numItems; i++) { list.push(clip.components[i]); }
    list.push(comp);
    clip.components = collection(list, 'numItems');
}

// tiny xml reader, enough for xmeml
function parseXml(text) {
    const decode = (t) => t.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, '&');
    const root = { tag: '#root', attrs: {}, children: [], text: '' };
    const stack = [root];
    const re = /<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<!DOCTYPE[^>]*>|<\/([\w:.-]+)\s*>|<([\w:.-]+)((?:\s+[\w:.-]+="[^"]*")*)\s*(\/?)>|([^<]+)/g;
    let m;
    while ((m = re.exec(text))) {
        if (m[1]) { stack.pop(); }
        else if (m[2]) {
            const attrs = {};
            (m[3] || '').replace(/([\w:.-]+)="([^"]*)"/g, (_, k, v) => { attrs[k] = decode(v); });
            const node = { tag: m[2], attrs, children: [], text: '' };
            stack[stack.length - 1].children.push(node);
            if (!m[4]) { stack.push(node); }
        } else if (m[5]) { stack[stack.length - 1].text += decode(m[5]); }
    }
    return root;
}
const xChild = (n, tag) => (n ? n.children.find((c) => c.tag === tag) : null);
const xKids = (n, tag) => (n ? n.children.filter((c) => c.tag === tag) : []);
const xText = (n, tag) => { const c = xChild(n, tag); return c ? c.text.trim() : null; };
const xEsc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// what premiere saves, enough for the mask graft to find a clip's effects and the nests a sequence plays
function projectXml(sequences) {
    let nextId = 1;
    const objects = [];
    const ticks = (sec) => String(Math.round(sec * ticksPerSecond));
    const uid = () => 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
        const r = Math.floor(Math.random() * 16);
        return (c === 'x' ? r : (r & 3) | 8).toString(16);
    });
    for (const seq of sequences) {
        const trackRefs = [];
        for (let t = 0; t < seq.videoTracks.numTracks; t++) {
            const track = seq.videoTracks[t];
            const itemRefs = [];
            for (let c = 0; c < track.clips.numItems; c++) {
                const clip = track.clips[c];
                const compRefs = [];
                for (let k = 0; k < clip.components.numItems; k++) {
                    const comp = clip.components[k];
                    if (/^AE\.ADBE (Opacity|Motion)$/.test(comp.matchName)) { continue; }
                    const paramRefs = [];
                    for (let p = 0; p < comp.properties.numItems; p++) {
                        const id = nextId++;
                        const value = typeof comp.properties[p].value === 'number' ? comp.properties[p].value : 0;
                        objects.push(`\t<VideoComponentParam ObjectID="${id}" ClassID="a4ff2d6e-7ac2-44f8-9d52-17d9ca50e542" Version="9">\n`
                            + `\t\t<ParameterID>${p + 1}</ParameterID>\n\t\t<IsTimeVarying>false</IsTimeVarying>\n\t\t<ParameterControlType>8</ParameterControlType>\n`
                            + `\t\t<Name>${xEsc(comp.properties[p].displayName)}</Name>\n\t\t<StartKeyframe>-91445760000000000,${value},0,0,0,0,0,0</StartKeyframe>\n`
                            + `\t\t<LowerBound>0</LowerBound>\n\t\t<UpperBound>100</UpperBound>\n\t</VideoComponentParam>\n`);
                        paramRefs.push(id);
                    }
                    // the masks under the effect: one Mask Path parameter each, as premiere keeps a pen mask
                    const maskRefs = [];
                    (comp.masks || []).forEach((mask, mi) => {
                        const pathId = nextId++;
                        const first = String(mask.keyframes || '').split(';').filter(Boolean)[0];
                        objects.push(`\t<ArbVideoComponentParam ObjectID="${pathId}" ClassID="313e54d4-6903-49ad-b0bf-8262cdd10f4e" Version="2">\n`
                            + `\t\t<ParameterID>6</ParameterID>\n\t\t<ParameterControlType>22</ParameterControlType>\n\t\t<Name>Mask Path</Name>\n`
                            + `\t\t<StartKeyframePosition>-91445760000000000</StartKeyframePosition>\n\t\t<Keyframes>${mask.keyframes || ''}</Keyframes>\n`
                            + (first ? `\t\t<StartKeyframeValue Encoding="base64">${first.split(',')[1]}\n\t\t</StartKeyframeValue>\n` : '')
                            + `\t</ArbVideoComponentParam>\n`);
                        const maskId = nextId++;
                        objects.push(`\t<VideoFilterComponent ObjectID="${maskId}" ClassID="d10da199-beea-4dd1-b941-ed3a78766d50" Version="9">\n\t\t<Component Version="6">\n`
                            + `\t\t\t<Params Version="1">\n\t\t\t\t<Param Index="0" ObjectRef="${pathId}"/>\n\t\t\t</Params>\n\t\t\t<ID>${mi}</ID>\n\t\t\t<Bypass>false</Bypass>\n`
                            + `\t\t\t<DisplayName>Mask</DisplayName>\n\t\t\t<Intrinsic>false</Intrinsic>\n\t\t\t<InstanceName>${xEsc(mask.instance || String(mi + 1))}</InstanceName>\n`
                            + `\t\t\t<ArchivedType>0</ArchivedType>\n\t\t</Component>\n\t\t<MatchName>AE.ADBE AEMask</MatchName>\n\t\t<VideoFilterType>2</VideoFilterType>\n\t</VideoFilterComponent>\n`);
                        maskRefs.push(maskId);
                    });
                    const id = nextId++;
                    objects.push(`\t<VideoFilterComponent ObjectID="${id}" ClassID="d10da199-beea-4dd1-b941-ed3a78766d50" Version="9">\n\t\t<Component Version="6">\n\t\t\t<Params Version="1">\n`
                        + paramRefs.map((r, i) => `\t\t\t\t<Param Index="${i}" ObjectRef="${r}"/>\n`).join('')
                        + `\t\t\t</Params>\n\t\t\t<ID>${k + 1}</ID>\n\t\t\t<Bypass>false</Bypass>\n\t\t\t<DisplayName>${xEsc(comp.displayName)}</DisplayName>\n`
                        + `\t\t\t<Intrinsic>false</Intrinsic>\n\t\t\t<InstanceName>${xEsc(comp.displayName)}</InstanceName>\n\t\t\t<ArchivedType>0</ArchivedType>\n\t\t</Component>\n`
                        + (maskRefs.length ? `\t\t<SubComponents Version="1">\n` + maskRefs.map((r, i) => `\t\t\t<SubComponent Index="${i}" ObjectRef="${r}"/>\n`).join('') + `\t\t</SubComponents>\n` : '')
                        + `\t\t<MatchName>${xEsc(comp.matchName)}</MatchName>\n\t\t<VideoFilterType>2</VideoFilterType>\n\t</VideoFilterComponent>\n`);
                    compRefs.push(id);
                }
                const chainId = nextId++;
                objects.push(`\t<VideoComponentChain ObjectID="${chainId}" ClassID="0970e08a-f58f-4108-b29a-1a717b8e12e2" Version="3">\n\t\t<ComponentChain Version="3">\n\t\t\t<Components Version="1">\n`
                    + compRefs.map((r, i) => `\t\t\t\t<Component Index="${i}" ObjectRef="${r}"/>\n`).join('')
                    + `\t\t\t</Components>\n\t\t</ComponentChain>\n\t</VideoComponentChain>\n`);
                // the clip's source: a nest, or the file; either way the in and out points live on the VideoClip
                const srcId = nextId++;
                if (clip.nestId) {
                    objects.push(`\t<VideoSequenceSource ObjectID="${srcId}" ClassID="4752dfa9-7a7e-4a3b-a25b-cafde1a8d036" Version="3">\n\t\t<SequenceSource Version="4">\n`
                        + `\t\t\t<Sequence ObjectURef="${clip.nestId}"/>\n\t\t</SequenceSource>\n\t</VideoSequenceSource>\n`);
                } else {
                    objects.push(`\t<VideoMediaSource ObjectID="${srcId}" ClassID="fake-video-media-source" Version="1">\n\t</VideoMediaSource>\n`);
                }
                const vclipId = nextId++;
                objects.push(`\t<VideoClip ObjectID="${vclipId}" ClassID="9308dbef-2440-4acb-9ab2-953b9a4e82ec" Version="11">\n\t\t<Clip Version="18">\n\t\t\t<Source ObjectRef="${srcId}"/>\n`
                    + `\t\t\t<InPoint>${ticks(clip.inPoint.seconds)}</InPoint>\n\t\t\t<OutPoint>${ticks(clip.outPoint.seconds)}</OutPoint>\n\t\t</Clip>\n\t</VideoClip>\n`);
                const clipRef = `\t\t<Clip ObjectRef="${vclipId}"/>\n`;
                const subId = nextId++;
                const clipName = clip.name || (clip.projectItem && clip.projectItem.name) || '';
                let mediaPath = '';
                try { mediaPath = clip.nestId ? '' : (clip.projectItem && clip.projectItem.getMediaPath()) || ''; } catch (e) { mediaPath = ''; }
                const mediaLine = mediaPath ? `\t\t<MediaPath>${xEsc(mediaPath)}</MediaPath>\n` : '';
                objects.push(`\t<SubClip ObjectID="${subId}" ClassID="e0c58dc9-dbdd-4166-aef7-5db7e3f22e84" Version="6">\n${clipRef}\t\t<Name>${xEsc(clipName)}</Name>\n${mediaLine}\t</SubClip>\n`);
                const itemId = nextId++;
                objects.push(`\t<VideoClipTrackItem ObjectID="${itemId}" ClassID="368b0406-29e3-4923-9fcd-094fbf9a1089" Version="8">\n\t\t<ClipTrackItem Version="8">\n`
                    + `\t\t\t<ComponentOwner Version="1">\n\t\t\t\t<Components ObjectRef="${chainId}"/>\n\t\t\t</ComponentOwner>\n`
                    + `\t\t\t<TrackItem Version="3">\n\t\t\t\t<Start>${ticks(clip.start.seconds)}</Start>\n\t\t\t\t<End>${ticks(clip.end.seconds)}</End>\n\t\t\t</TrackItem>\n`
                    + `\t\t\t<SubClip ObjectRef="${subId}"/>\n\t\t</ClipTrackItem>\n\t</VideoClipTrackItem>\n`);
                itemRefs.push(itemId);
            }
            const trackUid = uid();
            objects.push(`\t<VideoClipTrack ObjectUID="${trackUid}" ClassID="f68dcd81-8805-11d5-af2d-9bfa89d4ddd4" Version="1">\n\t\t<ClipTrack Version="2">\n\t\t\t<ClipItems Version="3">\n\t\t\t\t<TrackItems Version="1">\n`
                + itemRefs.map((r, i) => `\t\t\t\t\t<TrackItem Index="${i}" ObjectRef="${r}"/>\n`).join('')
                + `\t\t\t\t</TrackItems>\n\t\t\t</ClipItems>\n\t\t</ClipTrack>\n\t</VideoClipTrack>\n`);
            trackRefs.push(trackUid);
        }
        const groupId = nextId++;
        objects.push(`\t<VideoTrackGroup ObjectID="${groupId}" ClassID="9e9abf7a-0918-49c2-91ae-991b5dde77bb" Version="13">\n\t\t<TrackGroup Version="1">\n\t\t\t<Tracks Version="1">\n`
            + trackRefs.map((r, i) => `\t\t\t\t<Track Index="${i}" ObjectURef="${r}"/>\n`).join('')
            + `\t\t\t</Tracks>\n\t\t</TrackGroup>\n\t</VideoTrackGroup>\n`);
        objects.push(`\t<Sequence ObjectUID="${seq.sequenceID}" ClassID="6a15d903-8739-11d5-af2d-9b7855ad8974" Version="11">\n\t\t<TrackGroups Version="1">\n`
            + `\t\t\t<TrackGroup Version="1" Index="0">\n\t\t\t\t<First>${videoMediaType}</First>\n\t\t\t\t<Second ObjectRef="${groupId}"/>\n\t\t\t</TrackGroup>\n`
            + `\t\t</TrackGroups>\n\t\t<Name>${xEsc(seq.name)}</Name>\n\t</Sequence>\n`);
    }
    return '<?xml version="1.0" encoding="UTF-8" ?>\n<PremiereData Version="3">\n' + objects.join('') + '</PremiereData>\n';
}

// a saved project read back: {uid: {name, nests: [uid], tracks: [[{name, mediaPath, start, end, in, out, effects: [{name, matchName,
// masks: [{instance, keyframes}]}]}]]}}
function readProjectFile(file) {
    const raw = fs.readFileSync(file);
    const text = (raw[0] === 0x1f && raw[1] === 0x8b ? zlib.gunzipSync(raw) : raw).toString('utf8');
    const root = parseXml(text);
    const data = xChild(root, 'PremiereData');
    const byId = {};
    const byUid = {};
    data.children.forEach((n) => {
        if (n.attrs.ObjectID) { byId[n.attrs.ObjectID] = n; }
        if (n.attrs.ObjectUID) { byUid[n.attrs.ObjectUID] = n; }
    });
    const ref = (n) => (n ? byId[n.attrs.ObjectRef] : null);
    const uref = (n) => (n ? byUid[n.attrs.ObjectURef] : null);
    const out = {};
    data.children.filter((n) => n.tag === 'Sequence' && n.attrs.ObjectUID).forEach((seq) => {
        const entry = { name: xText(seq, 'Name'), nests: [], tracks: [] };
        xKids(xChild(seq, 'TrackGroups'), 'TrackGroup').forEach((tg) => {
            const group = ref(xChild(tg, 'Second'));
            if (!group || group.tag !== 'VideoTrackGroup') { return; }
            xKids(xChild(xChild(group, 'TrackGroup'), 'Tracks'), 'Track').forEach((tr) => {
                const track = uref(tr);
                const clips = [];
                xKids(xChild(xChild(xChild(track, 'ClipTrack'), 'ClipItems'), 'TrackItems'), 'TrackItem').forEach((ti) => {
                    const item = ref(ti);
                    const cti = xChild(item, 'ClipTrackItem');
                    const sub = ref(xChild(cti, 'SubClip'));
                    const vclip = ref(xChild(sub, 'Clip'));
                    const source = vclip ? ref(xChild(xChild(vclip, 'Clip'), 'Source')) : null;
                    const nestRef = source && source.tag === 'VideoSequenceSource' ? xChild(xChild(source, 'SequenceSource'), 'Sequence') : null;
                    if (nestRef) { entry.nests.push(nestRef.attrs.ObjectURef); }
                    const chain = ref(xChild(xChild(cti, 'ComponentOwner'), 'Components'));
                    const effects = xKids(xChild(xChild(chain, 'ComponentChain'), 'Components'), 'Component').map((c) => {
                        const fc = ref(c);
                        const masks = xKids(xChild(fc, 'SubComponents'), 'SubComponent').map((s) => {
                            const m = ref(s);
                            const params = xKids(xChild(xChild(m, 'Component'), 'Params'), 'Param').map(ref);
                            const pathParam = params.find((p) => p && xText(p, 'ParameterID') === '6');
                            return { instance: xText(xChild(m, 'Component'), 'InstanceName'), keyframes: pathParam ? (xText(pathParam, 'Keyframes') || '') : '' };
                        });
                        return { name: xText(xChild(fc, 'Component'), 'DisplayName'), matchName: xText(fc, 'MatchName'), masks };
                    });
                    const tItem = xChild(cti, 'TrackItem');
                    clips.push({ name: xText(sub, 'Name'), mediaPath: xText(sub, 'MediaPath') || '', start: Number(xText(tItem, 'Start')) / ticksPerSecond,
                        end: Number(xText(tItem, 'End')) / ticksPerSecond, in: vclip ? Number(xText(xChild(vclip, 'Clip'), 'InPoint')) / ticksPerSecond : 0,
                        nestId: nestRef ? nestRef.attrs.ObjectURef : null, effects });
                });
                entry.tracks.push(clips);
            });
        });
        out[seq.attrs.ObjectUID] = entry;
    });
    return out;
}

// clip with opacity + motion components, position normalized unless pixelPositions
function makeMotionClip(seqStart, srcIn, srcOut, pixelPositions) {
    const clip = makeClip('C:/x.mp4', seqStart, srcIn, srcOut);
    clip.components = collection([
        { displayName: 'Opacity', matchName: 'AE.ADBE Opacity', properties: collection([makeParam(100)], 'numItems') },
        {
            displayName: 'Motion', matchName: 'AE.ADBE Motion',
            properties: collection([makeParam(pixelPositions ? [960, 540] : [0.5, 0.5], 'Position'), makeParam(100, 'Scale')], 'numItems')
        }
    ], 'numItems');
    return clip;
}

function makePremiere(opts) {
    function makeSequence(name, id, clips) {
        return {
            name: name,
            sequenceID: id,
            timebase: '8475667200',
            frameSizeHorizontal: 1080,
            frameSizeVertical: 1920,
            videoTracks: collection([{ clips: collection(clips, 'numItems') }], 'numTracks'),
            audioTracks: collection([{ clips: collection([], 'numItems') }], 'numTracks'),
            markers: makeMarkers(),
            position: '0',
            exports: [],
            exportAsMediaDirect(outPath, presetPath, range) {
                // the render is the rush itself when the test gives one, so the sidecar has real frames to watch
                if (opts.renderSource) { fs.copyFileSync(opts.renderSource, outPath); } else { fs.writeFileSync(outPath, 'render'); }
                this.exports.push({ path: outPath, preset: presetPath, range });
                return 'No Error';
            },
            captionTracks: [],
            inPoint: null,
            outPoint: null,
            // premiere reports an unset mark as a time far before the sequence
            getInPointAsTime() { return time(this.inPoint === null ? -360000 : this.inPoint); },
            getOutPointAsTime() { return time(this.outPoint === null ? -360000 : this.outPoint); },
            getSelection() { return collection(opts.selected || [], 'numItems'); },
            getPlayerPosition() { return time(Number(this.position) / ticksPerSecond); },
            setPlayerPosition(ticks) { this.position = ticks; return true; },
            createCaptionTrack(item, start) { this.captionTracks.push({ item: item.name, start }); return true; },
            importMGT(file, ticks, vTrack) {
                // a template clip: text in premiere's json, a color control
                const at = Number(ticks) / ticksPerSecond;
                const clip = makeClip(file, at, 0, 5);
                clip.name = path.basename(file, '.mogrt');
                clip.isMGT = () => true;
                const text = makeParam('{"fontEditValue":["Montserrat-Black"],"textEditValue":"Title"}', 'Main Text');
                const params = collection([text, makeParam([255, 255, 255, 255], 'Fill Color')], 'numItems');
                params.getParamForDisplayName = (n) => [text, params[1]].find((x) => x.displayName === n) || null;
                const mgt = { displayName: 'Graphic Parameters', properties: params };
                clip.getMGTComponent = () => mgt;
                Object.defineProperty(clip, 'end', { get() { return this._end; }, set(t) { this._end = time(t.seconds); }, configurable: true });
                clip.end = time(at + 5);
                const track = this.videoTracks[vTrack];
                const list = [];
                for (let i = 0; i < track.clips.numItems; i++) { list.push(track.clips[i]); }
                list.push(clip);
                list.sort((x, y) => x.start.seconds - y.start.seconds);
                track.clips = collection(list, 'numItems');
                return clip;
            },
            autoReframeSequence(num, den, preset, newName) {
                const s = makeSequence(newName, id + '-reframed', []);
                s.reframe = { num, den, preset };
                sequences.push(s);
                return s;
            }
        };
    }
    const seq = makeSequence('Rush seq', 'seq-0', opts.clips || []);
    const sequences = [seq].concat(opts.extraSequences || []);
    const rootChildren = (opts.projectItems || []).slice();
    const project = {
        name: 'demo.prproj',
        path: opts.projectPath || 'C:/proj/demo.prproj',
        activeSequence: seq,
        imported: [],
        saved: 0,
        get sequences() { return collection(sequences, 'numSequences'); },
        save() {
            this.saved += 1;
            // a real file only when the test gave the fake a place for it
            if (opts.projectPath) { fs.writeFileSync(opts.projectPath, zlib.gzipSync(Buffer.from(projectXml(sequences), 'utf8'))); }
            return 0;
        },
        importSequences(p, ids) {
            this.imported.push(p);
            if (!fs.existsSync(p)) {
                ids.forEach((id) => sequences.push(makeSequence('from ' + path.basename(p) + ' ' + id, id, [])));
                return 0;
            }
            // from a saved project: the sequences asked for, then the nests they play, rebuilt from the file
            const saved = readProjectFile(p);
            const wanted = [];
            const visit = (id) => {
                if (!saved[id] || wanted.indexOf(id) >= 0 || sequences.some((s) => s.sequenceID === id)) { return; }
                wanted.push(id);
                saved[id].nests.forEach(visit);
            };
            ids.forEach(visit);
            wanted.reverse().forEach((id) => {
                const entry = saved[id];
                const created = makeSequence(entry.name, id, []);
                created.videoTracks = collection(entry.tracks.map((clips, ti) => ({
                    name: 'Video ' + (ti + 1),
                    clips: collection(clips.map((c) => {
                        const clip = makeMotionClip(c.start, c.in, c.in + (c.end - c.start), opts.pixelPositions);
                        clip.name = c.name;
                        clip.nestId = c.nestId;
                        if (c.mediaPath) { clip.projectItem = { name: path.basename(c.mediaPath), getMediaPath() { return c.mediaPath; } }; }
                        c.effects.forEach((e) => {
                            const comp = { displayName: e.name, matchName: e.matchName, properties: collection([], 'numItems'), masks: e.masks };
                            addComponent(clip, comp);
                        });
                        return clip;
                    }), 'numItems')
                })), 'numTracks');
                sequences.push(created);
            });
            return 0;
        },
        deleteSequence(target) {
            const at = sequences.indexOf(target);
            if (at < 0) { return false; }
            sequences.splice(at, 1);
            if (this.activeSequence === target) { this.activeSequence = sequences[0] || null; }
            return true;
        },
        importFiles(paths) {
            this.imported.push(paths[0]);
            if (/\.(mp4|mov|mxf|wav|m4a|mp3|aif|aiff|png|jpg|jpeg)$/i.test(paths[0])) {
                // a .mov here is a rendered graphic: prores 4444 with alpha and no sound
                rootChildren.push(require('./liveTimeline').makeProjectItem(paths[0], /\.(png|jpg|jpeg|mov)$/i.test(paths[0]) ? 0 : 1));
                return true;
            }
            if (/\.srt$/i.test(paths[0])) {
                const p = paths[0];
                rootChildren.push({ name: path.basename(p), type: 1, getMediaPath() { return p; } });
                return true;
            }
            const xml = parseXml(fs.readFileSync(paths[0], 'utf8'));
            const main = xChild(xChild(xml, 'xmeml'), 'sequence');
            const fps = Number(xText(xChild(main, 'rate'), 'timebase')) || 30;
            const defs = [];
            (function collect(n) {
                n.children.forEach((c) => {
                    if (c.tag === 'sequence' && c !== main && c.children.length) { defs.push(c); }
                    collect(c);
                });
            }(main));
            // clips point at their real files, like premiere after an import
            const files = {};
            const fileOf = (c) => {
                const f = xChild(c, 'file');
                if (!f) { return null; }
                const url = xText(f, 'pathurl');
                if (url) {
                    let p = decodeURIComponent(url.replace(/^file:\/\/(localhost)?/, ''));
                    if (/^\/[A-Za-z]:/.test(p)) { p = p.slice(1); }
                    files[f.attrs.id] = p;
                }
                return files[f.attrs.id] || null;
            };
            const createdIds = {};
            const build = (node) => {
                const created = makeSequence(xText(node, 'name') || 'imported', 'seq-' + sequences.length, []);
                createdIds[node.attrs.id] = created.sequenceID;
                const video = xChild(xChild(node, 'media'), 'video');
                const tracks = xKids(video, 'track').map((t, ti) => ({
                    name: 'Video ' + (ti + 1),
                    enabled: xText(t, 'enabled') !== 'FALSE',
                    clips: collection(xKids(t, 'clipitem').map((c) => {
                        const clip = makeMotionClip(Number(xText(c, 'start')) / fps, Number(xText(c, 'in')) / fps,
                            Number(xText(c, 'out')) / fps, opts.pixelPositions);
                        clip.name = xText(c, 'name');
                        const filePath = fileOf(c);
                        if (filePath) { clip.projectItem = { name: path.basename(filePath), getMediaPath() { return filePath; } }; }
                        clip.nest = xChild(c, 'sequence') ? xChild(c, 'sequence').attrs.id : null;
                        clip.nestId = clip.nest ? createdIds[clip.nest] || null : null;
                        xKids(c, 'filter').forEach((f) => {
                            const effect = xChild(f, 'effect');
                            const id = xText(effect, 'effectid');
                            if (id === 'Gaussian Blur') { addComponent(clip, makeEffect(id, () => created, opts)); }
                            if (id === 'timeremap') {
                                const speed = xKids(effect, 'parameter').find((p) => xText(p, 'parameterid') === 'speed');
                                const value = Number(xText(speed, 'value')) / 100;
                                clip.getSpeed = () => value;
                            }
                        });
                        return clip;
                    }), 'numItems')
                }));
                created.videoTracks = collection(tracks.length ? tracks : [{ name: 'Video 1', clips: collection([], 'numItems') }], 'numTracks');
                // exploded l/r xml tracks are one stereo track in premiere; 0 db reads 0.1778 like the real level
                const audioXml = xKids(xChild(xChild(node, 'media'), 'audio'), 'track').filter((t) => (t.attrs.currentExplodedTrackIndex || '0') === '0');
                const audioTracks = audioXml.map((t, ti) => ({
                    name: 'Audio ' + (ti + 1),
                    clips: collection(xKids(t, 'clipitem').map((c) => {
                        const clip = makeClip(fileOf(c) || 'C:/a.wav', Number(xText(c, 'start')) / fps, Number(xText(c, 'in')) / fps, Number(xText(c, 'out')) / fps);
                        clip.components = collection([{ displayName: 'Volume', matchName: 'audio volume',
                            properties: collection([makeParam(false, 'Bypass'), makeParam(0.17782794, 'Level')], 'numItems') }], 'numItems');
                        return clip;
                    }), 'numItems')
                }));
                if (audioTracks.length) { created.audioTracks = collection(audioTracks, 'numTracks'); }
                sequences.push(created);
                return created;
            };
            // nests land in the project too, before the main one: the host must pick by name
            defs.forEach(build);
            if (main) { build(main); }
            return true;
        },
        get rootItem() {
            return {
                children: collection(rootChildren, 'numItems'),
                createBin(name) {
                    const bin = {
                        name, type: 2, items: [],
                        get children() { return collection(this.items, 'numItems'); },
                        adopt(item) {
                            const at = rootChildren.indexOf(item);
                            if (at >= 0) { rootChildren.splice(at, 1); }
                            this.items.push(item);
                        },
                        deleteBin() { rootChildren.splice(rootChildren.indexOf(bin), 1); return true; }
                    };
                    rootChildren.push(bin);
                    return bin;
                }
            };
        },
        getInsertionBin() { return {}; },
        openSequence(id) {
            this.opened = id;
            const found = sequences.find((x) => x.sequenceID === id);
            if (found) { this.activeSequence = found; }
            return true;
        }
    };
    const videoTransitions = ['Cross Dissolve', 'Dip to Black', 'Dip to White', 'Film Dissolve', 'Push', 'Slide', 'Wipe'];
    const audioTransitions = ['Constant Power', 'Constant Gain', 'Exponential Fade'];
    // 1x1 png, what the frame exporter leaves on disk
    const pngBytes = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64');
    const qe = {
        added: [],
        exported: [],
        project: {
            getActiveSequence() {
                const active = project.activeSequence;
                return {
                    name: active.name,
                    // qe: video tracks to add, after which one, audio tracks to add (then their type, place, submixes)
                    addTracks(numVideo, afterVideo, numAudio) {
                        if (active.addVideoTracks) { active.addVideoTracks(Number(numVideo) || 0); }
                        return true;
                    },
                    CTI: {
                        get timecode() {
                            // premiere formats the playhead in the sequence's timecode, drop frame with semicolons
                            const exact = ticksPerSecond / Number(active.timebase);
                            const fps = Math.round(exact);
                            const f = Math.round(Number(active.position) / ticksPerSecond * fps);
                            const pad = (n) => String(n).padStart(2, '0');
                            const sep = Number.isInteger(exact) ? ':' : ';';
                            return [pad(Math.floor(f / fps / 3600)), pad(Math.floor(f / fps / 60) % 60), pad(Math.floor(f / fps) % 60), pad(f % fps)].join(sep);
                        }
                    },
                    exportFramePNG(timecode, outPath) {
                        fs.writeFileSync(outPath, pngBytes);
                        qe.exported.push({ sequence: active.name, timecode, path: outPath });
                        return true;
                    },
                    getAudioTrackAt(t) { return this.trackAt(active.audioTracks[t], t, true); },
                    getVideoTrackAt(t) { return this.trackAt(active.videoTracks[t], t, false); },
                    trackAt(track, t, audio) {
                        const clips = track.clips;
                        const items = [];
                        let cursor = 0;
                        for (let i = 0; i < clips.numItems; i++) {
                            const clip = clips[i];
                            if (clip.start.seconds > cursor + 1e-9) { items.push({ type: 'Empty', start: { secs: cursor }, end: { secs: clip.start.seconds } }); }
                            items.push({
                                type: 'Clip',
                                name: clip.name,
                                start: { secs: clip.start.seconds },
                                end: { secs: clip.end.seconds },
                                addTransition(tr, atStart, duration, offset, alignment) {
                                    if (!track.addTransitionAt) { return false; }
                                    return track.addTransitionAt(tr.name, atStart ? clip.start.seconds : clip.end.seconds, duration, alignment);
                                },
                                addVideoEffect(fx) {
                                    addComponent(clip, makeEffect(fx.name, () => active, opts));
                                    qe.added.push({ sequence: active.name, track: t, index: i, effect: fx.name });
                                    return true;
                                },
                                addAudioEffect(fx) {
                                    addComponent(clip, makeEffect(fx.name, () => active, opts));
                                    qe.added.push({ sequence: active.name, track: 'A' + (t + 1), index: i, effect: fx.name });
                                    return true;
                                }
                            });
                            cursor = clip.end.seconds;
                        }
                        return { numItems: items.length, getItemAt(i) { return items[i]; } };
                    }
                };
            },
            getVideoEffectByName(name) {
                return ['Track Matte Key', 'Gaussian Blur', 'Mosaic', 'Transform', 'Drop Shadow', 'Lumetri Color', 'Corner Pin',
                    'Warp Stabilizer'].indexOf(name) >= 0 ? { name } : undefined;
            },
            getAudioEffectByName(name) { return ['DeNoise'].indexOf(name) >= 0 ? { name } : undefined; },
            getVideoTransitionList() { return videoTransitions.slice(); },
            getAudioTransitionList() { return audioTransitions.slice(); },
            getVideoTransitionByName(name) { return videoTransitions.indexOf(name) >= 0 ? { name } : undefined; },
            getAudioTransitionByName(name) { return audioTransitions.indexOf(name) >= 0 ? { name } : undefined; }
        }
    };
    const app = {
        version: '25.0.0', project, qeEnabled: false,
        encoder: { ENCODE_ENTIRE: 0, ENCODE_IN_TO_OUT: 1, ENCODE_WORKAREA: 2 },
        enableQE() { this.qeEnabled = true; return true; },
        getCurrentProjectViewSelection() { return opts.projectSelection || null; }
    };
    function File(p) {
        this.fsName = p;
        this.exists = fs.existsSync(p);
        this.length = this.exists ? fs.statSync(p).size : 0;
        const dir = path.dirname(p);
        this.parent = { exists: fs.existsSync(dir), create() { fs.mkdirSync(dir, { recursive: true }); return true; } };
    }
    return { app, File, seq, project, sequences, qe };
}

function loadHost(premiere) {
    function Time() { this.seconds = 0; }
    const ctx = vm.createContext({ app: premiere.app, File: premiere.File, Time: Time, $: {}, qe: premiere.qe });
    const src = fs.readFileSync(path.join(__dirname, '..', 'panel', 'jsx', 'host.jsx'), 'utf8');
    vm.runInContext(src, ctx, { filename: 'host.jsx' });
    return ctx;
}

// same script string the panel builds in cep.js
function es3Literal(value) {
    return JSON.stringify(value).replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
}

function hostCall(ctx, fn, args) {
    const script = 'chopshop.dispatch(' + es3Literal(fn) + ',' + es3Literal(JSON.stringify(args || {})) + ')';
    return JSON.parse(vm.runInContext(script, ctx));
}

module.exports = { makePremiere, makeClip, makeMotionClip, makeNestClip, makeInnerSequence, loadHost, hostCall, projectXml, readProjectFile };
