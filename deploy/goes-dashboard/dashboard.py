#!/home/digitaino/goes/venv/bin/python3
"""GOES-19 ground-station dashboard for mesh-wx.

Live goesrecv stats (nanomsg pub sockets 6001/6002/5002) pushed to the browser via
server-sent events every 500 ms, latest imagery + EMWIN files from goesproc, Pi health,
and a "pointing" mode that swaps the dongle over to an rtl_power SNR meter.
Runs as goes-dashboard.service on http://0.0.0.0:8080
"""
import collections
import glob
import hashlib
import ipaddress
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

sys.path.insert(0, "/home/digitaino/goes")
import pynng  # noqa: E402
import signal_monitor  # noqa: E402  (pointing-mode Monitor class)

HOME = "/home/digitaino"
IMG_ROOT = f"{HOME}/goes-images"
THUMB_DIR = f"{HOME}/goes/thumbs"
PORT = int(os.environ.get("PORT", "8080"))
# The mesh weather bot's admin portal (meshcore-weather.service). Only its
# read-only public bundle is proxied here; the portal itself stays LAN-only.
BOT_URL = os.environ.get("BOT_URL", "http://127.0.0.1:8081")
os.makedirs(THUMB_DIR, exist_ok=True)


def clean(o):
    """Replace NaN/inf (invalid in JSON) with null, recursively."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


# ----------------------------------------------------------------------------- goesrecv stats
class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.demod = {}
        self.frames = collections.deque(maxlen=4000)      # (t, ok, vit, rs)
        self.history = collections.deque(maxlen=1800)     # per-second: [t, vit_avg, packets, drops, rs]
        self.current = {}
        self.totals = {"packets": 0, "drops": 0, "rs_errors": 0}
        self.lock_since = None
        self.last_ok = 0
        self.const = []
        self.const_wanted = 0
        self.decoder_msgs = 0
        self.last_msg = 0
        for fn in (self.run_demod, self.run_decoder, self.run_const, self.summarize):
            threading.Thread(target=fn, daemon=True).start()

    def _sub(self, port):
        s = pynng.Sub0(recv_timeout=2000)
        s.subscribe(b"")
        s.dial(f"tcp://127.0.0.1:{port}", block=False)
        return s

    def run_demod(self):
        s = self._sub(6001)
        while True:
            try:
                d = json.loads(s.recv())
                with self.lock:
                    self.demod = d
                    self.last_msg = time.time()
            except pynng.Timeout:
                pass
            except Exception:  # noqa: BLE001
                time.sleep(1)

    def run_decoder(self):
        s = self._sub(6002)
        while True:
            try:
                d = json.loads(s.recv())
                ok = 1 if d.get("ok") else 0
                rs = d.get("reed_solomon_errors", -1)
                with self.lock:
                    self.frames.append((time.time(), ok, d.get("viterbi_errors", 0), rs))
                    self.decoder_msgs += 1
                    self.last_msg = time.time()
                    if ok:
                        self.totals["packets"] += 1
                        if rs > 0:
                            self.totals["rs_errors"] += rs
                    else:
                        self.totals["drops"] += 1
            except pynng.Timeout:
                pass
            except Exception:  # noqa: BLE001
                time.sleep(1)

    def run_const(self):
        # symbol samples (complex<float>) after clock recovery; only drain while someone is watching
        s = None
        while True:
            if self.const_wanted < time.time() - 5:
                if s is not None:
                    s.close()
                    s = None
                time.sleep(0.5)
                continue
            if s is None:
                s = self._sub(5002)
            try:
                m = s.recv()  # complex<int8_t> pairs (I,Q), one per symbol
                n = min(len(m) // 2, 600)
                with self.lock:
                    self.const = list(struct.unpack(f"<{2*n}b", m[:2 * n]))
                time.sleep(0.4)  # ~2 constellation frames per second; pub/sub drops the rest
            except pynng.Timeout:
                with self.lock:
                    self.const = []
            except Exception:  # noqa: BLE001
                time.sleep(1)

    def summarize(self):
        while True:
            time.sleep(1)
            now = time.time()
            with self.lock:
                recent = [f for f in self.frames if f[0] > now - 1]
                ok = [f for f in recent if f[1]]
                vit_avg = sum(f[2] for f in recent) / len(recent) if recent else None
                packets, drops = len(ok), len(recent) - len(ok)
                rs = sum(f[3] for f in ok if f[3] > 0)
                if packets:
                    self.last_ok = now
                    if self.lock_since is None:
                        self.lock_since = now
                elif now - self.last_ok > 3:
                    self.lock_since = None
                self.history.append([round(now), None if vit_avg is None else round(vit_avg), packets, drops, rs])
                self.current = {
                    "vit_avg": None if vit_avg is None else round(vit_avg),
                    "packets": packets, "drops": drops, "rs_sum": rs,
                    "locked": bool(self.lock_since), "lock_since": self.lock_since,
                    "freq": self.demod.get("frequency"), "gain": self.demod.get("gain"),
                    "omega": self.demod.get("omega"),
                    "feed_alive": now - self.last_msg < 5,
                    "totals": dict(self.totals),
                }

    def snapshot(self, with_const):
        with self.lock:
            d = dict(self.current)
            if with_const:
                self.const_wanted = time.time()
                d["const"] = self.const
            return d

    def history_list(self):
        with self.lock:
            return list(self.history)


# ----------------------------------------------------------------------------- system / files status
class Status:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {}
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            try:
                self.refresh()
            except Exception as e:  # noqa: BLE001
                with self.lock:
                    self.data = {"error": str(e)}
            time.sleep(5)

    def refresh(self):
        svc = sh(["systemctl", "is-active", "goesrecv", "goesproc", "goes-monitor"]).split("\n")
        services = dict(zip(["goesrecv", "goesproc", "goes-monitor"], svc))
        try:
            temp = int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000
        except Exception:  # noqa: BLE001
            temp = None
        du = shutil.disk_usage(HOME)
        today = time.strftime("%Y-%m-%d")
        counts = {}
        for cat in ("emwin", "goes19", "goes18", "dcs", "nws", "text"):
            counts[cat] = sum(len(f) for _, _, f in os.walk(f"{IMG_ROOT}/{cat}"))
        counts["emwin_today"] = sum(len(f) for _, _, f in os.walk(f"{IMG_ROOT}/emwin/{today}"))
        imgs = glob.glob(f"{IMG_ROOT}/goes19/**/*.jpg", recursive=True) + glob.glob(f"{IMG_ROOT}/goes18/**/*.jpg", recursive=True)
        imgs.sort(key=os.path.getmtime, reverse=True)
        latest_imgs = [{"path": os.path.relpath(p, IMG_ROOT), "name": os.path.basename(p),
                        "mtime": int(os.path.getmtime(p)), "size": os.path.getsize(p)} for p in imgs[:12]]
        em = glob.glob(f"{IMG_ROOT}/emwin/*/*")
        em.sort(key=os.path.getmtime, reverse=True)
        latest_emwin = [{"path": os.path.relpath(p, IMG_ROOT), "name": os.path.basename(p),
                         "mtime": int(os.path.getmtime(p)), "size": os.path.getsize(p)} for p in em[:15]]
        up = sh(["systemctl", "show", "goesrecv", "-p", "ActiveEnterTimestampMonotonic", "--value"])
        with self.lock:
            self.data = {"services": services, "temp": temp, "disk_free_gb": round(du.free / 1e9, 1),
                         "disk_used_pct": round(100 * (du.total - du.free) / du.total),
                         "counts": counts, "latest_images": latest_imgs, "latest_emwin": latest_emwin,
                         "host": sh(["hostname"]), "load": open("/proc/loadavg").read().split()[0],
                         "goesrecv_up_s": None if not up.isdigit() else int(time.clock_gettime(time.CLOCK_MONOTONIC) - int(up) / 1e6),
                         "time": int(time.time())}

    def get(self):
        with self.lock:
            return dict(self.data)


# ----------------------------------------------------------------------------- mode switching
class Mode:
    def __init__(self):
        self.mode = "receive"
        self.monitor = None
        self.lock = threading.Lock()
        self.msg = ""

    def point(self, gain=40.0):
        with self.lock:
            if self.mode == "point" and self.monitor:
                return
            sh(["sudo", "-n", "systemctl", "stop", "goesproc", "goesrecv"])
            time.sleep(1.5)
            self.monitor = signal_monitor.Monitor(gain=gain, bias=True)
            self.monitor.start()
            self.mode = "point"
            self.msg = "goesrecv stopped; rtl_power meter running (bias tee on)"

    def receive(self):
        with self.lock:
            if self.monitor:
                self.monitor.terminate()
                self.monitor = None
            sh(["pkill", "-x", "rtl_power"])  # exact process name only
            time.sleep(1)
            sh(["sudo", "-n", "systemctl", "start", "goesrecv", "goesproc"])
            self.mode = "receive"
            self.msg = "goesrecv + goesproc running"

    def set_gain(self, gain):
        with self.lock:
            if self.monitor:
                self.monitor.restart(gain=gain)

    def meter(self):
        with self.lock:
            return self.monitor.snapshot() if self.monitor else None


STATS = Stats()
STATUS = Status()
MODE = Mode()


# ----------------------------------------------------------------------------- mesh weather bot
class Bot:
    """Read-only view of the mesh weather bot for the public page: identity,
    how to reach it, request/reply counters and the redacted traffic feed.
    Fetched from the bot's portal on the Pi at most every 3 s no matter how
    many browsers are watching; when the bot is down the page says so."""

    def __init__(self):
        self.lock = threading.Lock()
        self.data = {"online": False, "error": "not fetched yet"}
        self.fetched = 0.0

    def get(self):
        with self.lock:
            if time.time() - self.fetched < 3:
                return dict(self.data)
        try:
            with urllib.request.urlopen(f"{BOT_URL}/api/public/bot?n=60", timeout=3) as r:
                d = json.loads(r.read())
            d["online"] = True
            d["error"] = None
        except Exception as e:  # noqa: BLE001  (bot stopped, portal restarting)
            d = {"online": False, "error": f"bot not reachable ({e.__class__.__name__})"}
        with self.lock:
            self.data = d
            self.fetched = time.time()
        return dict(d)


BOT = Bot()


def thumb_for(rel):
    src = os.path.join(IMG_ROOT, rel)
    if not os.path.isfile(src):
        return None
    key = hashlib.md5(f"{rel}:{os.path.getmtime(src)}".encode()).hexdigest()
    out = f"{THUMB_DIR}/{key}.jpg"
    if not os.path.exists(out):
        try:
            from PIL import Image
            im = Image.open(src)
            im.thumbnail((640, 640))
            im.convert("RGB").save(out, "JPEG", quality=80)
        except Exception:  # noqa: BLE001
            return src
    return out


HTML = open("/home/digitaino/goes/dashboard.html", "rb").read() if os.path.exists("/home/digitaino/goes/dashboard.html") else b"dashboard.html missing"


def is_lan_address(ip: str) -> bool:
    """Loopback or a private range (RFC 1918, link-local, IPv6 ULA), IPv4-mapped included.
    A string prefix test here once let public 172.2.x.x and 172.200+.x.x in as LAN."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_loopback or addr.is_private


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def is_public(self):
        """True for anything arriving through the Cloudflare tunnel (or from outside the LAN):
        such viewers get a read-only dashboard and every control endpoint is refused."""
        if "Cf-Ray" in self.headers or "Cf-Connecting-Ip" in self.headers or "CF-Connecting-IP" in self.headers:
            return True
        return not is_lan_address(self.client_address[0])

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype, cache="max-age=3600"):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return self._send(404, b"not found", "text/plain")
        self._send(200, body, ctype, {"Cache-Control": cache})

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        p = unquote(u.path)
        if p == "/":
            return self._send(200, HTML, "text/html; charset=utf-8")
        if p == "/api/state":
            return self._send(200, json.dumps(clean(self.state(True))).encode(), "application/json")
        if p == "/api/history":
            return self._send(200, json.dumps(STATS.history_list()).encode(), "application/json")
        if p == "/api/bot":
            return self._send(200, json.dumps(clean(BOT.get())).encode(), "application/json")
        if p == "/events":
            return self.sse()
        if p.startswith("/img/") or p.startswith("/thumb/"):
            rel = os.path.normpath(p.split("/", 2)[2])
            if rel.startswith("..") or os.path.isabs(rel):
                return self._send(403, b"no", "text/plain")
            full = os.path.join(IMG_ROOT, rel)
            if p.startswith("/thumb/"):
                full = thumb_for(rel) or full
            ext = full.rsplit(".", 1)[-1].lower()
            ctype = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
                     "txt": "text/plain; charset=utf-8"}.get(ext, "application/octet-stream")
            return self._file(full, ctype)
        self._send(404, b"not found", "text/plain")

    def do_POST(self):  # noqa: N802
        if self.is_public():
            return self._send(403, b'{"error":"read-only"}', "application/json")
        p = urlparse(self.path).path
        if p == "/mode/point":
            threading.Thread(target=MODE.point, daemon=True).start()
        elif p == "/mode/receive":
            threading.Thread(target=MODE.receive, daemon=True).start()
        elif p.startswith("/meter/gain/"):
            try:
                MODE.set_gain(float(p.rsplit("/", 1)[1]))
            except ValueError:
                pass
        elif p == "/meter/resetpeak":
            m = MODE.monitor
            if m:
                with m.lock:
                    m.peak = None
                    m.history.clear()
        else:
            return self._send(404, b"not found", "text/plain")
        self._send(200, b'{"ok":true}', "application/json")

    def state(self, full):
        d = {"mode": MODE.mode, "mode_msg": MODE.msg, "t": time.time(), "control": not self.is_public()}
        if MODE.mode == "receive":
            d["stats"] = STATS.snapshot(with_const=True)
        else:
            d["meter"] = MODE.meter()
        if full:
            d["status"] = STATUS.get()
            d["history"] = STATS.history_list()[-900:]
        return d

    def sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        n = 0
        try:
            while True:
                d = self.state(full=(n % 10 == 0))
                self.wfile.write(f"data: {json.dumps(clean(d))}\n\n".encode())
                self.wfile.flush()
                n += 1
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


if __name__ == "__main__":
    # default state is receiving: if a previous pointing session left goesrecv stopped, bring it back
    if sh(["systemctl", "is-active", "goesrecv"]) != "active":
        sh(["sudo", "-n", "systemctl", "start", "goesrecv", "goesproc"])
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    print(f"GOES dashboard on http://0.0.0.0:{PORT}", flush=True)
    srv.serve_forever()
