(function () {
    var fc = window.flashCep;
    var nodeRequire = fc.nodeRequire;
    var fs = nodeRequire('fs');
    var path = nodeRequire('path');
    var os = nodeRequire('os');
    var proc = nodeRequire('process');

    var appBase = proc.env.APPDATA || path.join(os.homedir(), '.config');
    var appDir = path.join(appBase, 'chopshopMCP');
    var configFile = path.join(appDir, 'config.json');
    var bridgeFile = path.join(appDir, 'bridge.json');
    var cliPath = path.join(fc.extensionPath(), '..', 'sidecar', 'cli.py');

    var defaults = {
        whisperModel: 'auto',
        language: 'auto',
        padMs: 120,
        minSilenceMs: 350,
        thresholdDb: null,
        voiceTrack: 1,
        claudeFormat: 'unboxing',
        claudeGoal: '',
        maskMode: 'native',
        maskFeather: 30,
        maskExpansion: 12,
        cutoutQuality: 'auto',
        graphicsAccent: null,
        pythonPath: 'python'
    };

    var state = { context: null, format: 'unboxing', busy: false, child: null, bridge: null, manualMedia: [] };
    var panelVersion = '1.2.1';
    var mediaTypes = ['mp4', 'mov', 'mkv', 'mxf', 'm4v', 'avi', 'wav', 'mp3', 'm4a', 'aac', 'flac'];
    var settingIds = ['maskModeSelect', 'featherInput', 'expansionInput', 'cutoutSelect', 'voiceTrackSelect', 'languageSelect',
        'whisperSelect', 'padInput', 'minSilenceInput', 'thresholdInput', 'accentText', 'pythonInput'];

    function $(id) { return document.getElementById(id); }

    function loadConfig() {
        var cfg = Object.assign({}, defaults);
        try { Object.assign(cfg, JSON.parse(fs.readFileSync(configFile, 'utf8'))); } catch (e) {}
        return cfg;
    }

    function saveConfig(patch) {
        var cfg = Object.assign(loadConfig(), patch);
        fs.mkdirSync(appDir, { recursive: true });
        fs.writeFileSync(configFile, JSON.stringify(cfg, null, 2), 'utf8');
        return cfg;
    }

    function parseAccent(text) {
        var t = String(text || '').trim().replace(/^#/, '');
        if (!t) { return null; }
        if (/^[0-9a-f]{3}$/i.test(t)) { t = t.replace(/./g, function (c) { return c + c; }); }
        return /^[0-9a-f]{6}$/i.test(t) ? '#' + t.toLowerCase() : undefined;
    }

    function setFormat(format) {
        state.format = format;
        Array.prototype.forEach.call($('formatGroup').children, function (b) {
            var on = b.dataset.format === format;
            b.classList.toggle('active', on);
            b.setAttribute('aria-checked', on ? 'true' : 'false');
        });
    }

    function fillForm(cfg) {
        setFormat(cfg.claudeFormat || 'unboxing');
        $('goalInput').value = cfg.claudeGoal || '';
        $('maskModeSelect').value = cfg.maskMode || 'native';
        $('featherInput').value = cfg.maskFeather;
        $('expansionInput').value = cfg.maskExpansion;
        $('cutoutSelect').value = cfg.cutoutQuality || 'auto';
        $('voiceTrackSelect').value = String(cfg.voiceTrack || 1);
        $('languageSelect').value = cfg.language || 'auto';
        $('whisperSelect').value = cfg.whisperModel || 'auto';
        $('padInput').value = cfg.padMs;
        $('minSilenceInput').value = cfg.minSilenceMs;
        $('thresholdInput').value = cfg.thresholdDb === null || cfg.thresholdDb === undefined ? '' : cfg.thresholdDb;
        $('accentText').value = cfg.graphicsAccent || '';
        if (cfg.graphicsAccent) { $('accentPicker').value = cfg.graphicsAccent; }
        $('pythonInput').value = cfg.pythonPath || 'python';
    }

    function numberOr(id, fallback) {
        var raw = $(id).value.trim();
        return raw === '' || isNaN(Number(raw)) ? fallback : Number(raw);
    }

    function readForm() {
        var threshold = $('thresholdInput').value.trim();
        var form = {
            claudeFormat: state.format,
            claudeGoal: $('goalInput').value.trim(),
            maskMode: $('maskModeSelect').value,
            maskFeather: numberOr('featherInput', defaults.maskFeather),
            maskExpansion: numberOr('expansionInput', defaults.maskExpansion),
            cutoutQuality: $('cutoutSelect').value,
            voiceTrack: Number($('voiceTrackSelect').value) || 1,
            language: $('languageSelect').value,
            whisperModel: $('whisperSelect').value,
            padMs: numberOr('padInput', 0),
            minSilenceMs: numberOr('minSilenceInput', defaults.minSilenceMs),
            thresholdDb: threshold === '' ? null : Number(threshold),
            pythonPath: $('pythonInput').value.trim() || 'python'
        };
        var accent = parseAccent($('accentText').value);
        if (accent !== undefined) { form.graphicsAccent = accent; }
        return form;
    }

    function saveSettings(e) {
        if (e && e.target === $('accentText')) {
            var accent = parseAccent(e.target.value);
            e.target.classList.toggle('invalid', accent === undefined);
            if (accent === undefined) { log('accent: use a hex colour like #ff5a36'); return; }
            e.target.value = accent || '';
            if (accent) { $('accentPicker').value = accent; }
        }
        saveConfig(readForm());
        log('settings saved');
    }

    function log(msg) {
        var time = new Date().toTimeString().slice(0, 8);
        var box = $('logBox');
        box.textContent = time + '  ' + msg + '\n' + box.textContent.slice(0, 8000);
    }

    function setDot(id, level, title) {
        var el = $(id);
        el.className = 'dot ' + level;
        if (title) { el.title = title; el.parentNode.title = title; }
    }

    function setProgress(pct, label) {
        $('progressBar').style.width = Math.round(Math.max(0, Math.min(1, pct)) * 100) + '%';
        if (label) { $('stageLabel').textContent = label; }
    }

    function showResult(html, isError) {
        var box = $('resultBox');
        box.hidden = false;
        box.className = 'result' + (isError ? ' error' : '');
        box.innerHTML = html;
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"]/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
        });
    }

    function fmtTime(sec) {
        sec = Math.round(sec);
        return Math.floor(sec / 60) + ':' + ('0' + (sec % 60)).slice(-2);
    }

    function setBusy(busy) {
        state.busy = busy;
        ['analyzeBtn', 'refineMaskBtn', 'silencesBtn'].forEach(function (id) { $(id).disabled = busy; });
        $('cancelBtn').hidden = !busy;
    }

    function showSettings(open) {
        $('mainView').hidden = open;
        $('settingsView').hidden = !open;
        $('settingsBtn').classList.toggle('active', open);
        $('settingsBtn').setAttribute('aria-pressed', open ? 'true' : 'false');
        window.scrollTo(0, 0);
    }

    async function host(fn, args) {
        var res = await fc.hostCall(fn, args);
        if (!res.ok) { throw new Error(res.error); }
        return res.result;
    }

    function samePath(a, b) {
        return String(a).replace(/\\/g, '/').toLowerCase() === String(b).replace(/\\/g, '/').toLowerCase();
    }

    async function refreshContext() {
        var ctx = { premiereVersion: '?', sequence: null, media: [] };
        try {
            ctx = await host('getContext');
            setDot('statusHost', 'ok', 'premiere ' + ctx.premiereVersion);
        } catch (err) {
            setDot('statusHost', 'bad', err.message);
            log('premiere: ' + err.message);
        }
        state.manualMedia.slice().reverse().forEach(function (p) {
            var existing = ctx.media.filter(function (m) { return samePath(m.path, p); })[0];
            if (existing) { ctx.media.splice(ctx.media.indexOf(existing), 1); ctx.media.unshift(existing); return; }
            ctx.media.unshift({ path: p, name: path.basename(p), source: 'file', inSequence: false });
        });
        state.context = ctx;

        var select = $('mediaSelect');
        var previous = select.value;
        select.innerHTML = '';
        ctx.media.forEach(function (m, i) {
            var opt = document.createElement('option');
            opt.value = String(i);
            var tag = m.inSequence ? '  (in timeline)' : m.nested ? '  (nested in ' + (m.via || 'sequence') + ')' : '';
            opt.textContent = m.name + tag;
            select.appendChild(opt);
        });
        if (previous && Number(previous) < ctx.media.length) { select.value = previous; }
        updateMediaHint();
    }

    function browse() {
        var fsApi = window.cep && window.cep.fs;
        if (!fsApi || !fsApi.showOpenDialog) { log('file dialog not available in this premiere build'); return; }
        var res = fsApi.showOpenDialog(false, false, 'Pick the rush', '', mediaTypes);
        if (!res || res.err || !res.data || !res.data.length) { return; }
        var picked = res.data[0];
        state.manualMedia = [picked].concat(state.manualMedia.filter(function (p) { return !samePath(p, picked); }));
        refreshContext().then(function () { $('mediaSelect').value = '0'; updateMediaHint(); });
    }

    function selectedMedia() {
        if (!state.context || !state.context.media.length) { return null; }
        return state.context.media[Number($('mediaSelect').value) || 0];
    }

    function updateMediaHint() {
        var m = selectedMedia();
        $('mediaHint').textContent = m ? m.path
            : 'No file found behind the timeline clip (nested, merged or multicam?). Browse to the rush, or Diagnose in Settings.';
    }

    async function cutSilences() {
        if (state.busy) { return; }
        await refreshContext();
        var media = selectedMedia();
        if (!media) { showResult('No rush found. Select a clip in the timeline or project panel.', true); return; }

        var cfg = saveConfig(readForm());
        var options = {
            padMs: cfg.padMs,
            minSilenceMs: cfg.minSilenceMs,
            thresholdDb: cfg.thresholdDb,
            voiceTrack: cfg.voiceTrack,
            sourceName: media.name.replace(/\.[^.]+$/, '')
        };
        if ((media.inSequence || media.onTimeline) && state.context.sequence) {
            options.ticksPerFrame = state.context.sequence.ticksPerFrame;
            options.width = state.context.sequence.width;
            options.height = state.context.sequence.height;
        }

        setBusy(true);
        $('resultBox').hidden = true;
        setProgress(0, 'Starting');
        log('cut silences on ' + media.name);
        var stageWeights = { silences: [0, 0.9], xml: [0.9, 1] };

        try {
            var result = await window.chopSidecar.runSidecar(
                ['roughCut', '--media', media.path, '--mode', 'silences', '--optionsJson', JSON.stringify(options)],
                {
                    pythonPath: cfg.pythonPath,
                    cliPath: cliPath,
                    onChild: function (child) { state.child = child; },
                    onProgress: function (evt) {
                        var w = stageWeights[evt.stage] || [0, 1];
                        var pct = evt.pct === null || evt.pct === undefined ? 0 : evt.pct;
                        setProgress(w[0] + (w[1] - w[0]) * pct, evt.message || (evt.stage + ' ' + Math.round(pct * 100) + '%'));
                        if (evt.message) { log(evt.message); }
                    }
                }
            );
            setDot('statusPython', 'ok');
            await applyResult(result);
        } catch (err) {
            setProgress(0, 'Failed');
            showResult(escapeHtml(err.message).replace(/\n/g, '<br>'), true);
            log('error: ' + err.message);
        } finally {
            state.child = null;
            setBusy(false);
        }
    }

    async function applyResult(result) {
        var s = result.stats;
        var saved = s.sourceDuration - s.keptDuration;
        var seq = await host('importSequenceXml', { xmlPath: result.xmlPath });
        var lines = ['<b>New sequence:</b> ' + escapeHtml(seq.sequenceName || result.sequenceName),
            fmtTime(s.keptDuration) + ' kept of ' + fmtTime(s.sourceDuration) +
            ' (-' + Math.round(100 * saved / Math.max(1, s.sourceDuration)) + '%), ' + s.segments + ' segments'];
        if (s.audioStreams > 1) {
            lines.push(s.audioStreams + ' audio tracks kept, silences measured on A' + s.voiceTrack + '.');
        }
        setProgress(1, 'Done');
        showResult(lines.join('<br>'));
        log('done: ' + lines.join(' | ').replace(/<[^>]+>/g, ''));
    }

    function cancel() {
        if (state.child) {
            state.child.kill();
            log('cancelled');
        }
    }

    async function clearMarkers() {
        try {
            var res = await host('clearReviewMarkers');
            log(res.removed + ' review markers removed');
            showResult(res.removed + ' review markers removed from the active timeline.');
        } catch (err) { log('clear markers: ' + err.message); }
    }

    async function checkPython(quiet) {
        var cfg = quiet ? loadConfig() : saveConfig(readForm());
        if (!fs.existsSync(cliPath)) { log('sidecar not found at ' + cliPath); setDot('statusPython', 'bad', 'sidecar missing'); return; }
        log('checking python setup');
        setDot('statusPython', 'busy', 'checking python');
        try {
            var report = await window.chopSidecar.runSidecar(['check'], { pythonPath: cfg.pythonPath, cliPath: cliPath });
            var missing = Object.keys(report).filter(function (k) { return typeof report[k] === 'string' && report[k].indexOf('missing') === 0; });
            setDot('statusPython', missing.length ? 'warn' : 'ok', 'python ' + report.python + (missing.length ? ', missing ' + missing.join(', ') : ''));
            log('python ' + report.python + (missing.length ? ', missing ' + missing.join(', ') : ', every module found'));
            if (!quiet || missing.length) {
                showResult('Python ' + report.python + (missing.length ? '<br>Missing: ' + missing.join(', ') + '<br>Run install.bat again.' : '<br>All modules found.'),
                    missing.length > 0);
            }
        } catch (err) {
            setDot('statusPython', 'bad', err.message);
            log('python: ' + err.message);
            showResult(escapeHtml(err.message), true);
        }
    }

    async function refineMask() {
        if (state.busy) { return; }
        var cfg = saveConfig(readForm());
        var range;
        try { range = await host('getRange'); } catch (err) { showResult(escapeHtml(err.message), true); return; }
        if (!range.clips.length) { showResult('Select the clip that carries the sketch in the timeline.', true); return; }
        if (range.clips.length > 1) { showResult('Select only the clip that carries the sketch.', true); return; }
        var clip = range.clips[0];
        var options = { sequenceId: range.sequenceId, track: clip.track, index: clip.index, inPoint: range['in'], outPoint: range.out };
        setBusy(true);
        $('resultBox').hidden = true;
        setProgress(0, 'Refining the sketch');
        log('refine mask on ' + clip.label + (range['in'] === null ? ' (no In/Out: the sketch\'s own keys)' : ' from ' + fmtTime(range['in']) + ' to ' + fmtTime(range.out)));
        try {
            var res = await window.chopSidecar.runSidecar(
                ['refine', '--optionsJson', JSON.stringify(options)],
                {
                    pythonPath: cfg.pythonPath,
                    cliPath: cliPath,
                    onChild: function (child) { state.child = child; },
                    onProgress: function (evt) {
                        var pct = evt.pct === null || evt.pct === undefined ? 0 : evt.pct;
                        setProgress(pct, evt.message || ('refining ' + Math.round(pct * 100) + '%'));
                        if (evt.message) { log(evt.message); }
                    }
                }
            );
            setDot('statusPython', 'ok');
            setProgress(1, 'Done');
            var lines = ['<b>' + escapeHtml(res.effect) + ' mask refined</b> on ' + escapeHtml(res.clip) + ': ' + res.frames + ' frames keyed, ' +
                fmtTime(res.range['in']) + ' to ' + fmtTime(res.range.out) + ' of the source.',
                'Sequence "' + escapeHtml(res.sequenceName) + '" replaced (' + (res.outlines && res.outlines.quality === 'video' ? 'SAM 2' : 'MobileSAM') + ').'];
            (res.warnings || []).forEach(function (w) { lines.push('<i>' + escapeHtml(w) + '</i>'); });
            showResult(lines.join('<br>'));
            log('done: ' + lines.join(' | ').replace(/<[^>]+>/g, ''));
        } catch (err) {
            setProgress(0, 'Failed');
            showResult(escapeHtml(err.message).replace(/\n/g, '<br>'), true);
            log('error: ' + err.message);
        } finally {
            state.child = null;
            setBusy(false);
        }
    }

    var formatLabels = { unboxing: 'unboxing / desk setup / asmr', gaming: 'gaming', podcast: 'podcast / interview', core: 'other' };

    async function analyzeForClaude() {
        if (state.busy) { return; }
        await refreshContext();
        var media = selectedMedia();
        if (!media) { showResult('No rush found. Select a clip, or Browse to the file.', true); return; }
        var cfg = saveConfig(readForm());
        setBusy(true);
        $('resultBox').hidden = true;
        setProgress(0, 'Starting analysis');
        log('analyze ' + media.name);
        var weights = { video: [0, 0.8], audio: [0.8, 0.9], speech: [0.9, 0.98], beats: [0.98, 1] };
        try {
            var res = await window.chopSidecar.runSidecar(
                ['perceive', '--media', media.path, '--optionsJson', JSON.stringify({ voiceTrack: cfg.voiceTrack })],
                {
                    pythonPath: cfg.pythonPath,
                    cliPath: cliPath,
                    onChild: function (child) { state.child = child; },
                    onProgress: function (evt) {
                        var w = weights[evt.stage] || [0, 1];
                        var pct = evt.pct === null || evt.pct === undefined ? 0 : evt.pct;
                        setProgress(w[0] + (w[1] - w[0]) * pct, evt.message || (evt.stage + ' ' + Math.round(pct * 100) + '%'));
                        if (evt.message) { log(evt.message); }
                    }
                }
            );
            setDot('statusPython', 'ok');
            var st = res.stats;
            setProgress(1, 'Ready for Claude');
            showResult('<b>Analysed ' + fmtTime(res.duration) + '</b>: ' + st.beats + ' beats, ' + Math.round(100 * st.idleRatio) +
                '% idle, ' + Math.round(100 * st.speechRatio) + '% speech, ' + st.shotChanges + ' hard cuts (' + st.seconds + ' s).<br>' +
                (copyText(buildPrompt(media, cfg)) ? 'Prompt copied: paste it in Claude Desktop.' : 'Click Copy prompt, then paste it in Claude Desktop.'));
        } catch (err) {
            setProgress(0, 'Failed');
            showResult(escapeHtml(err.message).replace(/\n/g, '<br>'), true);
            log('error: ' + err.message);
        } finally {
            state.child = null;
            setBusy(false);
        }
    }

    function buildPrompt(media, cfg) {
        var goal = cfg.claudeGoal || '45 s vertical flash edit + a tightened long version';
        return 'Edit the rush "' + media.name + '" with chopshopMCP.\n' +
            'File: ' + media.path + '\n' +
            'Footage: ' + formatLabels[cfg.claudeFormat || 'core'] + ' (playbook: ' + (cfg.claudeFormat || 'core') + ')\n' +
            'Goal: ' + goal + '\n' +
            'Voice track: A' + (cfg.voiceTrack || 1) + '\n' +
            'Read the playbooks, analyse, watch, log what you see, build the edits, then tell me what you kept and why.';
    }

    function copyPrompt() {
        var media = selectedMedia();
        if (!media) { showResult('Select a rush first.', true); return; }
        var cfg = saveConfig(readForm());
        showResult(copyText(buildPrompt(media, cfg)) ? 'Prompt copied, paste it in Claude Desktop.' : escapeHtml(buildPrompt(media, cfg)));
    }

    function copyText(text) {
        var area = document.createElement('textarea');
        area.value = text;
        document.body.appendChild(area);
        area.select();
        var ok = false;
        try { ok = document.execCommand('copy'); } catch (e) {}
        document.body.removeChild(area);
        return ok;
    }

    async function diagnose() {
        var cfg = loadConfig();
        var report = { panel: panelVersion, cliPath: cliPath, pythonPath: cfg.pythonPath };
        try { report.host = await host('diagnose'); } catch (e) { report.hostError = e.message; }
        try { report.context = await host('getContext'); } catch (e) { report.contextError = e.message; }
        // shows how premiere stores a track matte key set by hand
        try { report.effects = await host('diagnoseEffects'); } catch (e) { report.effectsError = e.message; }
        var media = selectedMedia();
        if (media) {
            try {
                report.probe = await window.chopSidecar.runSidecar(['probe', '--media', media.path], { pythonPath: cfg.pythonPath, cliPath: cliPath });
            } catch (e) { report.probeError = e.message; }
        }
        var text = JSON.stringify(report, null, 1);
        log(text);
        showResult(copyText(text) ? 'Diagnostics copied to the clipboard, paste them in the chat.'
            : 'Diagnostics are in the log below, select and copy them.');
    }

    async function selfTest() {
        var cfg = loadConfig();
        log('self-test: building a tiny test edit');
        try {
            var report = await window.chopSelfTest.runSelfTest({
                host: host,
                makeAssets: function () { return window.chopSidecar.runSidecar(['selftest'], { pythonPath: cfg.pythonPath, cliPath: cliPath }); },
                matteOffset: cfg.matteOffset,
                log: log
            });
            report.panel = panelVersion;
            var text = JSON.stringify(report, null, 1);
            log(text);
            showResult(report.passed + ' passed, ' + report.failed + ' failed. ' +
                (copyText(text) ? 'Report copied, paste it in the chat or an issue.' : 'Report in the log below.'));
        } catch (e) {
            showResult('Self-test could not run: ' + e.message);
        }
    }

    async function startBridge() {
        try {
            state.bridge = window.chopBridge.createBridge({
                bridgeFile: bridgeFile,
                evalHost: function (fn, args) { return fc.hostCall(fn, args); },
                reloadHost: function () { return fc.loadHostScript(); },
                reloadPanel: function () { window.location.reload(); },
                log: log
            });
            var port = await state.bridge.start();
            setDot('statusBridge', 'ok', 'claude bridge on 127.0.0.1:' + port);
            $('bridgeHint').textContent = 'Claude bridge listening on 127.0.0.1:' + port + ' (used by the MCP server).';
        } catch (err) {
            setDot('statusBridge', 'bad', err.message);
            $('bridgeHint').textContent = 'Claude bridge down: ' + err.message;
            log('bridge: ' + err.message);
        }
    }

    async function init() {
        fc.applyTheme();
        fc.onThemeChange(fc.applyTheme);
        fillForm(loadConfig());

        var ping = await fc.hostCall('ping');
        if (!ping.ok) {
            // manifest scriptpath sometimes loads late, load it by hand
            await fc.loadHostScript();
            ping = await fc.hostCall('ping');
        }
        setDot('statusHost', ping.ok ? 'ok' : 'bad', ping.ok ? 'premiere ' + ping.result.version : ping.error);

        $('settingsBtn').addEventListener('click', function () { showSettings($('settingsView').hidden); });
        $('backBtn').addEventListener('click', function () { showSettings(false); });
        $('refreshBtn').addEventListener('click', refreshContext);
        $('browseBtn').addEventListener('click', browse);
        $('mediaSelect').addEventListener('change', updateMediaHint);
        $('formatGroup').addEventListener('click', function (e) {
            var b = e.target.closest('button[data-format]');
            if (b) { setFormat(b.dataset.format); saveConfig(readForm()); }
        });
        $('goalInput').addEventListener('change', function () { saveConfig(readForm()); });
        $('analyzeBtn').addEventListener('click', analyzeForClaude);
        $('copyPromptBtn').addEventListener('click', copyPrompt);
        $('refineMaskBtn').addEventListener('click', refineMask);
        $('silencesBtn').addEventListener('click', cutSilences);
        $('clearMarkersBtn').addEventListener('click', clearMarkers);
        $('cancelBtn').addEventListener('click', cancel);
        settingIds.forEach(function (id) { $(id).addEventListener('change', saveSettings); });
        $('accentPicker').addEventListener('input', function () { $('accentText').value = $('accentPicker').value; });
        $('accentPicker').addEventListener('change', function () {
            $('accentText').value = $('accentPicker').value;
            saveSettings({ target: $('accentText') });
        });
        $('checkBtn').addEventListener('click', function () { checkPython(false); });
        $('diagnoseBtn').addEventListener('click', diagnose);
        $('selfTestBtn').addEventListener('click', selfTest);
        window.addEventListener('beforeunload', function () { if (state.bridge) { state.bridge.stop(); } });

        await startBridge();
        await refreshContext();
        checkPython(true);
    }

    init();
}());
