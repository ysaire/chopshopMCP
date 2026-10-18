// es3 only: no json, no array.indexOf/forEach, no string.trim

var chopshop = (typeof chopshop !== 'undefined') ? chopshop : {};

(function (fe) {
    var ticksPerSecond = 254016000000;
    var markerPrefix = '[chop] ';
    var maxCandidates = 60;

    function quoteString(s) {
        var out = '"';
        for (var i = 0; i < s.length; i++) {
            var c = s.charAt(i);
            var code = s.charCodeAt(i);
            if (c === '"') { out += '\\"'; }
            else if (c === '\\') { out += '\\\\'; }
            else if (c === '\n') { out += '\\n'; }
            else if (c === '\r') { out += '\\r'; }
            else if (c === '\t') { out += '\\t'; }
            else if (code < 32 || code === 0x2028 || code === 0x2029) {
                var hex = code.toString(16);
                while (hex.length < 4) { hex = '0' + hex; }
                out += '\\u' + hex;
            } else { out += c; }
        }
        return out + '"';
    }

    function toJson(v) {
        if (v === null || v === undefined) { return 'null'; }
        var t = typeof v;
        if (t === 'number') { return isFinite(v) ? String(v) : 'null'; }
        if (t === 'boolean') { return v ? 'true' : 'false'; }
        if (t === 'string') { return quoteString(v); }
        if (v instanceof Array) {
            var parts = [];
            for (var i = 0; i < v.length; i++) { parts.push(toJson(v[i])); }
            return '[' + parts.join(',') + ']';
        }
        if (t === 'object') {
            var props = [];
            for (var k in v) {
                if (v.hasOwnProperty(k) && typeof v[k] !== 'function') {
                    props.push(quoteString(String(k)) + ':' + toJson(v[k]));
                }
            }
            return '{' + props.join(',') + '}';
        }
        return quoteString(String(v));
    }

    function parseJson(s) {
        // input always comes from JSON.stringify in the panel, data only
        return eval('(' + s + ')');
    }

    function count(coll) {
        if (!coll) { return 0; }
        if (coll.numItems !== undefined) { return coll.numItems; }
        if (coll.numTracks !== undefined) { return coll.numTracks; }
        if (coll.numSequences !== undefined) { return coll.numSequences; }
        return coll.length || 0;
    }

    function normPath(p) {
        return String(p || '').replace(/\\/g, '/').toLowerCase();
    }

    function mediaPathOf(projectItem) {
        if (!projectItem) { return ''; }
        try { return projectItem.getMediaPath() || ''; } catch (e) { return ''; }
    }

    function activeSequence() {
        var seq = app.project ? app.project.activeSequence : null;
        if (!seq) { throw new Error('no active sequence, open a timeline first'); }
        return seq;
    }

    function clipsOfMedia(tracks, target) {
        var found = [];
        for (var t = 0; t < count(tracks); t++) {
            var clips = tracks[t].clips;
            for (var c = 0; c < count(clips); c++) {
                var clip = clips[c];
                if (normPath(mediaPathOf(clip.projectItem)) === target) { found.push(clip); }
            }
        }
        return found;
    }

    function isSequenceItem(projectItem) {
        try { return !!(projectItem && projectItem.isSequence && projectItem.isSequence()); } catch (e) { return false; }
    }

    function sequenceOfItem(projectItem) {
        var seqs = app.project.sequences;
        for (var i = 0; i < count(seqs); i++) {
            try {
                if (seqs[i].projectItem && seqs[i].projectItem.nodeId === projectItem.nodeId) { return seqs[i]; }
            } catch (e) {}
        }
        return null;
    }

    function resolveMedia(projectItem, depth) {
        var p = mediaPathOf(projectItem);
        if (p) { return { path: p, nested: false, via: null, itemName: projectItem.name }; }
        if (depth > 2 || !isSequenceItem(projectItem)) { return null; }
        var inner = sequenceOfItem(projectItem);
        if (!inner) { return null; }
        var groups = [inner.videoTracks, inner.audioTracks];
        for (var g = 0; g < groups.length; g++) {
            for (var t = 0; t < count(groups[g]); t++) {
                var clips = groups[g][t].clips;
                for (var c = 0; c < count(clips); c++) {
                    var r = resolveMedia(clips[c].projectItem, depth + 1);
                    if (r) { return { path: r.path, nested: true, via: inner.name, itemName: r.itemName }; }
                }
            }
        }
        return null;
    }

    function pathsInSequence(seq) {
        var map = {};
        var groups = [seq.videoTracks, seq.audioTracks];
        for (var g = 0; g < groups.length; g++) {
            for (var t = 0; t < count(groups[g]); t++) {
                var clips = groups[g][t].clips;
                for (var c = 0; c < count(clips); c++) {
                    var r = resolveMedia(clips[c].projectItem, 0);
                    if (r && !map[normPath(r.path)]) { map[normPath(r.path)] = { clip: clips[c], nested: r.nested }; }
                }
            }
        }
        return map;
    }

    function describeItem(projectItem) {
        if (!projectItem) { return null; }
        var d = { name: projectItem.name, type: projectItem.type, mediaPath: mediaPathOf(projectItem) };
        try { d.isSequence = projectItem.isSequence(); } catch (e1) {}
        try { d.isMulticam = projectItem.isMulticamClip(); } catch (e2) {}
        try { d.isMerged = projectItem.isMergedClip(); } catch (e3) {}
        try { d.isOffline = projectItem.isOffline(); } catch (e4) {}
        var r = resolveMedia(projectItem, 0);
        d.resolved = r ? r.path : null;
        return d;
    }

    var api = {};

    api.ping = function () {
        return { version: app.version, project: app.project ? app.project.name : null };
    };

    api.getContext = function () {
        var ctx = { premiereVersion: app.version, project: null, sequence: null, media: [] };
        if (!app.project) { return ctx; }
        ctx.project = { name: app.project.name, path: app.project.path };

        var seq = app.project.activeSequence;
        var inSeq = seq ? pathsInSequence(seq) : {};
        var seen = {};

        function addMedia(projectItem, source, bin) {
            if (!projectItem) { return; }
            var r = resolveMedia(projectItem, 0);
            if (!r) { return; }
            var key = normPath(r.path);
            if (seen[key]) { return; }
            if (ctx.media.length >= maxCandidates) { ctx.truncated = true; return; }
            seen[key] = true;
            var hit = inSeq[key];
            var name = r.itemName || r.path.replace(/\\/g, '/').split('/').pop();
            var entry = {
                path: r.path,
                name: name,
                source: source,
                nested: r.nested || !!(hit && hit.nested),
                // markers need the file placed directly on the timeline
                inSequence: !!(hit && !hit.nested)
            };
            if (r.via) { entry.via = r.via; }
            if (bin) { entry.bin = bin; }
            if (hit) {
                entry.onTimeline = true;
                entry.clipStart = hit.clip.start.seconds;
                entry.clipIn = hit.clip.inPoint.seconds;
                entry.clipOut = hit.clip.outPoint.seconds;
            }
            ctx.media.push(entry);
        }

        if (seq) {
            ctx.sequence = {
                name: seq.name,
                id: seq.sequenceID,
                ticksPerFrame: seq.timebase,
                width: seq.frameSizeHorizontal,
                height: seq.frameSizeVertical
            };
            try { ctx.sequence.playhead = seq.getPlayerPosition().seconds; } catch (e1) {}

            var sel = seq.getSelection();
            for (var i = 0; i < count(sel); i++) { addMedia(sel[i].projectItem, 'timeline'); }
        }

        var binType = (typeof ProjectItemType !== 'undefined' && ProjectItemType.BIN) || 2;
        function addSelected(item, bin, depth) {
            if (item && item.type === binType && item.children) {
                var inside = bin ? bin + '/' + item.name : item.name;
                for (var b = 0; b < count(item.children) && depth < 4; b++) { addSelected(item.children[b], inside, depth + 1); }
                return;
            }
            addMedia(item, 'project', bin);
        }
        var projSel = null;
        try { projSel = app.getCurrentProjectViewSelection(); } catch (e2) {}
        if (projSel) {
            for (var j = 0; j < projSel.length; j++) { addSelected(projSel[j], null, 0); }
        }

        if (seq) {
            for (var k in inSeq) {
                if (inSeq.hasOwnProperty(k)) { addMedia(inSeq[k].clip.projectItem, 'sequence'); }
            }
        }
        return ctx;
    };

    api.diagnose = function () {
        var out = { premiere: app.version, os: $.os, project: null, activeSequence: null, clips: [], projectSelection: [], sequences: [] };
        if (!app.project) { return out; }
        out.project = app.project.path;
        var seq = app.project.activeSequence;
        if (seq) {
            out.activeSequence = {
                name: seq.name,
                ticksPerFrame: seq.timebase,
                width: seq.frameSizeHorizontal,
                height: seq.frameSizeVertical,
                videoTracks: count(seq.videoTracks),
                audioTracks: count(seq.audioTracks)
            };
            var groups = [['V', seq.videoTracks], ['A', seq.audioTracks]];
            for (var g = 0; g < groups.length; g++) {
                for (var t = 0; t < count(groups[g][1]); t++) {
                    var clips = groups[g][1][t].clips;
                    for (var c = 0; c < count(clips) && out.clips.length < 40; c++) {
                        var clip = clips[c];
                        out.clips.push({
                            track: groups[g][0] + (t + 1),
                            name: clip.name,
                            start: clip.start.seconds,
                            inPoint: clip.inPoint.seconds,
                            outPoint: clip.outPoint.seconds,
                            item: describeItem(clip.projectItem)
                        });
                    }
                }
            }
        }
        var projSel = null;
        try { projSel = app.getCurrentProjectViewSelection(); } catch (e1) {}
        if (projSel) {
            for (var j = 0; j < projSel.length && j < 10; j++) { out.projectSelection.push(describeItem(projSel[j])); }
        }
        for (var s = 0; s < count(app.project.sequences) && s < 20; s++) {
            var sq = app.project.sequences[s];
            out.sequences.push({ name: sq.name, hasProjectItem: !!sq.projectItem });
        }
        return out;
    };

    api.importSequenceXml = function (args) {
        var proj = app.project;
        if (!proj) { throw new Error('no project open'); }
        var file = new File(args.xmlPath);
        if (!file.exists) { throw new Error('xml not found: ' + args.xmlPath); }

        var before = {};
        for (var i = 0; i < proj.sequences.numSequences; i++) { before[proj.sequences[i].sequenceID] = true; }

        var ok = proj.importFiles([file.fsName], true, proj.getInsertionBin(), false);

        // an xml with nests creates several sequences: pick the main one by name
        var fresh = [];
        for (var j = 0; j < proj.sequences.numSequences; j++) {
            var s = proj.sequences[j];
            if (!before[s.sequenceID]) { fresh.push(s); }
        }
        var nestNames = {};
        var wanted = args.nestNames || [];
        for (var n = 0; n < wanted.length; n++) { nestNames[wanted[n]] = true; }
        var created = null;
        var nests = [];
        for (var k = 0; k < fresh.length; k++) {
            if (nestNames[fresh[k].name]) {
                nests.push({ name: fresh[k].name, id: fresh[k].sequenceID });
            } else if (!created || fresh[k].name === args.sequenceName) {
                created = fresh[k];
            }
        }
        if (created) { proj.openSequence(created.sequenceID); }
        return {
            imported: !!ok,
            sequenceName: created ? created.name : null,
            sequenceId: created ? created.sequenceID : null,
            nests: nests
        };
    };

    api.addMarkersForMedia = function (args) {
        var seq = activeSequence();
        var target = normPath(args.mediaPath);
        // video clips first, audio only for audio-only rushes (podcasts)
        var clips = clipsOfMedia(seq.videoTracks, target);
        if (!clips.length) { clips = clipsOfMedia(seq.audioTracks, target); }
        if (!clips.length) { throw new Error('this rush is not in the active sequence'); }

        var color = (args.color !== undefined) ? args.color : 1;
        var markers = args.markers || [];
        var created = 0;

        for (var m = 0; m < markers.length; m++) {
            var mk = markers[m];
            for (var c = 0; c < clips.length; c++) {
                var clip = clips[c];
                var srcIn = clip.inPoint.seconds;
                var srcOut = clip.outPoint.seconds;
                if (mk.end <= srcIn || mk.start >= srcOut) { continue; }
                var speed = 1;
                try { speed = clip.getSpeed() || 1; } catch (e1) {}
                var s = Math.max(mk.start, srcIn);
                var e = Math.min(mk.end, srcOut);
                var seqStart = clip.start.seconds + (s - srcIn) / speed;
                var seqEnd = clip.start.seconds + (e - srcIn) / speed;

                var marker = seq.markers.createMarker(seqStart);
                var name = mk.name || 'cut';
                marker.name = (name.indexOf(markerPrefix) === 0) ? name : markerPrefix + name;
                marker.comments = mk.comment || '';
                marker.end = seqEnd;
                try { marker.setColorByIndex(color); } catch (e2) {}
                created++;
            }
        }
        return { created: created, clips: clips.length };
    };

    function findSequence(sequenceId) {
        if (!sequenceId) { return activeSequence(); }
        var seqs = app.project.sequences;
        for (var i = 0; i < count(seqs); i++) {
            if (seqs[i].sequenceID === sequenceId) { return seqs[i]; }
        }
        throw new Error('sequence not found: ' + sequenceId);
    }

    function timeAt(seconds) {
        var t = new Time();
        t.seconds = seconds;
        return t;
    }

    function motionOf(clip) {
        var comps = clip.components;
        for (var i = 0; i < count(comps); i++) {
            if (comps[i].matchName === 'AE.ADBE Motion') { return comps[i]; }
        }
        return count(comps) > 1 ? comps[1] : null;
    }

    // position is normalized in recent premiere builds, pixels in some older ones
    function positionValue(param, x, y, seq) {
        var current = null;
        try { current = param.getValue(); } catch (e) {}
        if (current && current.length === 2 && (current[0] > 2 || current[1] > 2)) {
            return [x * seq.frameSizeHorizontal, y * seq.frameSizeVertical];
        }
        return [x, y];
    }

    api.applyMotion = function (args) {
        var seq = findSequence(args.sequenceId);
        var frame = 1 / (254016000000 / Number(seq.timebase));
        var applied = 0;
        var report = [];
        var specs = args.clips || [];
        for (var i = 0; i < specs.length; i++) {
            var spec = specs[i];
            var track = seq.videoTracks[spec.track || 0];
            var clip = track ? track.clips[spec.index] : null;
            if (!clip) { report.push('no clip v' + ((spec.track || 0) + 1) + ' #' + spec.index); continue; }
            var motion = motionOf(clip);
            if (!motion) { report.push('no motion on clip ' + spec.index); continue; }
            var position = motion.properties[0];
            var scale = motion.properties[1];
            var from = spec.from;
            if (spec.keys && spec.keys.length) {
                // keys: [[media seconds, scale %, x, y], ...]
                scale.setTimeVarying(true);
                position.setTimeVarying(true);
                for (var k = 0; k < spec.keys.length; k++) {
                    var key = spec.keys[k];
                    var kt = timeAt(clip.inPoint.seconds + key[0]);
                    var last = k === spec.keys.length - 1;
                    scale.addKey(kt);
                    scale.setValueAtKey(kt, key[1], last);
                    position.addKey(kt);
                    position.setValueAtKey(kt, positionValue(position, key[2], key[3], seq), last);
                }
            } else if (!spec.to) {
                scale.setValue(from.scalePct, 1);
                position.setValue(positionValue(position, from.posX, from.posY, seq), 1);
            } else {
                // keyframe times live in source media time, so in and out points frame the move
                var t0 = timeAt(clip.inPoint.seconds);
                var t1 = timeAt(Math.max(clip.inPoint.seconds, clip.outPoint.seconds - frame));
                var to = spec.to;
                scale.setTimeVarying(true);
                position.setTimeVarying(true);
                scale.addKey(t0);
                scale.setValueAtKey(t0, from.scalePct, 1);
                scale.addKey(t1);
                scale.setValueAtKey(t1, to.scalePct, 1);
                position.addKey(t0);
                position.setValueAtKey(t0, positionValue(position, from.posX, from.posY, seq), 1);
                position.addKey(t1);
                position.setValueAtKey(t1, positionValue(position, to.posX, to.posY, seq), 1);
            }
            applied++;
        }
        var first = seq.videoTracks[0].clips[0];
        return { applied: applied, issues: report, motionName: first ? (motionOf(first) || {}).displayName : null };
    };

    function findItemByPath(bin, target, depth) {
        if (!bin || depth > 6) { return null; }
        var kids = bin.children;
        for (var i = 0; i < count(kids); i++) {
            var item = kids[i];
            if (normPath(mediaPathOf(item)) === target) { return item; }
            if (item.type === 2) {
                var found = findItemByPath(item, target, depth + 1);
                if (found) { return found; }
            }
        }
        return null;
    }

    api.importCaptions = function (args) {
        var seq = findSequence(args.sequenceId);
        var file = new File(args.srtPath);
        if (!file.exists) { throw new Error('srt not found: ' + args.srtPath); }
        app.project.importFiles([file.fsName], true, app.project.getInsertionBin(), false);
        var item = findItemByPath(app.project.rootItem, normPath(file.fsName), 0);
        if (!item) { throw new Error('srt imported but not found in the project'); }
        var ok = seq.createCaptionTrack(item, 0);
        return { created: !!ok, item: item.name };
    };

    api.autoReframe = function (args) {
        var seq = findSequence(args.sequenceId);
        var name = args.name || (seq.name + ' ' + args.numerator + 'x' + args.denominator);
        var created = seq.autoReframeSequence(args.numerator || 9, args.denominator || 16, args.preset || 'default', name, false);
        if (!created) { throw new Error('auto reframe failed'); }
        return { sequenceName: created.name, sequenceId: created.sequenceID };
    };

    function lower(s) {
        return String(s || '').toLowerCase();
    }

    // effect controls show some names with a trailing colon ("Matte:"), callers say "Matte"
    function paramName(s) {
        return lower(s).replace(/\s*:\s*$/, '');
    }

    function findComponent(clip, effectName) {
        var comps = clip.components;
        var found = null;
        // last match: a freshly added effect lands at the bottom of the stack
        for (var i = 0; i < count(comps); i++) {
            if (lower(comps[i].displayName) === lower(effectName)) { found = comps[i]; }
        }
        return found;
    }

    function findProperty(comp, name, index) {
        var props = comp.properties;
        for (var i = 0; i < count(props); i++) {
            if (paramName(props[i].displayName) === paramName(name)) { return props[i]; }
        }
        // other ui languages: fall back to the documented position
        return (index !== undefined && index < count(props)) ? props[index] : null;
    }

    function qeItemAt(qeSeq, trackIndex, clipIndex) {
        var qeTrack = qeSeq.getVideoTrackAt(trackIndex);
        if (!qeTrack) { return null; }
        var k = -1;
        for (var i = 0; i < qeTrack.numItems; i++) {
            var item = qeTrack.getItemAt(i);
            // gaps between clips are items too
            if (!item || item.type === 'Empty') { continue; }
            k++;
            if (k === clipIndex) { return item; }
        }
        return null;
    }

    function sameValue(a, b) {
        if (typeof a === 'boolean' || typeof b === 'boolean') { return !!a === !!b; }
        if (typeof a === 'number' && typeof b === 'number') { return Math.abs(a - b) < 0.01; }
        return String(a) === String(b);
    }

    function trackNames(seq) {
        var names = [];
        for (var t = 0; t < count(seq.videoTracks); t++) {
            var name = null;
            try { name = seq.videoTracks[t].name; } catch (e) {}
            names.push(name);
        }
        return names;
    }

    // matte popup value = track + offset; 99 clamps to the last entry, which reveals the offset
    function matteValue(prop, matteTrack, numTracks, state) {
        if (state.offset === null || state.offset === undefined) {
            state.offset = 0;
            try {
                prop.setValue(99, true);
                var probed = prop.getValue();
                state.probe = probed;
                if (typeof probed === 'number' && probed < 99 && probed >= numTracks - 1 && probed <= numTracks + 1) {
                    state.offset = probed - numTracks;
                }
            } catch (e) { state.probe = 'error: ' + e.message; }
        }
        return matteTrack + state.offset;
    }

    function keyNest(nest, spec, matteOffset) {
        var out = { name: nest.name, id: nest.sequenceID, ok: true, applied: 0, issues: [], effects: [] };
        app.project.openSequence(nest.sequenceID);
        var active = app.project.activeSequence;
        if (!active || active.sequenceID !== nest.sequenceID) {
            // some builds only switch on assignment
            try { app.project.activeSequence = nest; } catch (e0) {}
            active = app.project.activeSequence;
        }
        if (!active || active.sequenceID !== nest.sequenceID) {
            out.ok = false;
            out.issues.push('could not make ' + nest.name + ' the active sequence');
            return out;
        }
        var qeSeq = qe.project.getActiveSequence();
        out.tracks = trackNames(nest);
        var state = { offset: (matteOffset === null || matteOffset === undefined) ? null : Number(matteOffset), probe: null };
        var effects = spec.effects || [];
        for (var i = 0; i < effects.length; i++) {
            var e = effects[i];
            var where = e.effect + ' on V' + (e.track + 1) + ' #' + e.index;
            var track = nest.videoTracks[e.track];
            var clip = track ? track.clips[e.index] : null;
            if (!clip) { out.ok = false; out.issues.push('no clip for ' + where); continue; }
            var comp = findComponent(clip, e.effect);
            if (!comp) {
                var fx = qe.project.getVideoEffectByName(e.effect);
                var qeItem = qeItemAt(qeSeq, e.track, e.index);
                if (!fx) { out.ok = false; out.issues.push('effect not found: ' + e.effect + ' (english ui expected)'); continue; }
                if (!qeItem) { out.ok = false; out.issues.push('no qe item for ' + where); continue; }
                qeItem.addVideoEffect(fx);
                // fresh dom handles, the old component list is stale
                clip = nest.videoTracks[e.track].clips[e.index];
                comp = findComponent(clip, e.effect);
            }
            if (!comp) { out.ok = false; out.issues.push('effect added but not visible: ' + where); continue; }
            var done = { where: where, params: [] };
            var params = e.params || [];
            for (var p = 0; p < params.length; p++) {
                var ps = params[p];
                var prop = findProperty(comp, ps.name, ps.index);
                if (!prop) { out.ok = false; out.issues.push('no ' + ps.name + ' on ' + where); continue; }
                var value = ps.value;
                if (ps.matteTrack !== undefined) {
                    value = matteValue(prop, ps.matteTrack, count(nest.videoTracks), state);
                    var expected = 'Video ' + ps.matteTrack;
                    if (out.tracks[ps.matteTrack - 1] && out.tracks[ps.matteTrack - 1] !== expected) {
                        out.issues.push('matte track is named ' + out.tracks[ps.matteTrack - 1] + ', premiere expects ' + expected);
                    }
                }
                var read = null;
                try {
                    prop.setValue(value, true);
                    read = prop.getValue();
                } catch (err) { out.ok = false; out.issues.push(ps.name + ' on ' + where + ': ' + err.message); continue; }
                done.params.push({ name: prop.displayName, set: value, read: read });
                if (read !== null && read !== undefined && !sameValue(read, value)) {
                    out.ok = false;
                    out.issues.push(ps.name + ' on ' + where + ' stayed at ' + read + ' instead of ' + value);
                }
            }
            out.effects.push(done);
            out.applied++;
        }
        out.matteOffset = state.offset;
        out.matteProbe = state.probe;
        return out;
    }

    api.applyMatteEffects = function (args) {
        var main = findSequence(args.sequenceId);
        app.enableQE();
        if (typeof qe === 'undefined') { throw new Error('qe dom unavailable'); }
        var report = { ok: true, nests: [] };
        var specs = args.nests || [];
        for (var i = 0; i < specs.length; i++) {
            var nest = null;
            try { nest = specs[i].id ? findSequence(specs[i].id) : null; } catch (e) {}
            if (!nest) {
                report.ok = false;
                report.nests.push({ name: specs[i].name, ok: false, issues: ['nested sequence not found after import'] });
                continue;
            }
            var r = keyNest(nest, specs[i], args.matteOffset);
            report.nests.push(r);
            if (!r.ok) { report.ok = false; }
        }
        app.project.openSequence(main.sequenceID);
        return report;
    };

    function trackList(seq, audio) {
        return audio ? seq.audioTracks : seq.videoTracks;
    }

    function clipRecord(seq, clip, audio, t, c) {
        var speed = 1;
        try { speed = clip.getSpeed() || 1; } catch (e) {}
        var inPoint = clip.inPoint.seconds;
        // premiere stores a sped nested clip's in point divided by its speed
        if (speed !== 1 && isSequenceItem(clip.projectItem)) { inPoint *= speed; }
        // premiere's out point goes stale once a script trims the clip
        var outPoint = inPoint + (clip.end.seconds - clip.start.seconds) * speed;
        return {
            audio: audio, track: t, index: c, label: (audio ? 'A' : 'V') + (t + 1) + ' #' + c, name: clip.name,
            start: clip.start.seconds, end: clip.end.seconds, inPoint: inPoint, outPoint: outPoint,
            speed: speed, mgt: !!(clip.isMGT && clip.isMGT()), mediaPath: mediaPathOf(clip.projectItem)
        };
    }

    // targets: "selection", or [{track: 'V2', index}] or [{track: 'V1', at: seconds}]
    function resolveTargets(seq, targets) {
        var out = [];
        if (!targets || targets === 'selection') {
            for (var g = 0; g < 2; g++) {
                var tracks = trackList(seq, g === 1);
                for (var t = 0; t < count(tracks); t++) {
                    var clips = tracks[t].clips;
                    for (var c = 0; c < count(clips); c++) {
                        var sel = false;
                        try { sel = clips[c].isSelected(); } catch (e) {}
                        if (sel) { out.push(clipRecord(seq, clips[c], g === 1, t, c)); }
                    }
                }
            }
            if (!out.length) { throw new Error('nothing selected in the timeline'); }
            return out;
        }
        for (var i = 0; i < targets.length; i++) {
            var spec = targets[i];
            var name = String(spec.track || 'V1');
            var audio = name.charAt(0).toUpperCase() === 'A';
            var tIndex = Number(name.substring(1)) - 1;
            var track = trackList(seq, audio)[tIndex];
            if (!track) { throw new Error('no track ' + name); }
            var found = -1;
            if ((spec.index === undefined || spec.index === null) && (spec.at === undefined || spec.at === null)) {
                for (var w = 0; w < count(track.clips); w++) { out.push(clipRecord(seq, track.clips[w], audio, tIndex, w)); }
                continue;
            }
            if (spec.index !== undefined && spec.index !== null) {
                found = Number(spec.index);
            } else {
                for (var k = 0; k < count(track.clips); k++) {
                    if (track.clips[k].start.seconds <= spec.at + 1e-6 && spec.at < track.clips[k].end.seconds) { found = k; }
                }
            }
            if (found < 0 || found >= count(track.clips)) { throw new Error('no clip on ' + name + (spec.at !== undefined ? ' at ' + spec.at + ' s' : ' #' + spec.index)); }
            out.push(clipRecord(seq, track.clips[found], audio, tIndex, found));
        }
        return out;
    }

    api.getClips = function (args) {
        var seq = findSequence(args.sequenceId);
        var fps = 254016000000 / Number(seq.timebase);
        return { sequenceId: seq.sequenceID, sequence: seq.name, fps: fps, clips: resolveTargets(seq, args.targets) };
    };

    // an in or out point the user never set reads as a time before the sequence
    function markSeconds(seq, which) {
        var t = null;
        try { t = which === 'in' ? seq.getInPointAsTime() : seq.getOutPointAsTime(); } catch (e) { t = null; }
        if (!t) {
            try { t = { seconds: Number(which === 'in' ? seq.getInPoint() : seq.getOutPoint()) }; } catch (e2) { return null; }
        }
        var s = Number(t.seconds);
        return (isFinite(s) && s >= 0) ? s : null;
    }

    api.getRange = function (args) {
        var seq = findSequence(args && args.sequenceId);
        var fps = 254016000000 / Number(seq.timebase);
        var inPoint = markSeconds(seq, 'in');
        var outPoint = markSeconds(seq, 'out');
        if (inPoint !== null && outPoint !== null && outPoint <= inPoint) { inPoint = null; outPoint = null; }
        var clips = [];
        for (var t = 0; t < count(seq.videoTracks); t++) {
            var items = seq.videoTracks[t].clips;
            for (var c = 0; c < count(items); c++) {
                var sel = false;
                try { sel = items[c].isSelected(); } catch (e) {}
                if (sel) { clips.push(clipRecord(seq, items[c], false, t, c)); }
            }
        }
        return { sequenceId: seq.sequenceID, sequence: seq.name, fps: fps, 'in': inPoint, out: outPoint, clips: clips };
    };

    function propertyFor(comp, name, index) {
        var props = comp.properties;
        var n = count(props);
        if (index !== undefined && index < n && (!name || paramName(props[index].displayName) === paramName(name))) { return props[index]; }
        if (name) {
            for (var i = 0; i < n; i++) {
                if (paramName(props[i].displayName) === paramName(name)) { return props[i]; }
            }
        }
        return (index !== undefined && index < n) ? props[index] : null;
    }

    // preset files say "AE.ADBE Geometry2" where the timeline says "AE.ADBE Geometry": versions are ignored
    function baseMatch(name) {
        return String(name || '').toLowerCase().replace(/[\s\d]+$/, '');
    }

    function componentByMatch(clip, matchName, displayName) {
        var comps = clip.components;
        var found = null;
        for (var i = 0; i < count(comps); i++) {
            if ((matchName && baseMatch(comps[i].matchName) === baseMatch(matchName)) || lower(comps[i].displayName) === lower(displayName)) { found = comps[i]; }
        }
        return found;
    }

    function qeTrackItem(qeSeq, audio, trackIndex, clipIndex) {
        var qeTrack = audio ? qeSeq.getAudioTrackAt(trackIndex) : qeSeq.getVideoTrackAt(trackIndex);
        if (!qeTrack) { return null; }
        var k = -1;
        for (var i = 0; i < qeTrack.numItems; i++) {
            var item = qeTrack.getItemAt(i);
            if (!item || item.type === 'Empty') { continue; }
            k++;
            if (k === clipIndex) { return item; }
        }
        return null;
    }

    function toPixels(ps) {
        var w = ps.layerSize[0];
        var h = ps.layerSize[1];
        var out = { name: ps.name, index: ps.index, kind: ps.kind };
        if (ps.keys) {
            out.keys = [];
            for (var k = 0; k < ps.keys.length; k++) { out.keys.push([ps.keys[k][0], [ps.keys[k][1][0] * w, ps.keys[k][1][1] * h]]); }
        } else {
            out.value = [ps.value[0] * w, ps.value[1] * h];
        }
        return out;
    }

    function setParam(prop, ps, issues, where) {
        var label = (ps.name || '#' + ps.index) + ' on ' + where;
        try {
            if (ps.keys) {
                // fresh animation: drop the static value and any old keys
                if (prop.isTimeVarying()) { prop.setTimeVarying(false); }
                prop.setTimeVarying(true);
                for (var k = 0; k < ps.keys.length; k++) {
                    var t = timeAt(ps.keys[k][0]);
                    prop.addKey(t);
                    prop.setValueAtKey(t, ps.keys[k][1], k === ps.keys.length - 1);
                }
                return ps.keys.length;
            }
            if (ps.kind === 'color') {
                var c = ps.value;
                prop.setColorValue(c[0], c[1], c[2], c[3], true);
                return 1;
            }
            if (prop.isTimeVarying && prop.isTimeVarying()) { prop.setTimeVarying(false); }
            prop.setValue(ps.value, true);
            var read = prop.getValue();
            if (read !== null && read !== undefined && !(read instanceof Array) && !sameValue(read, ps.value)) {
                issues.push(label + ' reads ' + read + ' instead of ' + ps.value);
            }
            return 1;
        } catch (e) {
            issues.push(label + ': ' + e.message);
            return 0;
        }
    }

    // a range is [a, b] (the call's factor) or [a, b, db]; dips win over boosts where they meet
    function duckGain(t, ranges, factor, ramp) {
        var lo = 1;
        var hi = 1;
        for (var i = 0; i < ranges.length; i++) {
            var a = ranges[i][0];
            var b = ranges[i][1];
            var f = ranges[i].length > 2 ? Math.pow(10, Number(ranges[i][2]) / 20) : factor;
            var v = 1;
            if (t >= a && t <= b) { v = f; }
            else if (t > a - ramp && t < a) { v = 1 + (f - 1) * (t - (a - ramp)) / ramp; }
            else if (t > b && t < b + ramp) { v = f + (1 - f) * (t - b) / ramp; }
            if (v < lo) { lo = v; }
            if (v > hi) { hi = v; }
        }
        return lo < 1 ? lo : hi;
    }

    function volumeLevel(clip) {
        var volume = componentByMatch(clip, null, 'Volume');
        return volume ? propertyFor(volume, 'Level', 1) : null;
    }

    // tracks: the first n audio tracks, or trackList: [0-based audio track numbers]
    api.duckAudio = function (args) {
        var seq = findSequence(args.sequenceId);
        var factor = Math.pow(10, Number(args.db || 0) / 20);
        var ramp = Number(args.ramp || 0.25);
        var ranges = args.ranges || [];
        var report = { clips: 0, keys: 0, issues: [] };
        var list = args.trackList;
        if (!list) {
            list = [];
            for (var n = 0; n < (args.tracks || 0); n++) { list.push(n); }
        }
        for (var li = 0; li < list.length; li++) {
            var t = Number(list[li]);
            if (t >= count(seq.audioTracks)) { report.issues.push('no audio track A' + (t + 1)); continue; }
            var clips = seq.audioTracks[t].clips;
            for (var c = 0; c < count(clips); c++) {
                var clip = clips[c];
                var cs = clip.start.seconds;
                var ce = clip.end.seconds;
                var times = [cs, ce];
                var touched = false;
                for (var r = 0; r < ranges.length; r++) {
                    if (ranges[r][0] - ramp < ce && ranges[r][1] + ramp > cs) { touched = true; }
                    var marks = [ranges[r][0] - ramp, ranges[r][0], ranges[r][1], ranges[r][1] + ramp];
                    for (var m = 0; m < 4; m++) { if (marks[m] > cs && marks[m] < ce) { times.push(marks[m]); } }
                }
                if (!touched) { continue; }
                times.sort(function (x, y) { return x - y; });
                var level = volumeLevel(clip);
                if (!level) { report.issues.push('no volume level on A' + (t + 1) + ' #' + c); continue; }
                // relative to the clip's own level, whatever unit premiere stores it in
                var base = level.getValue();
                var speed = 1;
                try { speed = clip.getSpeed() || 1; } catch (e) {}
                if (level.isTimeVarying()) { level.setTimeVarying(false); }
                level.setTimeVarying(true);
                var last = -1;
                for (var k = 0; k < times.length; k++) {
                    if (times[k] - last < 0.001) { continue; }
                    last = times[k];
                    var media = timeAt(clip.inPoint.seconds + (times[k] - cs) * speed);
                    level.addKey(media);
                    level.setValueAtKey(media, base * duckGain(times[k], ranges, factor, ramp), k === times.length - 1);
                    report.keys++;
                }
                report.clips++;
            }
        }
        return report;
    };

    // clips: [{track (0-based audio), index, db}]
    api.setVolume = function (args) {
        var seq = findSequence(args.sequenceId);
        var report = { clips: 0, issues: [] };
        var specs = args.clips || [];
        for (var i = 0; i < specs.length; i++) {
            var s = specs[i];
            var track = seq.audioTracks[s.track];
            var clip = track ? track.clips[s.index] : null;
            if (!clip) { report.issues.push('no clip A' + (s.track + 1) + ' #' + s.index); continue; }
            var level = volumeLevel(clip);
            if (!level) { report.issues.push('no volume level on A' + (s.track + 1) + ' #' + s.index); continue; }
            try {
                if (level.isTimeVarying()) { level.setTimeVarying(false); }
                level.setValue(level.getValue() * Math.pow(10, Number(s.db) / 20), true);
                report.clips++;
            } catch (e) { report.issues.push('A' + (s.track + 1) + ' #' + s.index + ': ' + e.message); }
        }
        return report;
    };

    // premiere-made templates keep their text inside a json string: only the text is swapped, the style stays
    function setMgtValue(prop, value) {
        var current = null;
        try { current = prop.getValue(); } catch (e) {}
        if (typeof value === 'string' && typeof current === 'string' && current.charAt(0) === '{' && current.indexOf('"textEditValue"') >= 0) {
            // a function replacement: "$" in the new text stays literal
            prop.setValue(current.replace(/"textEditValue"\s*:\s*"(?:[^"\\]|\\.)*"/, function () { return '"textEditValue":' + quoteString(value); }), true);
            return;
        }
        prop.setValue(value, true);
    }

    function describeMgt(clip) {
        var comp = null;
        try { comp = clip.getMGTComponent(); } catch (e) {}
        if (!comp) { return null; }
        var props = [];
        for (var p = 0; p < count(comp.properties); p++) { props.push(describeParam(comp.properties[p], p)); }
        return props;
    }

    api.addMogrt = function (args) {
        var seq = findSequence(args.sequenceId);
        var file = new File(args.path);
        if (!file.exists) { throw new Error('template not found: ' + args.path); }
        var ticks = String(Math.round(Number(args.at || 0) * ticksPerSecond));
        var item = seq.importMGT(file.fsName, ticks, Number(args.track || 0), Number(args.audioTrack || 0));
        if (!item) { throw new Error('premiere refused the template'); }
        if (args.duration) { item.end = timeAt(Number(args.at || 0) + Number(args.duration)); }
        var issues = [];
        var comp = null;
        try { comp = item.getMGTComponent(); } catch (e) {}
        var values = args.values || {};
        for (var key in values) {
            if (!values.hasOwnProperty(key)) { continue; }
            var prop = comp ? comp.properties.getParamForDisplayName(key) : null;
            if (!prop) { issues.push('no control named ' + key); continue; }
            try { setMgtValue(prop, values[key]); } catch (err) { issues.push(key + ': ' + err.message); }
        }
        // where it landed, so presets can target it
        var track = seq.videoTracks[Number(args.track || 0)];
        var index = -1;
        for (var c = 0; c < count(track.clips); c++) {
            if (Math.abs(track.clips[c].start.seconds - Number(args.at || 0)) < 0.001) { index = c; }
        }
        return { track: 'V' + (Number(args.track || 0) + 1), index: index, name: item.name, controls: comp ? describeMgt(item) : null,
                 issues: issues };
    };

    // plans: one effects list per target (keyframe times differ per clip); mode add appends, update edits the last match
    api.applyEffects = function (args) {
        var seq = findSequence(args.sequenceId);
        var restore = app.project.activeSequence;
        if (!restore || restore.sequenceID !== seq.sequenceID) { app.project.openSequence(seq.sequenceID); }
        app.enableQE();
        var qeSeq = qe.project.getActiveSequence();
        var report = { ok: true, clips: [] };
        var targets = args.targets || [];
        for (var i = 0; i < targets.length; i++) {
            var target = targets[i];
            var effects = args.plans ? args.plans[i] : args.effects;
            var out = { clip: target.label || ((target.audio ? 'A' : 'V') + (target.track + 1) + ' #' + target.index), effects: [], issues: [] };
            for (var e = 0; e < effects.length; e++) {
                var spec = effects[e];
                var where = spec.effect + ' on ' + out.clip;
                var clip = trackList(seq, spec.audio || target.audio)[target.track].clips[target.index];
                var comp = null;
                var mgt = lower(spec.effect) === 'mogrt' || lower(spec.effect) === 'graphic parameters';
                if (mgt) {
                    try { comp = clip.getMGTComponent(); } catch (e0) {}
                    if (!comp) { out.issues.push(out.clip + ' is not a motion graphics template'); continue; }
                } else if (spec.intrinsic || args.mode === 'update') { comp = componentByMatch(clip, spec.matchName, spec.effect); }
                if (!comp && args.mode === 'update' && !spec.intrinsic) { out.issues.push('no ' + spec.effect + ' to update on ' + out.clip); continue; }
                if (!comp) {
                    var fx = (spec.audio || target.audio) ? qe.project.getAudioEffectByName(spec.effect) : qe.project.getVideoEffectByName(spec.effect);
                    var item = qeTrackItem(qeSeq, spec.audio || target.audio, target.track, target.index);
                    if (!fx) { out.issues.push('effect not found: ' + spec.effect); continue; }
                    if (!item) { out.issues.push('no qe item for ' + out.clip); continue; }
                    if (spec.audio || target.audio) { item.addAudioEffect(fx); } else { item.addVideoEffect(fx); }
                    clip = trackList(seq, spec.audio || target.audio)[target.track].clips[target.index];
                    comp = componentByMatch(clip, null, spec.effect);
                }
                if (!comp) { out.issues.push('effect added but not visible: ' + where); continue; }
                var done = { effect: spec.effect, params: 0, keys: 0, skipped: spec.skipped || [] };
                var params = spec.params || [];
                // points come normalized; some builds store this effect's points in layer pixels
                var pixels = false;
                for (var q = 0; q < count(comp.properties); q++) {
                    var cur = null;
                    try { cur = comp.properties[q].getValue(); } catch (e2) {}
                    if (cur && cur.length === 2 && typeof cur[0] === 'number' && (cur[0] > 2 || cur[1] > 2)) { pixels = true; }
                }
                for (var p = 0; p < params.length; p++) {
                    var prop = propertyFor(comp, params[p].name, params[p].index);
                    if (!prop) { out.issues.push('no ' + (params[p].name || '#' + params[p].index) + ' on ' + where); continue; }
                    if (mgt && params[p].value !== undefined && !params[p].keys) {
                        try { setMgtValue(prop, params[p].value); done.params++; } catch (e1) { out.issues.push(params[p].name + ': ' + e1.message); }
                        continue;
                    }
                    var ps = params[p];
                    if (pixels && ps.kind === 'point' && ps.layerSize) { ps = toPixels(ps); }
                    var n = setParam(prop, ps, out.issues, where);
                    if (params[p].keys) { done.keys += n; } else { done.params += n; }
                }
                out.effects.push(done);
            }
            if (out.issues.length) { report.ok = false; }
            report.clips.push(out);
        }
        if (restore && restore.sequenceID !== seq.sequenceID) { app.project.openSequence(restore.sequenceID); }
        return report;
    };

    function describeParam(prop, index) {
        var d = { index: index, name: prop.displayName };
        try { d.animated = !!prop.isTimeVarying(); } catch (e0) {}
        try { d.value = prop.getValue(); } catch (e1) { d.value = 'n/a'; }
        if (d.animated) {
            try {
                var keys = prop.getKeys();
                d.keys = keys ? keys.length : 0;
                if (keys && keys.length) { d.firstKey = keys[0].seconds; d.lastKey = keys[keys.length - 1].seconds; }
            } catch (e2) {}
        }
        return d;
    }

    api.clipEffects = function (args) {
        var seq = findSequence(args.sequenceId);
        var clips = resolveTargets(seq, args.targets);
        for (var i = 0; i < clips.length; i++) {
            var rec = clips[i];
            var clip = trackList(seq, rec.audio)[rec.track].clips[rec.index];
            rec.components = [];
            for (var c = 0; c < count(clip.components); c++) {
                var comp = clip.components[c];
                var props = [];
                for (var p = 0; p < count(comp.properties) && p < 40; p++) { props.push(describeParam(comp.properties[p], p)); }
                rec.components.push({ index: c, name: comp.displayName, matchName: comp.matchName, properties: props });
            }
            if (rec.mgt) { rec.mogrtControls = describeMgt(clip); }
        }
        return { sequence: seq.name, clips: clips };
    };

    function describeComponents(clip) {
        var comps = [];
        for (var i = 0; i < count(clip.components); i++) {
            var c = clip.components[i];
            var props = [];
            for (var p = 0; p < count(c.properties) && p < 12; p++) {
                var v = null;
                try { v = c.properties[p].getValue(); } catch (e) { v = 'n/a'; }
                props.push({ name: c.properties[p].displayName, value: v });
            }
            comps.push({ name: c.displayName, matchName: c.matchName, properties: props });
        }
        return comps;
    }

    // run on a hand-made track matte setup to see how premiere really stores it
    api.diagnoseEffects = function (args) {
        var seq = (args && args.sequenceId) ? findSequence(args.sequenceId) : activeSequence();
        var out = { sequence: seq.name, videoTracks: trackNames(seq), clips: [] };
        for (var t = 0; t < count(seq.videoTracks); t++) {
            var clips = seq.videoTracks[t].clips;
            for (var c = 0; c < count(clips) && out.clips.length < 24; c++) {
                out.clips.push({ track: 'V' + (t + 1), index: c, name: clips[c].name, start: clips[c].start.seconds,
                                 components: describeComponents(clips[c]) });
            }
        }
        try {
            app.enableQE();
            var qeSeq = qe.project.getActiveSequence();
            out.qe = { activeSequence: qeSeq ? qeSeq.name : null, trackMatteKey: !!qe.project.getVideoEffectByName('Track Matte Key'),
                       gaussianBlur: !!qe.project.getVideoEffectByName('Gaussian Blur'), mosaic: !!qe.project.getVideoEffectByName('Mosaic') };
        } catch (e) { out.qe = { error: e.message }; }
        return out;
    };

    api.clearReviewMarkers = function () {
        var seq = activeSequence();
        var markers = seq.markers;
        var current = markers.getFirstMarker();
        var removed = 0;
        var guard = 0;
        while (current && guard < 100000) {
            guard++;
            var next = markers.getNextMarker(current);
            if (current.name && String(current.name).indexOf(markerPrefix) === 0) {
                markers.deleteMarker(current);
                removed++;
            }
            current = next;
        }
        return { removed: removed };
    };

    api.setPlayhead = function (args) {
        var seq = activeSequence();
        var ticks = String(Math.round(Number(args.seconds) * ticksPerSecond));
        return { ok: !!seq.setPlayerPosition(ticks), ticks: ticks };
    };

    api.exportFrame = function (args) {
        var seq = findSequence(args.sequenceId);
        var active = app.project.activeSequence;
        if (!active || active.sequenceID !== seq.sequenceID) { app.project.openSequence(seq.sequenceID); }
        app.enableQE();
        var qeSeq = qe.project.getActiveSequence();
        if (!qeSeq || !qeSeq.exportFramePNG) { throw new Error('qe frame export unavailable in this premiere'); }
        seq.setPlayerPosition(String(Math.round(Number(args.seconds) * ticksPerSecond)));
        var timecode = qeSeq.CTI ? qeSeq.CTI.timecode : null;
        if (!timecode) { throw new Error('qe did not report the playhead timecode'); }
        var ok = qeSeq.exportFramePNG(timecode, args.path);
        return { sequence: seq.name, timecode: timecode, path: args.path, ok: ok !== false, written: new File(args.path).exists };
    };

    api.saveProject = function () {
        var ok = app.project.save();
        return { path: app.project.path, ok: ok !== false };
    };

    function dropSequences(ids) {
        var out = { deleted: [], left: [] };
        if (typeof app.project.deleteSequence !== 'function') {
            for (var k = 0; k < ids.length; k++) { out.left.push({ id: ids[k] }); }
            out.error = 'deleteSequence unavailable in this premiere';
            return out;
        }
        for (var i = 0; i < ids.length; i++) {
            if (!ids[i]) { continue; }
            var seq = null;
            try { seq = findSequence(ids[i]); } catch (e) {}
            if (!seq) { continue; }
            var ok = false;
            try { ok = app.project.deleteSequence(seq) !== false; } catch (err) { ok = false; }
            (ok ? out.deleted : out.left).push({ name: seq.name, id: ids[i] });
        }
        return out;
    }

    // drop: sequences to delete once the import is in (the stage it replaces); without a path only the drop happens
    api.importProjectSequences = function (args) {
        var before = {};
        var i;
        for (i = 0; i < count(app.project.sequences); i++) { before[app.project.sequences[i].sequenceID] = true; }
        var ok = args.path ? app.project.importSequences(args.path, args.sequenceIds || []) : true;
        var added = [];
        var opened = null;
        for (i = 0; i < count(app.project.sequences); i++) {
            var s = app.project.sequences[i];
            if (!before[s.sequenceID]) { added.push({ name: s.name, id: s.sequenceID }); }
            if (args.open && s.sequenceID === args.open) { opened = s; }
        }
        if (opened) { app.project.openSequence(opened.sequenceID); }
        var out = { ok: ok !== false, sequences: added, opened: opened ? opened.sequenceID : null };
        if (args.drop && args.drop.length) {
            if (args.path && !added.length) {
                out.dropped = { deleted: [], left: [], error: 'nothing was imported, nothing dropped' };
                for (i = 0; i < args.drop.length; i++) { out.dropped.left.push({ id: args.drop[i] }); }
            } else {
                out.dropped = dropSequences(args.drop);
            }
        }
        return out;
    };

    api.deleteSequences = function (args) {
        return dropSequences(args.ids || []);
    };

    // premiere deletes items only with their bin: they go into a bin of their own that is then deleted
    api.dropMedia = function (args) {
        var out = { removed: [], left: [] };
        var used = {};
        for (var s = 0; s < count(app.project.sequences); s++) {
            var seq = app.project.sequences[s];
            var all = everyClip(seq);
            for (var c = 0; c < all.length; c++) {
                var p = normPath(mediaPathOf(all[c].clip.projectItem));
                if (p && !used[p]) { used[p] = seq.name; }
            }
        }
        var doomed = [];
        var paths = args.paths || [];
        for (var i = 0; i < paths.length; i++) {
            var item = findProjectItem(paths[i]);
            if (!item) { out.left.push({ path: paths[i], why: 'not in the project' }); continue; }
            if (used[normPath(paths[i])]) { out.left.push({ path: paths[i], name: item.name, why: 'used in ' + used[normPath(paths[i])] }); continue; }
            doomed.push(item);
        }
        if (!doomed.length) { return out; }
        var name = 'chopshop removed ' + new Date().getTime();
        var bin = app.project.rootItem.createBin(name);
        for (var k = 0; !bin && k < count(app.project.rootItem.children); k++) {
            if (app.project.rootItem.children[k].name === name) { bin = app.project.rootItem.children[k]; }
        }
        if (!bin) { throw new Error('premiere did not create the bin the files go through'); }
        for (var d = 0; d < doomed.length; d++) {
            doomed[d].moveBin(bin);
            out.removed.push(doomed[d].name);
        }
        bin.deleteBin();
        return out;
    };

    // premiere's own encoder, in process: the call returns once the file is written
    api.exportSequence = function (args) {
        var seq = findSequence(args.sequenceId);
        var preset = new File(args.presetPath || '');
        if (!args.presetPath || !preset.exists) { throw new Error('no export preset at ' + args.presetPath); }
        var enc = app.encoder || {};
        var range = args.range === 'inout'
            ? (enc.ENCODE_IN_TO_OUT !== undefined ? enc.ENCODE_IN_TO_OUT : 1)
            : (enc.ENCODE_ENTIRE !== undefined ? enc.ENCODE_ENTIRE : 0);
        var folder = new File(args.path).parent;
        if (folder && !folder.exists) { folder.create(); }
        var answer = seq.exportAsMediaDirect(args.path, preset.fsName, range);
        var out = new File(args.path);
        return { sequence: seq.name, path: args.path, answer: String(answer), written: out.exists, bytes: out.exists ? out.length : 0 };
    };

    // premiere 25: end trims the tail, start slides the head and keeps the in point, inPoint slips the content,
    // move shifts, remove never ripples, and none of them touch the linked items; insertClip and overwriteClip
    // bring the source's picture and sound. every step here moves a clip and its linked items together.

    var editEps = 1e-4;

    function timeOf(sec) { var t = new Time(); t.seconds = Number(sec); return t; }

    function fmtSec(x) { return String(Math.round(x * 1000) / 1000); }

    function everyClip(seq) {
        var out = [];
        for (var g = 0; g < 2; g++) {
            var audio = g === 1;
            var tracks = trackList(seq, audio);
            for (var t = 0; t < count(tracks); t++) {
                var clips = tracks[t].clips;
                for (var c = 0; c < count(clips); c++) {
                    out.push({ clip: clips[c], audio: audio, track: t, index: c, label: (audio ? 'A' : 'V') + (t + 1) + ' #' + c });
                }
            }
        }
        return out;
    }

    function mediaTypeOf(clip, fallbackAudio) {
        try { if (clip.mediaType) { return String(clip.mediaType); } } catch (e) {}
        return fallbackAudio ? 'Audio' : 'Video';
    }

    // every read hands out a new object for the same track item: nodeId is what stays
    function sameItem(a, b) {
        if (a === b) { return true; }
        var x = null, y = null;
        try { x = a.nodeId; y = b.nodeId; } catch (e) {}
        return !!x && String(x) === String(y);
    }

    function sameSpan(a, b) {
        return a.name === b.name && Math.abs(a.start.seconds - b.start.seconds) < editEps &&
            Math.abs(a.end.seconds - b.end.seconds) < editEps && Math.abs(a.inPoint.seconds - b.inPoint.seconds) < editEps;
    }

    // the linked list holds the clip itself, by identity or as a copy
    function linkedGroup(clip, audio) {
        var group = [clip];
        var linked = null;
        try { linked = clip.getLinkedItems(); } catch (e) {}
        var selfSeen = false;
        for (var i = 0; linked && i < count(linked); i++) {
            var l = linked[i];
            if (sameItem(l, clip)) { selfSeen = true; continue; }
            var known = false;
            for (var k = 0; k < group.length; k++) { if (sameItem(group[k], l)) { known = true; } }
            if (known) { continue; }
            group.push(l);
        }
        if (!selfSeen) {
            var type = mediaTypeOf(clip, audio);
            for (var j = 1; j < group.length; j++) {
                if (mediaTypeOf(group[j], audio) === type && sameSpan(group[j], clip)) { group.splice(j, 1); break; }
            }
        }
        return group;
    }

    function inGroup(clip, group) {
        for (var i = 0; i < group.length; i++) { if (sameItem(group[i], clip)) { return true; } }
        return false;
    }

    // towards the hole first so nothing lands on a neighbour
    function shiftFrom(seq, at, delta) {
        var list = [];
        var all = everyClip(seq);
        for (var i = 0; i < all.length; i++) { if (all[i].clip.start.seconds >= at - editEps) { list.push(all[i].clip); } }
        list.sort(function (a, b) { return delta < 0 ? a.start.seconds - b.start.seconds : b.start.seconds - a.start.seconds; });
        for (var j = 0; j < list.length; j++) { list[j].move(timeOf(delta)); }
        return list.length;
    }

    function crossing(seq, group, from, to) {
        var out = [];
        var all = everyClip(seq);
        for (var i = 0; i < all.length; i++) {
            var c = all[i].clip;
            if (inGroup(c, group)) { continue; }
            var s = c.start.seconds, e = c.end.seconds;
            if (s < to - editEps && e > from + editEps && (s < from - editEps || e > to + editEps)) { out.push(all[i].label); }
        }
        return out;
    }

    function capture(group) {
        var out = [];
        for (var i = 0; i < group.length; i++) {
            var speed = 1;
            try { speed = group[i].getSpeed() || 1; } catch (e) {}
            out.push({ clip: group[i], start: group[i].start.seconds, end: group[i].end.seconds, inPoint: group[i].inPoint.seconds, speed: speed });
        }
        return out;
    }

    function findProjectItem(mediaPath) {
        var want = normPath(mediaPath);
        function walk(item, depth) {
            if (!item || depth > 8) { return null; }
            if (normPath(mediaPathOf(item)) === want && want) { return item; }
            var kids = null;
            try { kids = item.children; } catch (e) {}
            for (var i = 0; kids && i < count(kids); i++) {
                var hit = walk(kids[i], depth + 1);
                if (hit) { return hit; }
            }
            return null;
        }
        return walk(app.project.rootItem, 0);
    }

    function shallowWith(obj, key, value) {
        var out = {};
        for (var k in obj) { if (obj.hasOwnProperty(k)) { out[k] = obj[k]; } }
        out[key] = value;
        return out;
    }

    function trimStep(seq, entry, op) {
        var main = entry.clip;
        var group = linkedGroup(main, entry.audio);
        var ripple = op.ripple !== false;
        var s0 = main.start.seconds, e0 = main.end.seconds;
        var out = { op: 'trim', clip: op.clip, ok: true, moved: 0, spanning: [] };
        var span, i;
        // source times never move: turn them into this clip's sequence times as it is now
        var speed0 = 1;
        try { speed0 = main.getSpeed() || 1; } catch (e) {}
        var in0 = main.inPoint.seconds;
        if (op.out !== undefined && op.out !== null) { op = shallowWith(op, 'end', s0 + (Number(op.out) - in0) / speed0); }
        if (op['in'] !== undefined && op['in'] !== null) { op = shallowWith(op, 'start', s0 + (Number(op['in']) - in0) / speed0); }
        if (op.end !== undefined && op.end !== null) {
            var d = Number(op.end) - e0;
            out.spanning = crossing(seq, group, e0, e0);
            if (ripple && d > 0) { out.moved += shiftFrom(seq, e0, d); }
            span = capture(group);
            for (i = 0; i < span.length; i++) { span[i].clip.end = timeOf(span[i].end + d); }
            if (ripple && d < 0) { out.moved += shiftFrom(seq, e0, d); }
        }
        if (op.start !== undefined && op.start !== null) {
            var h = Number(op.start) - s0;
            if (!out.spanning.length) { out.spanning = crossing(seq, group, s0, s0); }
            if (ripple && h < 0) { out.moved += shiftFrom(seq, s0, -h); }
            span = capture(group);
            for (i = 0; i < span.length; i++) {
                span[i].clip.start = timeOf(span[i].start + h);
                span[i].clip.inPoint = timeOf(span[i].inPoint + h * span[i].speed);
            }
            if (ripple && h > 0) { out.moved += shiftFrom(seq, s0, -h); }
        }
        return out;
    }

    function removeStep(seq, entry, op) {
        var main = entry.clip;
        var group = linkedGroup(main, entry.audio);
        var s0 = main.start.seconds, e0 = main.end.seconds;
        var out = { op: 'remove', clip: op.clip, ok: true, moved: 0, spanning: crossing(seq, group, s0, e0) };
        for (var i = 0; i < group.length; i++) { group[i].remove(false, true); }
        if (op.ripple !== false) { out.moved = shiftFrom(seq, e0, s0 - e0); }
        return out;
    }

    function placeStep(seq, op, byLabel, session) {
        var item = null;
        if (op.sameAs) {
            var shown = byLabel[op.sameAs];
            if (!shown) { throw new Error('no clip ' + op.sameAs); }
            item = shown.clip.projectItem;
        } else {
            item = findProjectItem(op.media);
            if (!item) {
                app.project.importFiles([op.media], true, app.project.getInsertionBin(), false);
                item = findProjectItem(op.media);
            }
            if (!item) { throw new Error('premiere could not import ' + op.media); }
        }
        var name = String(op.track || 'V1');
        var audio = name.charAt(0).toUpperCase() === 'A';
        var wanted = Number(name.substring(1));
        var before = count(trackList(seq, audio));
        if (!audio && wanted > before && wanted <= before + 8) {
            qeOf(seq, session).addTracks(wanted - before, before, 0);
        }
        var added = count(trackList(seq, audio)) - before;
        var track = trackList(seq, audio)[wanted - 1];
        if (!track) { throw new Error('no track ' + name + (added ? ', premiere added ' + added : '')); }
        var mode = op.op === 'overwrite' ? 'overwrite' : 'insert';
        item.setInPoint(Number(op['in']), 4);
        item.setOutPoint(Number(op.out), 4);
        try {
            if (mode === 'insert') { track.insertClip(item, Number(op.at)); } else { track.overwriteClip(item, Number(op.at)); }
        } finally {
            item.clearInPoint(4);
            item.clearOutPoint(4);
        }
        var out = { op: mode, ok: true, media: mediaPathOf(item), at: Number(op.at), length: Number(op.out) - Number(op['in']), mode: mode };
        if (added) { out.addedTracks = added; }
        return out;
    }

    function moveStep(seq, entry, op) {
        var main = entry.clip;
        var group = linkedGroup(main, entry.audio);
        var d = Number(op.to) - main.start.seconds;
        var all = everyClip(seq);
        var span = capture(group);
        for (var i = 0; i < span.length; i++) {
            var home = null;
            for (var k = 0; k < all.length && !home; k++) {
                if (sameItem(all[k].clip, span[i].clip) || (mediaTypeOf(all[k].clip, all[k].audio) === mediaTypeOf(span[i].clip, all[k].audio) && sameSpan(all[k].clip, span[i].clip))) { home = all[k]; }
            }
            if (!home) { continue; }
            var a = span[i].start + d, b = span[i].end + d;
            for (var j = 0; j < all.length; j++) {
                var o = all[j];
                if (o.audio !== home.audio || o.track !== home.track || inGroup(o.clip, group)) { continue; }
                if (o.clip.start.seconds < b - editEps && o.clip.end.seconds > a + editEps) {
                    throw new Error(o.label + ' already sits at ' + fmtSec(o.clip.start.seconds) + '-' + fmtSec(o.clip.end.seconds) + ': move it or trim it first');
                }
            }
        }
        for (var m = 0; m < group.length; m++) { group[m].move(timeOf(d)); }
        return { op: 'move', clip: op.clip, ok: true, to: Number(op.to) };
    }

    // qe only reaches the active sequence: open ours once per call, editSequence gives the user's back
    function qeOf(seq, session) {
        if (!session.qe) {
            var active = app.project.activeSequence;
            session.restore = active;
            if (!active || active.sequenceID !== seq.sequenceID) { app.project.openSequence(seq.sequenceID); }
            app.enableQE();
            session.qe = qe.project.getActiveSequence();
        }
        return session.qe;
    }

    function frameRate(seq) {
        var exact = ticksPerSecond / Number(seq.timebase);
        return { exact: exact, nominal: Math.round(exact) };
    }

    // qe takes durations as the sequence's timecode
    function timecodeOf(sec, fps) {
        var f = Math.round(sec * fps);
        function pad(n) { return (n < 10 ? '0' : '') + n; }
        return pad(Math.floor(f / fps / 3600)) + ';' + pad(Math.floor(f / fps / 60) % 60) + ';' + pad(Math.floor(f / fps) % 60) + ';' + pad(f % fps);
    }

    function placeOf(seq, clip, taken) {
        var all = everyClip(seq);
        for (var i = 0; i < all.length; i++) { if (sameItem(all[i].clip, clip)) { return all[i]; } }
        for (var j = 0; j < all.length; j++) {
            if (taken[all[j].label]) { continue; }
            if (mediaTypeOf(all[j].clip, all[j].audio) === mediaTypeOf(clip, all[j].audio) && sameSpan(all[j].clip, clip)) { return all[j]; }
        }
        return null;
    }

    function qeClipAt(qeSeq, place) {
        var qeTrack = place.audio ? qeSeq.getAudioTrackAt(place.track) : qeSeq.getVideoTrackAt(place.track);
        var start = place.clip.start.seconds;
        for (var i = 0; qeTrack && i < qeTrack.numItems; i++) {
            var item = qeTrack.getItemAt(i);
            if (!item || item.type === 'Empty') { continue; }
            try { if (Math.abs(Number(item.start.secs) - start) < 1e-3) { return item; } } catch (e) {}
        }
        return qeTrackItem(qeSeq, place.audio, place.track, place.index);
    }

    function transitionList(audio) {
        var list = audio ? qe.project.getAudioTransitionList() : qe.project.getVideoTransitionList();
        var names = [];
        for (var i = 0; list && i < list.length; i++) { names.push(String(list[i])); }
        return names;
    }

    function clearCut(seq, audio, trackIndex, at) {
        var track = trackList(seq, audio)[trackIndex];
        var removed = [];
        var list = null;
        try { list = track.transitions; } catch (e) {}
        for (var k = list ? count(list) - 1 : -1; k >= 0; k--) {
            var t = track.transitions[k];
            if (t.start.seconds <= at + editEps && t.end.seconds >= at - editEps) {
                removed.unshift((audio ? 'A' : 'V') + (trackIndex + 1) + ' ' + t.name + ' ' + fmtSec(t.start.seconds) + '-' + fmtSec(t.end.seconds));
                t.remove(false, true);
            }
        }
        return removed;
    }

    function hasCut(seq, audio, trackIndex, at) {
        var list = null;
        try { list = trackList(seq, audio)[trackIndex].transitions; } catch (e) {}
        for (var k = 0; list && k < count(list); k++) {
            if (list[k].start.seconds <= at + editEps && list[k].end.seconds >= at - editEps) { return true; }
        }
        return false;
    }

    var alignments = { start: 0, center: 0.5, end: 1 };

    function transitionStep(seq, entry, op, session) {
        var side = op.side === 'end' ? 'end' : 'start';
        var align = op.align === undefined ? 'center' : String(op.align);
        if (alignments[align] === undefined) { throw new Error('align is start, center or end, not ' + align); }
        var main = entry.clip;
        var at = side === 'start' ? main.start.seconds : main.end.seconds;
        var fps = frameRate(seq);
        var frames = Math.max(1, Math.round(Number(op.duration === undefined ? 0.5 : op.duration) * fps.nominal));
        var taken = {};
        var first = placeOf(seq, main, taken);
        taken[first.label] = true;
        var targets = [{ place: first, name: op.name || (entry.audio ? 'Constant Power' : 'Cross Dissolve') }];
        if (!entry.audio && op.audio !== false) {
            var group = linkedGroup(main, false);
            for (var g = 1; g < group.length; g++) {
                var p = placeOf(seq, group[g], taken);
                if (p && p.audio) {
                    taken[p.label] = true;
                    targets.push({ place: p, name: op.audioName || 'Constant Power' });
                }
            }
        }
        var found = [];
        for (var i = 0; i < targets.length; i++) {
            var a = targets[i].place.audio;
            var tr = a ? qe.project.getAudioTransitionByName(targets[i].name) : qe.project.getVideoTransitionByName(targets[i].name);
            if (!tr) { throw new Error('no transition named ' + targets[i].name + ': ' + transitionList(a).join(', ')); }
            found.push(tr);
        }
        var qeSeq = qeOf(seq, session);
        var out = { op: 'transition', clip: op.clip, ok: true, name: targets[0].name, side: side, at: at,
                    duration: Math.round(frames / fps.exact * 1000) / 1000, audio: [] };
        var replaced = [];
        for (var j = 0; j < targets.length; j++) {
            var place = targets[j].place;
            replaced = replaced.concat(clearCut(seq, place.audio, place.track, at));
            var item = qeClipAt(qeSeq, place);
            if (!item) { throw new Error('qe does not see ' + place.label); }
            item.addTransition(found[j], side === 'start', timecodeOf(frames / fps.nominal, fps.nominal), '00;00;00;00', alignments[align]);
            if (!hasCut(seq, place.audio, place.track, at)) { throw new Error('premiere placed no ' + targets[j].name + ' on ' + place.label + ' at ' + fmtSec(at)); }
            if (j > 0) { out.audio.push(place.label); }
        }
        if (replaced.length) { out.replaced = replaced; }
        return out;
    }

    function removeTransitionStep(seq, op) {
        var at = Number(op.at);
        var wanted = op.tracks || null;
        var removed = [];
        for (var g = 0; g < 2; g++) {
            var tracks = trackList(seq, g === 1);
            for (var t = 0; t < count(tracks); t++) {
                var name = (g === 1 ? 'A' : 'V') + (t + 1);
                var keep = !wanted;
                for (var w = 0; wanted && w < wanted.length; w++) { if (String(wanted[w]).toUpperCase() === name) { keep = true; } }
                if (keep) { removed = removed.concat(clearCut(seq, g === 1, t, at)); }
            }
        }
        if (!removed.length) { throw new Error('no transition on ' + fmtSec(at)); }
        return { op: 'removeTransition', ok: true, at: at, removed: removed };
    }

    function readState(seq) {
        var all = everyClip(seq);
        var out = { sequenceId: seq.sequenceID, name: seq.name, duration: 0, video: [], audio: [] };
        try {
            out.width = Number(seq.frameSizeHorizontal);
            out.height = Number(seq.frameSizeVertical);
            out.fps = Math.round(ticksPerSecond / Number(seq.timebase) * 1000) / 1000;
        } catch (f) {}
        var i;
        for (i = 0; i < all.length; i++) {
            var e = all[i];
            var rec = clipRecord(seq, e.clip, e.audio, e.track, e.index);
            try { rec.disabled = !!e.clip.disabled; } catch (x) {}
            rec.linked = [];
            var group = linkedGroup(e.clip, e.audio);
            var claimed = {};
            for (var g = 1; g < group.length; g++) {
                for (var k = 0; k < all.length; k++) {
                    if (k === i || claimed[k]) { continue; }
                    var other = all[k];
                    if (sameItem(other.clip, group[g]) || (mediaTypeOf(other.clip, other.audio) === mediaTypeOf(group[g], other.audio) && sameSpan(other.clip, group[g]))) {
                        claimed[k] = true;
                        rec.linked.push(other.label);
                        break;
                    }
                }
            }
            var list = e.audio ? out.audio : out.video;
            if (!list[e.track]) { list[e.track] = { name: (e.audio ? 'A' : 'V') + (e.track + 1), clips: [], gaps: [] }; }
            list[e.track].clips.push(rec);
            if (rec.end > out.duration) { out.duration = rec.end; }
        }
        for (var g2 = 0; g2 < 2; g2++) {
            var lists = g2 ? out.audio : out.video;
            var n = count(trackList(seq, g2 === 1));
            for (var t = 0; t < n; t++) {
                if (!lists[t]) { lists[t] = { name: (g2 ? 'A' : 'V') + (t + 1), clips: [], gaps: [] }; }
                var cl = lists[t].clips;
                cl.sort(function (a, b) { return a.start - b.start; });
                var cursor = 0;
                for (var c = 0; c < cl.length; c++) {
                    if (cl[c].start > cursor + editEps) { lists[t].gaps.push([Math.round(cursor * 1000) / 1000, Math.round(cl[c].start * 1000) / 1000]); }
                    if (cl[c].end > cursor) { cursor = cl[c].end; }
                }
                lists[t].transitions = [];
                var trs = null;
                try { trs = trackList(seq, g2 === 1)[t].transitions; } catch (z) {}
                for (var r = 0; trs && r < count(trs); r++) {
                    lists[t].transitions.push({ name: trs[r].name, start: trs[r].start.seconds, end: trs[r].end.seconds });
                }
            }
        }
        try {
            var a = seq.getInPointAsTime().seconds, b = seq.getOutPointAsTime().seconds;
            if (a >= 0 && b > a) { out.marks = { 'in': a, out: b }; }
        } catch (y) {}
        return out;
    }

    function duplicateSequence(seq, name) {
        var before = {};
        var i;
        for (i = 0; i < count(app.project.sequences); i++) { before[app.project.sequences[i].sequenceID] = true; }
        seq.clone();
        for (i = 0; i < count(app.project.sequences); i++) {
            var s = app.project.sequences[i];
            if (!before[s.sequenceID]) {
                if (name) { s.name = name; }
                return s;
            }
        }
        throw new Error('premiere did not copy ' + seq.name);
    }

    api.readSequence = function (args) {
        return readState(findSequence(args.sequenceId));
    };

    // labels name the clips as they are when the call starts; the first failing step stops the rest
    api.editSequence = function (args) {
        var seq = findSequence(args.sequenceId);
        if (args.copyAs) { seq = duplicateSequence(seq, String(args.copyAs)); }
        var byLabel = {};
        var start = everyClip(seq);
        for (var i = 0; i < start.length; i++) { byLabel[start[i].label] = start[i]; }
        var results = [];
        var ops = args.ops || [];
        var session = {};
        try {
            for (var j = 0; j < ops.length; j++) {
                var op = ops[j];
                try {
                    var entry = null;
                    if (op.op === 'trim' || op.op === 'remove' || op.op === 'move' || op.op === 'transition') {
                        entry = byLabel[op.clip];
                        if (!entry) { throw new Error('no clip ' + op.clip); }
                    }
                    if (op.op === 'trim') { results.push(trimStep(seq, entry, op)); }
                    else if (op.op === 'remove') { results.push(removeStep(seq, entry, op)); }
                    else if (op.op === 'move') { results.push(moveStep(seq, entry, op)); }
                    else if (op.op === 'insert' || op.op === 'overwrite') { results.push(placeStep(seq, op, byLabel, session)); }
                    else if (op.op === 'transition') { results.push(transitionStep(seq, entry, op, session)); }
                    else if (op.op === 'removeTransition') { results.push(removeTransitionStep(seq, op)); }
                    else { throw new Error('unknown step ' + op.op + ': trim, remove, move, insert, overwrite, transition or removeTransition'); }
                } catch (err) {
                    results.push({ op: op.op, clip: op.clip, ok: false, error: err.message || String(err) });
                    break;
                }
            }
        } finally {
            if (session.restore && session.restore.sequenceID !== seq.sequenceID) { app.project.openSequence(session.restore.sequenceID); }
        }
        return { results: results, state: readState(seq) };
    };

    fe.api = api;
    fe.toJson = toJson;

    fe.dispatch = function (fnName, argsJson) {
        try {
            var fn = api[fnName];
            if (!fn) { return toJson({ ok: false, error: 'unknown host function ' + fnName }); }
            var args = argsJson ? parseJson(argsJson) : {};
            return toJson({ ok: true, result: fn(args) });
        } catch (err) {
            var msg = (err && err.message) ? err.message : String(err);
            if (err && err.line) { msg += ' (host.jsx line ' + err.line + ')'; }
            return toJson({ ok: false, error: msg });
        }
    };
}(chopshop));
