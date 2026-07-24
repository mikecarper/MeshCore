#!/usr/bin/env python3
"""Local mock of the WebConfig portal backend, for iterating on webui/index.html
in a real browser with no firmware, no flashing, and no paid emulator account.

It serves the real webui/index.html and implements the same /api/* contract as
src/helpers/esp32/WebConfigServer.cpp -- including the 202+reqid handshake, the
pending -> done result polling, aggregate-success reboot gating, secret masking
(********), and the IATA / owner-key / length validation the firmware enforces.
So the browser drives the actual portal JS (wizard, save/poll/reqid, effective
value handling, reboot overlay, stats, scan) against realistic responses.

It does NOT run the C++ handlers (that's what test/ gtest covers) or the
AsyncTCP transport -- it's a frontend + contract harness.

Usage:
    python3 scripts/webconfig_mock_server.py               # LAN mode (login: password)
    python3 scripts/webconfig_mock_server.py --setup       # first-boot setup wizard
    python3 scripts/webconfig_mock_server.py --port 9000 --active-slots 2
Then open http://localhost:8080/ (or the chosen port). Editing index.html and
refreshing shows changes immediately -- the page is re-read per request.

Stdlib only; no pip install.
"""

import argparse
import copy
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "..", "webui", "index.html")

SENTINEL = "********"
ADMIN_PASSWORD = "password"          # matches the default ADMIN_PASSWORD build flag
BATCH_PENDING_SECS = 0.8             # how long POST->done takes, to exercise polling
SCAN_SECS = 0.8

# Destination buffer sizes (chars, minus the NUL) -- mirrors the MQTTPrefs fields
# the firmware validates in CommonCLI_Observer.cpp.
LEN_LIMITS = {
    "name": 31, "wifi.ssid": 31, "wifi.pwd": 63, "mqtt.origin": 31,
    "mqtt.email": 63, "mqtt.ntp": 63, "timezone": 31, "snmp.community": 23,
}
SLOT_LEN_LIMITS = {"server": 63, "username": 31, "password": 63,
                   "token": 47, "topic": 95, "audience": 63}

# Preset names + what the UI must collect (mirrors handlePresets()).
PRESETS = (
    [(n, "none") for n in (
        "analyzer-us", "analyzer-eu", "nz-analyzer", "meshmapper", "waev",
        "meshomatic", "cascadiamesh", "tennmesh", "nashmesh", "ctmesh", "chimesh",
        "meshat.se", "eastidahomesh", "coloradomesh", "dutchmeshcore-1",
        "dutchmeshcore-2", "meshcore-ca-1", "meshcore-ca-2", "meshcore-fi",
        "bostonmesh", "rflab", "ipnt.uk", "flmesh", "corecomms")]
    + [("meshrank", "token"), ("inwmesh", "userpass")]
)

SCAN_NETWORKS = [
    {"ssid": "Wokwi-GUEST", "rssi": -42, "enc": False},
    {"ssid": "HomeNet", "rssi": -55, "enc": True},
    {"ssid": "HomeNet-5G", "rssi": -61, "enc": True},
    {"ssid": "Neighbor 2.4", "rssi": -78, "enc": True},
    {"ssid": "OpenGuest", "rssi": -83, "enc": False},
]


def default_config(setup_mode):
    return {
        "radio": {
            "freq": 910.525, "bw": 62.5, "sf": 7, "cr": 5, "tx": 22, "af": 1.0,
            "rxdelay": 0.0, "txdelay": 0.5, "cad": False, "rxgain": True,
            "repeat": True, "flood_max": 64, "flood_max_advert": 8,
            "flood_max_unscoped": 8, "loop_detect": "moderate",
            "name": "MockNode", "lat": 39.7392, "lon": -104.9903,
            "advert_interval": 240, "flood_advert_interval": 6,
        },
        "wifi": {
            # setup mode = unconfigured (empty ssid -> wizard); LAN mode = joined
            "ssid": "" if setup_mode else "HomeNet",
            "pwd": "" if setup_mode else "secretpw",   # stored raw; masked on GET
            "powersave": "min",
        },
        "mqtt": {
            "origin": "" if setup_mode else "MockNode", "iata": "" if setup_mode else "DEN",
            "status": True, "packets": True, "raw": False, "tx": "advert", "rx": True,
            "interval": 5, "timezone": "MST7MDT,M3.2.0,M11.1.0", "timezone_offset": -7,
            "ntp": "pool.ntp.org", "owner": "", "email": "", "snmp": False,
            "snmp_community": "public",
            "slots": [_slot() for _ in range(6)],
        },
    }


def _slot():
    return {"preset": "none", "server": "", "port": 8883, "username": "",
            "password": "", "token": "", "topic": "", "audience": ""}


class State:
    def __init__(self, args):
        self.lock = threading.Lock()
        self.setup_mode = args.setup
        self.active_slots = args.active_slots
        self.cfg = default_config(args.setup)
        # latched at AP start, like WebConfigServer::_initial_setup
        self.initial_setup = args.setup and self.cfg["wifi"]["ssid"] == ""
        self.start = time.time()
        self.session = None            # cookie token when logged in (LAN mode)
        self.batch = {"state": "idle"}
        self.scan_started = None

    # ---- auth -------------------------------------------------------------
    def is_authed(self, headers):
        if self.setup_mode:
            return True                # setup mode: proximity trust, no auth
        if not self.session:
            return False
        cookie = headers.get("Cookie", "")
        m = re.search(r"wcs=([0-9a-f]+)", cookie)
        return bool(m and m.group(1) == self.session)

    # ---- config serialization (masks secrets, like handleConfigGet) -------
    def config_json(self):
        c = copy.deepcopy(self.cfg)
        c["wifi"]["pwd"] = SENTINEL if self.cfg["wifi"]["pwd"] else ""
        for s in c["mqtt"]["slots"]:
            s["password"] = SENTINEL if s["password"] else ""
            s["token"] = SENTINEL if s["token"] else ""
        return c

    def status_json(self, authed):
        return {
            "mode": "setup" if self.setup_mode else "lan",
            "auth": authed,
            "needs_setup": self.cfg["wifi"]["ssid"] == "",
            "name": self.cfg["radio"]["name"], "node_id": "a1b2c3d4e5f60718",
            "fw": "v1.7.1-mock", "role": "Repeater", "board": "Heltec V3 (mock)",
            "uptime_s": int(time.time() - self.start),
            "runtime_slots": 6, "max_slots": 6, "active_slots": self.active_slots,
        }


# ---------------------------------------------------------------------------
# set-command application + validation (mirrors the firmware's setters enough
# to produce realistic per-field OK / Error replies for the UI chips).
# ---------------------------------------------------------------------------
BOOL_KEYS = {"cad": ("radio", "cad"), "radio.rxgain": ("radio", "rxgain"),
             "repeat": ("radio", "repeat"), "mqtt.status": ("mqtt", "status"),
             "mqtt.packets": ("mqtt", "packets"), "mqtt.raw": ("mqtt", "raw"),
             "mqtt.rx": ("mqtt", "rx"), "snmp": ("mqtt", "snmp")}
INT_KEYS = {"tx": ("radio", "tx"), "flood.max": ("radio", "flood_max"),
            "flood.max.advert": ("radio", "flood_max_advert"),
            "flood.max.unscoped": ("radio", "flood_max_unscoped"),
            "advert.interval": ("radio", "advert_interval"),
            "flood.advert.interval": ("radio", "flood_advert_interval"),
            "mqtt.interval": ("mqtt", "interval"),
            "timezone.offset": ("mqtt", "timezone_offset")}
FLOAT_KEYS = {"lat": ("radio", "lat"), "lon": ("radio", "lon"),
              "af": ("radio", "af"), "rxdelay": ("radio", "rxdelay"),
              "txdelay": ("radio", "txdelay")}
STR_KEYS = {"name": ("radio", "name"), "wifi.ssid": ("wifi", "ssid"),
            "wifi.powersave": ("wifi", "powersave"), "loop.detect": ("radio", "loop_detect"),
            "mqtt.origin": ("mqtt", "origin"), "mqtt.ntp": ("mqtt", "ntp"),
            "mqtt.email": ("mqtt", "email"), "timezone": ("mqtt", "timezone"),
            "snmp.community": ("mqtt", "snmp_community"), "mqtt.tx": ("mqtt", "tx")}
SECRET_STR_KEYS = {"wifi.pwd": ("wifi", "pwd")}


def _hex64(v):
    return len(v) == 64 and all(c in "0123456789abcdefABCDEF" for c in v)


def apply_set(cfg, key, val):
    """Return (ok, reply) and mutate cfg. Mirrors the firmware's validation for
    the fields where it matters (length, IATA, owner key, port, radio combo)."""
    # length guard for the plain string fields
    if key in LEN_LIMITS and len(val) > LEN_LIMITS[key]:
        return False, "Error: %s too long (max %d chars)" % (key, LEN_LIMITS[key])

    if key == "password":
        # Stored outside cfg: it must never appear in the /api/config GET. The
        # firmware overwrites the CLI's "password now: <secret>" echo, so the
        # reply carries no secret either.
        global ADMIN_PASSWORD
        ADMIN_PASSWORD = val
        return True, "OK"

    if key == "radio":
        try:
            f, bw, sf, cr = val.split(",")
            f, bw, sf, cr = float(f), float(bw), int(sf), int(cr)
        except ValueError:
            return False, "Error, invalid radio params"
        if not (150 <= f <= 2500 and 7 <= bw <= 500 and 5 <= sf <= 12 and 5 <= cr <= 8):
            return False, "Error, invalid radio params"
        cfg["radio"].update(freq=f, bw=bw, sf=sf, cr=cr)
        return True, "OK - reboot to apply"

    if key == "mqtt.iata":
        if val == "":
            cfg["mqtt"]["iata"] = ""
            return True, "OK - IATA cleared"
        if len(val) != 3 or not val.isalnum() or not val.isascii():
            return False, "Error: IATA code must be exactly 3 letters/digits (e.g. DEN)"
        cfg["mqtt"]["iata"] = val.upper()
        return True, "OK"

    if key == "mqtt.owner":
        if val == "":
            cfg["mqtt"]["owner"] = ""
            return True, "OK - owner key cleared"
        if not _hex64(val):
            return False, "Error: public key must be 64 hex characters (32 bytes)"
        cfg["mqtt"]["owner"] = val
        return True, "OK"

    m = re.match(r"^mqtt([1-6])\.(\w+)$", key)
    if m:
        return apply_slot_set(cfg, int(m.group(1)) - 1, m.group(2), val)

    if key in BOOL_KEYS:
        sec, f = BOOL_KEYS[key]
        cfg[sec][f] = (val == "on")
        return True, "OK"
    if key in INT_KEYS:
        sec, f = INT_KEYS[key]
        try:
            cfg[sec][f] = int(val)
        except ValueError:
            return False, "Error: expected a number"
        return True, "OK"
    if key in FLOAT_KEYS:
        sec, f = FLOAT_KEYS[key]
        try:
            cfg[sec][f] = float(val)
        except ValueError:
            return False, "Error: expected a number"
        return True, "OK"
    if key in SECRET_STR_KEYS:
        sec, f = SECRET_STR_KEYS[key]
        cfg[sec][f] = val
        return True, "OK"
    if key in STR_KEYS:
        sec, f = STR_KEYS[key]
        cfg[sec][f] = val
        return True, "OK"
    return True, "OK"   # unknown-but-allowlisted: accept (mock is lenient here)


def apply_slot_set(cfg, idx, field, val):
    slot = cfg["mqtt"]["slots"][idx]
    if field in SLOT_LEN_LIMITS and len(val) > SLOT_LEN_LIMITS[field]:
        return False, "Error: %s too long (max %d chars)" % (field, SLOT_LEN_LIMITS[field])
    if field == "port":
        try:
            p = int(val)
        except ValueError:
            return False, "Error: port must be between 1 and 65535"
        if not (1 <= p <= 65535):
            return False, "Error: port must be between 1 and 65535"
        slot["port"] = p
        return True, "OK"
    if field in ("preset", "server", "username", "password", "token", "topic", "audience"):
        slot[field] = val
        if field == "token":
            return True, "OK - slot %d token set" % (idx + 1)
        return True, "OK"
    return False, "Error: unknown slot field"


def is_secret_key(key):
    return key == "wifi.pwd" or bool(re.match(r"^mqtt[1-6]\.(password|token)$", key))


def valid_reqid(reqid):
    return isinstance(reqid, str) and bool(re.fullmatch(r"[0-9A-Fa-f]{16}", reqid))


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):   # concise one-line log
        print("  %s %s" % (self.command, self.path))

    # -- helpers --
    def _json(self, code, obj, extra_headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n else b""

    def _need_auth(self):
        if not ST.is_authed(self.headers):
            self._json(401, {"error": "auth"})
            return True
        return False

    # -- GET --
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self._serve_index()
        if path == "/api/status":
            return self._json(200, ST.status_json(ST.is_authed(self.headers)))
        if path == "/api/presets":
            return self._json(200, {"presets": [{"name": n, "needs": nd} for n, nd in PRESETS]})
        if path == "/api/config":
            if self._need_auth():
                return
            with ST.lock:
                return self._json(200, ST.config_json())
        if path == "/api/config/result":
            if self._need_auth():
                return
            return self._config_result()
        if path == "/api/stats":
            if self._need_auth():
                return
            return self._json(200, self._stats())
        if path == "/api/scan":
            if self._need_auth():
                return
            return self._scan()
        return self._json(404, {"error": "not found"})

    # -- POST --
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/login":
            return self._login()
        if path == "/api/logout":
            ST.session = None
            return self._json(200, {"ok": True}, [("Set-Cookie", "wcs=; Max-Age=0; Path=/")])
        if path == "/api/config":
            if self._need_auth():
                return
            return self._config_post()
        if path == "/api/reboot":
            if self._need_auth():
                return
            return self._json(200, {"ok": True})
        if path == "/api/portal/exit":
            return self._json(200, {"ok": True, "url": "http://localhost:%d/" % PORT})
        return self._json(404, {"error": "not found"})

    # -- endpoint impls --
    def _serve_index(self):
        try:
            with open(INDEX_HTML, "rb") as f:      # re-read each time -> live edits
                html = f.read()
        except OSError:
            self.send_error(500, "webui/index.html not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(html)

    def _login(self):
        if ST.setup_mode:
            return self._json(200, {"ok": True})
        try:
            body = json.loads(self._read_body() or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad request"})
        if body.get("password") != ADMIN_PASSWORD:
            return self._json(401, {"error": "wrong password"})
        ST.session = secrets.token_hex(16)
        return self._json(200, {"ok": True},
                          [("Set-Cookie", "wcs=%s; HttpOnly; SameSite=Lax; Path=/" % ST.session)])

    def _config_post(self):
        raw = self._read_body()
        if len(raw) > 4096:
            return self._json(413, {"error": "body too large"})
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        reqid = body.get("reqid", "")
        if not valid_reqid(reqid):
            return self._json(400, {"error": "bad reqid"})
        reboot = bool(body.get("reboot", False))
        setmap = body.get("set", {}) or {}

        # `password` maps to the top-level CLI command rather than a setter. It
        # is accepted in both modes (LAN already required a login), but first
        # onboarding cannot finish without it.
        if "password" in setmap:
            pwd = str(setmap["password"])
            if not 0 < len(pwd) <= 15 or "\r" in pwd or "\n" in pwd:
                return self._json(400, {"error": "admin password must be 1-15 characters with no line breaks"})
        elif ST.setup_mode and ST.initial_setup and (reboot or "wifi.ssid" in setmap):
            return self._json(400, {"error": "admin password required for initial setup"})

        with ST.lock:
            if ST.batch.get("state") != "idle" and ST.batch.get("reqid") == reqid:
                return self._json(202, {
                    "state": ST.batch["state"], "count": len(ST.batch.get("results", [])),
                    "reqid": reqid,
                })
            if ST.batch.get("state") == "pending":
                return self._json(409, {"error": "busy", "reqid": ST.batch.get("reqid", "")})
            # drop unchanged secrets (sentinel), like the firmware does
            entries = [(k, v) for k, v in setmap.items()
                       if not (is_secret_key(k) and v == SENTINEL)]
            if not entries and not reboot:
                return self._json(400, {"error": "no changes"})
            # apply now, but expose as pending->done to exercise polling
            results, all_ok = [], True
            for k, v in entries:
                ok, reply = apply_set(ST.cfg, k, str(v))
                if not ok:
                    all_ok = False
                results.append({"key": k, "reply": reply})
            ST.batch = {"state": "pending", "reqid": reqid, "results": results,
                        "all_ok": all_ok, "reboot": reboot,
                        "done_at": time.time() + BATCH_PENDING_SECS}
            return self._json(202, {"state": "pending", "count": len(entries), "reqid": reqid})

    def _config_result(self):
        query = parse_qs(urlsplit(self.path).query)
        reqid = query.get("reqid", [""])[0]
        if not valid_reqid(reqid):
            return self._json(400, {"error": "bad reqid"})
        with ST.lock:
            b = ST.batch
            if b.get("state") == "idle":
                return self._json(200, {"state": "idle", "reqid": reqid})
            if b.get("reqid") != reqid:
                return self._json(404, {"error": "unknown request"})
            if b["state"] == "pending" and time.time() < b["done_at"]:
                return self._json(200, {"state": "pending", "reqid": b["reqid"]})
            b["state"] = "done"      # stays readable until next POST
            return self._json(200, {
                "state": "done", "reqid": b["reqid"], "all_ok": b["all_ok"],
                "reboot": b["reboot"] and b["all_ok"], "results": b["results"],
            })

    def _scan(self):
        rescan = "rescan=1" in self.path
        now = time.time()
        if rescan or ST.scan_started is None:
            ST.scan_started = now
            return self._json(200, {"state": "scanning"})
        if now - ST.scan_started < SCAN_SECS:
            return self._json(200, {"state": "scanning"})
        return self._json(200, {"state": "done", "networks": SCAN_NETWORKS})

    def _stats(self):
        up = int(time.time() - ST.start)
        slots = []
        for i, s in enumerate(ST.cfg["mqtt"]["slots"]):
            if s["preset"] == "none":
                continue
            slots.append({"n": i + 1, "name": s["preset"], "state": "ok",
                          "ok": 100 + up, "err": 0})
        return {
            "uptime_s": up, "batt_mv": 4020, "heap_free": 142000, "heap_min": 118000,
            "heap_max_alloc": 96000, "noise": -98, "rssi": -71, "snr": 9.5,
            "airtime_s": up // 20, "rx_airtime_s": up // 8, "recv": 512 + up,
            "sent": 88 + up // 3, "rx_err": 3, "sent_flood": 40, "sent_direct": 48,
            "recv_flood": 300, "recv_direct": 212, "tx_queue": 0, "mqtt_queue": 0,
            "wifi_rssi": -58, "ip": "192.168.1.42", "slots": slots,
        }


def main():
    global ST, PORT
    ap = argparse.ArgumentParser(description="Mock WebConfig portal backend")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--setup", action="store_true", help="first-boot setup wizard mode")
    ap.add_argument("--active-slots", type=int, default=5, help="server slots to expose (2 or 5)")
    args = ap.parse_args()
    ST, PORT = State(args), args.port

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    mode = "SETUP (wizard)" if args.setup else "LAN (login: %s)" % ADMIN_PASSWORD
    print("WebConfig mock backend -- %s" % mode)
    print("  open http://localhost:%d/   (Ctrl-C to stop)" % args.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
