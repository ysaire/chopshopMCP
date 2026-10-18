// thin layer over __adobe_cep__, no vendored csinterface needed

(function () {
    var cep = window.__adobe_cep__;
    var nodeRequire = window.cep_node ? window.cep_node.require : window.require;

    function evalScript(script) {
        return new Promise(function (resolve) {
            if (!cep) { return resolve('EvalScript error.'); }
            cep.evalScript(script, resolve);
        });
    }

    // es3 rejects raw line separators inside string literals
    function es3Literal(value) {
        return JSON.stringify(value).replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
    }

    function hostCall(fn, args) {
        var script = 'chopshop.dispatch(' + es3Literal(fn) + ',' + es3Literal(JSON.stringify(args || {})) + ')';
        return evalScript(script).then(function (raw) {
            if (!raw || raw === 'EvalScript error.' || raw === 'undefined') {
                return { ok: false, error: 'host.jsx did not answer (' + raw + ')' };
            }
            try { return JSON.parse(raw); } catch (e) {
                return { ok: false, error: 'bad host response: ' + String(raw).slice(0, 200) };
            }
        });
    }

    function extensionPath() {
        var raw = decodeURI(cep.getSystemPath('extension'));
        var isWin = nodeRequire('process').platform === 'win32';
        var p = raw.replace(isWin ? /^file:\/\/\// : /^file:\/\//, '');
        // install.bat uses a junction, resolve it to find ../sidecar
        try { p = nodeRequire('fs').realpathSync(p); } catch (e) {}
        return p;
    }

    function loadHostScript() {
        var jsx = nodeRequire('path').join(extensionPath(), 'jsx', 'host.jsx').replace(/\\/g, '/');
        return evalScript('$.evalFile(' + es3Literal(jsx) + ')');
    }

    function applyTheme() {
        try {
            var env = JSON.parse(cep.getHostEnvironment());
            var c = env.appSkinInfo.panelBackgroundColor.color;
            var rgb = 'rgb(' + Math.round(c.red) + ',' + Math.round(c.green) + ',' + Math.round(c.blue) + ')';
            document.documentElement.style.setProperty('--bg', rgb);
        } catch (e) {}
    }

    function onThemeChange(handler) {
        try { cep.addEventListener('com.adobe.csxs.events.ThemeColorChanged', handler); } catch (e) {}
    }

    window.flashCep = {
        nodeRequire: nodeRequire,
        evalScript: evalScript,
        hostCall: hostCall,
        extensionPath: extensionPath,
        loadHostScript: loadHostScript,
        applyTheme: applyTheme,
        onThemeChange: onThemeChange
    };
}());
