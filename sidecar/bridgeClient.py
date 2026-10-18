"""talks to the open premiere panel over localhost, token from bridge.json."""

import json
import urllib.error
import urllib.request

from config import appDir


class BridgeError(RuntimeError):
    pass


def bridgeInfo() -> dict:
    path = appDir() / "bridge.json"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        raise BridgeError("premiere panel not running: open premiere, then window > extensions > chopshopMCP")


def callPremiere(fn: str, args: dict | None = None, timeout: float = 120.0):
    info = bridgeInfo()
    body = json.dumps({"fn": fn, "args": args or {}}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{int(info['port'])}/call",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Flash-Token": info["token"]},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raise BridgeError(f"bridge refused {fn}: {err.code} {err.read().decode('utf-8', 'replace')}")
    except (urllib.error.URLError, ConnectionError, TimeoutError) as err:
        raise BridgeError(f"premiere panel not reachable ({err}), is the chopshopMCP panel open?")
    if not payload.get("ok"):
        raise BridgeError(payload.get("error") or f"{fn} failed in premiere")
    return payload.get("result")
