(function () {
    var nodeRequire = (typeof window !== 'undefined' && window.cep_node && window.cep_node.require) ||
        (typeof require === 'function' ? require : null);

    function splitCommand(pythonPath) {
        // "py -3" style launchers vs "C:\Program Files\Python312\python.exe"
        var p = String(pythonPath || 'python').trim();
        if (/\.exe$/i.test(p) || p.indexOf(' ') === -1) { return [p, []]; }
        var parts = p.split(/\s+/);
        return [parts[0], parts.slice(1)];
    }

    function runSidecar(cliArgs, opts) {
        var cp = nodeRequire('child_process');
        var cmd = splitCommand(opts.pythonPath);
        var env = Object.assign({}, nodeRequire('process').env, opts.env || {}, { PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' });

        return new Promise(function (resolve, reject) {
            var child;
            try {
                child = cp.spawn(cmd[0], cmd[1].concat([opts.cliPath]).concat(cliArgs), { env: env, windowsHide: true });
            } catch (e) { return reject(e); }

            var buffer = '';
            var stderrTail = '';
            var result = null;
            var failure = null;

            function handleLine(line) {
                line = line.trim();
                if (!line) { return; }
                var evt;
                try { evt = JSON.parse(line); } catch (e) { return; } // stray library prints
                if (evt.type === 'progress' && opts.onProgress) { opts.onProgress(evt); }
                else if (evt.type === 'result') { result = evt.data; }
                else if (evt.type === 'error') { failure = evt; }
            }

            child.stdout.on('data', function (chunk) {
                buffer += chunk.toString('utf8');
                var lines = buffer.split(/\r?\n/);
                buffer = lines.pop();
                lines.forEach(handleLine);
            });
            child.stderr.on('data', function (chunk) {
                stderrTail = (stderrTail + chunk.toString('utf8')).slice(-4000);
            });
            child.on('error', function (err) {
                reject(new Error('cannot start python (' + cmd[0] + '): ' + err.message));
            });
            child.on('close', function (code) {
                if (buffer) { handleLine(buffer); }
                if (failure) { return reject(new Error(failure.message)); }
                if (result !== null) { return resolve(result); }
                reject(new Error('sidecar exited with code ' + code + '\n' + stderrTail));
            });
            if (opts.onChild) { opts.onChild(child); }
        });
    }

    var exported = { runSidecar: runSidecar, splitCommand: splitCommand };
    if (typeof window !== 'undefined') { window.chopSidecar = exported; }
    if (typeof module === 'object' && module && module.exports) { module.exports = exported; }
}());
