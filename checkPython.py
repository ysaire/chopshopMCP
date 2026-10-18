# install helper: prints this interpreter path if it can run chopshopMCP
# --save also stores it in %appdata%\chopshopMCP\config.json so the panel uses the same python
# keep python 2 compatible syntax, old interpreters must fail cleanly here
import json
import os
import sys


def main():
    if sys.version_info < (3, 10):
        sys.stderr.write("too old: %s\n" % sys.version.split()[0])
        return 1
    exe = sys.executable
    if "--save" in sys.argv:
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
        appDir = os.path.join(base, "chopshopMCP")
        if not os.path.isdir(appDir):
            os.makedirs(appDir)
        configPath = os.path.join(appDir, "config.json")
        cfg = {}
        try:
            with open(configPath, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            pass
        cfg["pythonPath"] = exe
        with open(configPath, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    sys.stdout.write(exe + "\n")
    return 0


sys.exit(main())
