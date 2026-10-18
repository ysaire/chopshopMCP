// localhost bridge for the mcp server: token auth, no browser origins, whitelisted host functions only

(function () {
    var nodeRequire = (typeof window !== 'undefined' && window.cep_node && window.cep_node.require) ||
        (typeof require === 'function' ? require : null);

    var allowedFns = ['ping', 'getContext', 'diagnose', 'importSequenceXml', 'addMarkersForMedia', 'clearReviewMarkers', 'exportFrame',
        'setPlayhead', 'applyMotion', 'importCaptions', 'autoReframe', 'applyMatteEffects', 'diagnoseEffects',
        'getClips', 'applyEffects', 'clipEffects', 'addMogrt', 'duckAudio', 'setVolume',
        'saveProject', 'importProjectSequences', 'deleteSequences', 'getRange', 'reloadHost', 'exportSequence', 'reloadPanel', 'readSequence', 'editSequence', 'dropMedia'];
    var maxBodyBytes = 4 * 1024 * 1024;

    function createBridge(opts) {
        var http = nodeRequire('http');
        var crypto = nodeRequire('crypto');
        var fs = nodeRequire('fs');
        var path = nodeRequire('path');
        var Buffer = nodeRequire('buffer').Buffer;
        var proc = nodeRequire('process');

        var token = opts.token || crypto.randomBytes(24).toString('hex');
        var evalHost = opts.evalHost;
        var bridgeFile = opts.bridgeFile;
        var log = opts.log || function () {};
        var server = null;
        var port = null;

        function send(res, status, payload) {
            var body = JSON.stringify(payload);
            res.writeHead(status, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) });
            res.end(body);
        }

        function tokenOk(given) {
            if (typeof given !== 'string' || given.length !== token.length) { return false; }
            return crypto.timingSafeEqual(Buffer.from(given), Buffer.from(token));
        }

        function handle(req, res) {
            // a web page can reach localhost, it always sends an origin header
            if (req.headers.origin) { return send(res, 403, { ok: false, error: 'browser origins refused' }); }
            if (req.method !== 'POST' || req.url !== '/call') { return send(res, 404, { ok: false, error: 'not found' }); }
            if (String(req.headers['content-type'] || '').indexOf('application/json') !== 0) {
                return send(res, 415, { ok: false, error: 'json only' });
            }
            if (!tokenOk(req.headers['x-flash-token'])) { return send(res, 401, { ok: false, error: 'bad token' }); }

            var chunks = [];
            var size = 0;
            var aborted = false;
            req.on('data', function (chunk) {
                size += chunk.length;
                if (size > maxBodyBytes && !aborted) {
                    aborted = true;
                    send(res, 413, { ok: false, error: 'body too large' });
                    req.destroy();
                    return;
                }
                chunks.push(chunk);
            });
            req.on('end', function () {
                if (aborted) { return; }
                var msg;
                try { msg = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch (e) {
                    return send(res, 400, { ok: false, error: 'invalid json' });
                }
                if (!msg || allowedFns.indexOf(msg.fn) === -1) {
                    return send(res, 400, { ok: false, error: 'function not allowed: ' + (msg && msg.fn) });
                }
                log('claude -> ' + msg.fn);
                if (msg.fn === 'reloadHost') {
                    return Promise.resolve(opts.reloadHost ? opts.reloadHost() : null).then(function () {
                        send(res, 200, { ok: true, result: { reloaded: true } });
                    }, function (err) {
                        send(res, 500, { ok: false, error: String(err && err.message || err) });
                    });
                }
                if (msg.fn === 'reloadPanel') {
                    send(res, 200, { ok: true, result: { reloading: true } });
                    // after the answer is out: the reload tears this server down
                    return setTimeout(function () { if (opts.reloadPanel) { opts.reloadPanel(); } }, 150);
                }
                Promise.resolve(evalHost(msg.fn, msg.args || {})).then(function (out) {
                    send(res, 200, out);
                }, function (err) {
                    send(res, 500, { ok: false, error: String(err && err.message || err) });
                });
            });
        }

        function writeBridgeFile() {
            if (!bridgeFile) { return; }
            fs.mkdirSync(path.dirname(bridgeFile), { recursive: true });
            var info = { port: port, token: token, pid: proc.pid, startedAt: new Date().toISOString() };
            fs.writeFileSync(bridgeFile, JSON.stringify(info), { encoding: 'utf8', mode: 384 });
        }

        return {
            token: token,
            start: function () {
                return new Promise(function (resolve, reject) {
                    server = http.createServer(handle);
                    server.on('error', reject);
                    server.listen(opts.port || 0, '127.0.0.1', function () {
                        port = server.address().port;
                        try { writeBridgeFile(); } catch (e) { return reject(e); }
                        resolve(port);
                    });
                });
            },
            stop: function () {
                try { if (bridgeFile && fs.existsSync(bridgeFile)) { fs.unlinkSync(bridgeFile); } } catch (e) {}
                if (server) { server.close(); }
            },
            port: function () { return port; }
        };
    }

    var exported = { createBridge: createBridge, allowedFns: allowedFns };
    if (typeof window !== 'undefined') { window.chopBridge = exported; }
    if (typeof module === 'object' && module && module.exports) { module.exports = exported; }
}());
