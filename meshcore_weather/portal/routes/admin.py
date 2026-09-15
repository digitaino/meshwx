"""Admin API: the overview, the radio, the satellite receiver, the text bot
console, logs, host status, coverage, .env settings, and the request/reply
traffic feed. Everything here is operator-only except /public/bot, the
read-only bundle the goestools dashboard proxies for the public page. The
portal has no login: keep it on the LAN or gate it at the edge (see server.py)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from meshcore_weather.activity import activity_log
from meshcore_weather.config import settings
from meshcore_weather.meshcore.delivery import delivery_tracker
from meshcore_weather.portal import logbuf
from meshcore_weather.portal.sse import sse_response
from meshcore_weather.traffic import KINDS as TRAFFIC_KINDS, traffic_log

router = APIRouter()

# .env keys the portal may write. Anything else is refused.
ENV_WRITABLE = {
    "MCW_SERIAL_PORT", "MCW_SERIAL_BAUD",
    "MCW_MESHCORE_CHANNEL", "MCW_MESHWX_CHANNEL", "MCW_MESHWX_DISCOVER_CHANNEL",
    "MCW_HOME_CITIES", "MCW_HOME_RADIUS_KM", "MCW_HOME_STATES", "MCW_HOME_WFOS",
    "MCW_TIMEZONE", "MCW_TX_ENABLED", "MCW_EMWIN_SOURCE", "MCW_SDR_EMWIN_DIR",
    "MCW_SDR_POLL_INTERVAL", "MCW_SDR_DASHBOARD_URL", "MCW_LOG_LEVEL",
    "MCW_REPLY_MODE", "MCW_CHANNEL_REPLY_MAX_HOPS", "MCW_ADVERT_INTERVAL_HOURS", "MCW_PEER_BOT_PREFIX",
    "MCW_CONTACT_HOUSEKEEPING", "MCW_CONTACT_KEEP_FREE",
    "MCW_RETRANSMIT_MAX", "MCW_ECHO_WINDOW_S", "MCW_RETRANSMIT_PER_HOUR", "MCW_SCOPE_URL", "MCW_SCOPE_MODE",
    "MCW_SCOPE_MIN_OBSERVERS",
}

# Radio presets an operator can apply with one click.
RADIO_PRESETS = {
    "us_meshcore": {"label": "US MeshCore default", "freq_mhz": 910.525, "bw_khz": 62.5, "sf": 7, "cr": 5},
    "us_wide": {"label": "US 250 kHz / SF10", "freq_mhz": 910.525, "bw_khz": 250, "sf": 10, "cr": 5},
    "eu_meshcore": {"label": "EU 869.525 MHz", "freq_mhz": 869.525, "bw_khz": 62.5, "sf": 7, "cr": 5},
}


def _radio(request: Request):
    return request.app.state.bot.radio


def _bot(request: Request):
    return request.app.state.bot


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "JSON body required")
    if not isinstance(data, dict):
        raise HTTPException(400, "JSON object required")
    return data


def _write_env(updates: dict[str, str]) -> None:
    """Set keys in ./.env in place (commented or missing keys get appended)."""
    bad = set(updates) - ENV_WRITABLE
    if bad:
        raise HTTPException(400, f"not writable: {', '.join(sorted(bad))}")
    env_path = Path(".env")
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    for key, val in updates.items():
        val = str(val).strip()
        if len(val) > 200 or any(not c.isprintable() for c in val):
            raise HTTPException(400, f"{key}: value must be one printable line")
        for i, line in enumerate(lines):
            if line.startswith(f"{key}=") or line.startswith(f"# {key}="):
                lines[i] = f"{key}={val}" if val != "" else f"# {key}="
                break
        else:
            lines.append(f"{key}={val}")
    env_path.write_text("\n".join(lines) + "\n")


# -- Radio ------------------------------------------------------------------------


@router.get("/radio")
async def radio_state(request: Request) -> JSONResponse:
    radio = _radio(request)
    out = {
        "connected": radio.connected,
        "serial_port": settings.serial_port,
        "serial_baud": settings.serial_baud,
        "tx_enabled": settings.tx_enabled,
        "configured_channels": {
            "text": settings.meshcore_channel,
            "data": settings.meshwx_channel,
            "discover": settings.meshwx_discover_channel,
        },
        "presets": RADIO_PRESETS,
        "reply_mode": settings.reply_mode,
        "channel_reply_max_hops": settings.channel_reply_max_hops,
        "peer_bots": radio.peer_bots() if radio.connected and hasattr(radio, "peer_bots") else [],
        "housekeeping": {"enabled": settings.contact_housekeeping,
                         "slots": getattr(radio, "max_contacts", settings.contact_slots),
                         "keep_free": settings.contact_keep_free,
                         "last": getattr(radio, "last_housekeeping", None)},
        "device": getattr(radio, "device", {}) or {},
        "info": None,
        "channels": [],
        "error": None,
    }
    if radio.connected:
        try:
            out["info"] = await radio.info()
            out["channels"] = await radio.list_channels()
        except Exception as e:
            out["error"] = str(e)
    else:
        out["error"] = getattr(_bot(request), "_radio_last_error", None) or "radio not connected"
    return JSONResponse(out)


def _radio_call(request: Request):
    radio = _radio(request)
    if not radio.connected:
        raise HTTPException(503, "radio not connected")
    return radio


async def _run(coro):
    try:
        return await coro
    except ValueError as e:
        raise HTTPException(400, str(e))
    except ConnectionError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"radio error: {e}")


@router.post("/radio/name")
async def radio_set_name(request: Request) -> JSONResponse:
    body = await _body(request)
    await _run(_radio_call(request).set_name(str(body.get("name", ""))))
    return JSONResponse({"ok": True})


@router.post("/radio/params")
async def radio_set_params(request: Request) -> JSONResponse:
    body = await _body(request)
    if body.get("preset"):
        p = RADIO_PRESETS.get(body["preset"])
        if not p:
            raise HTTPException(400, "unknown preset")
        body = {**p}
    try:
        freq, bw, sf, cr = float(body["freq_mhz"]), float(body["bw_khz"]), int(body["sf"]), int(body["cr"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "freq_mhz, bw_khz, sf and cr are required")
    await _run(_radio_call(request).set_radio_params(freq, bw, sf, cr))
    return JSONResponse({"ok": True, "freq_mhz": freq, "bw_khz": bw, "sf": sf, "cr": cr})


@router.post("/radio/txpower")
async def radio_set_txpower(request: Request) -> JSONResponse:
    body = await _body(request)
    try:
        dbm = int(body["dbm"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "dbm required")
    await _run(_radio_call(request).set_tx_power(dbm))
    return JSONResponse({"ok": True, "dbm": dbm})


@router.post("/radio/coords")
async def radio_set_coords(request: Request) -> JSONResponse:
    body = await _body(request)
    try:
        lat, lon = float(body["lat"]), float(body["lon"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "lat and lon required")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(400, "lat/lon out of range")
    await _run(_radio_call(request).set_coords(lat, lon))
    return JSONResponse({"ok": True})


@router.post("/radio/channel")
async def radio_set_channel(request: Request) -> JSONResponse:
    body = await _body(request)
    try:
        idx = int(body["idx"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "idx required")
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "name required (use DELETE to clear a slot)")
    secret = body.get("secret") or None
    role = await _run(_radio_call(request).set_channel_name(idx, name, secret))
    note = None
    if role:
        key = {"text": "MCW_MESHCORE_CHANNEL", "data": "MCW_MESHWX_CHANNEL", "discover": "MCW_MESHWX_DISCOVER_CHANNEL"}[role]
        _write_env({key: name})
        note = f"slot {idx} is the bot's {role} channel; the bot now uses {name}"
    return JSONResponse({"ok": True, "role": role, "note": note,
                         "channels": await _radio_call(request).list_channels()})


@router.delete("/radio/channel/{idx}")
async def radio_clear_channel(request: Request, idx: int) -> JSONResponse:
    await _run(_radio_call(request).clear_channel(idx))
    return JSONResponse({"ok": True, "channels": await _radio_call(request).list_channels()})


@router.post("/radio/advert")
async def radio_advert(request: Request) -> JSONResponse:
    sent = await _run(_radio_call(request).advert_now(flood=True))
    return JSONResponse({"ok": True, "sent": sent,
                         "note": None if sent else "TX is disabled; advert suppressed"})


@router.post("/radio/reboot")
async def radio_reboot(request: Request) -> JSONResponse:
    await _run(_radio_call(request).reboot())
    return JSONResponse({"ok": True, "note": "node rebooting; the bot reconnects on its own"})


@router.post("/radio/tx")
async def radio_tx(request: Request) -> JSONResponse:
    """Flip transmit on or off. Applies immediately and persists to .env."""
    body = await _body(request)
    enabled = bool(body.get("enabled"))
    was = settings.tx_enabled
    settings.tx_enabled = enabled
    _write_env({"MCW_TX_ENABLED": "true" if enabled else "false"})
    adverted = False
    if enabled and not was and _radio(request).connected:
        # First thing on air: tell the mesh we exist so phones can DM us.
        try:
            adverted = await _radio(request).advert_now(flood=True)
        except Exception:
            adverted = False
    return JSONResponse({"ok": True, "tx_enabled": enabled, "adverted": adverted,
                         "note": "Transmit on; advert sent" if adverted else None})


@router.post("/radio/housekeep")
async def radio_housekeep(request: Request) -> JSONResponse:
    """Run contact housekeeping now (also runs after every contacts refresh)."""
    radio = _radio_call(request)
    if not settings.contact_housekeeping:
        raise HTTPException(400, "contact housekeeping is off (MCW_CONTACT_HOUSEKEEPING)")
    return JSONResponse({"ok": True, **await _run(radio.housekeep_contacts())})


@router.get("/radio/contacts")
async def radio_contacts(request: Request) -> JSONResponse:
    return JSONResponse({"contacts": await _run(_radio_call(request).contacts())})


# -- Satellite receiver -----------------------------------------------------------


async def _dashboard(path: str, method: str = "GET") -> dict | None:
    url = settings.sdr_dashboard_url.rstrip("/") + path
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.request(method, url)
            r.raise_for_status()
            return r.json() if r.headers.get("content-type", "").startswith("application/json") else {"ok": True}
    except Exception as e:
        return {"_error": str(e)}


async def _feed_stats(bot) -> dict:
    """What the EMWIN feed delivered recently, from the bot's own product list."""
    products = await bot.emwin.fetch_products()
    now = datetime.now(timezone.utc)
    hour_ago = now - timedelta(hours=1)
    last_hour = [p for p in products if p.get("timestamp") and p["timestamp"] > hour_ago]
    newest = max((p["timestamp"] for p in products if p.get("timestamp")), default=None)
    types = Counter((p.get("awips_id") or "")[:3] for p in last_hour)
    warn = sum(n for t, n in types.items() if t in {"TOR", "SVR", "FFW", "SVS", "FFS", "FLW", "FLS", "NPW", "WSW", "MWW", "SMW", "SPS", "CFW", "RFW", "EWW", "HLS", "TCV"})
    return {
        "source": settings.emwin_source,
        "directory": settings.sdr_emwin_dir if settings.emwin_source == "sdr" else None,
        "products_total": len(products),
        "products_last_hour": len(last_hour),
        "warnings_last_hour": warn,
        "newest_age_s": int((now - newest).total_seconds()) if newest else None,
        "top_types_last_hour": types.most_common(12),
    }


@router.get("/sdr")
async def sdr_state(request: Request) -> JSONResponse:
    state = await _dashboard("/api/state")
    feed = await _feed_stats(_bot(request))
    return JSONResponse({"dashboard_url": settings.sdr_dashboard_url, "receiver": state, "feed": feed})


@router.get("/sdr/history")
async def sdr_history() -> JSONResponse:
    return JSONResponse({"history": await _dashboard("/api/history")})


@router.post("/sdr/mode")
async def sdr_mode(request: Request) -> JSONResponse:
    body = await _body(request)
    mode = body.get("mode")
    if mode not in ("point", "receive"):
        raise HTTPException(400, "mode must be point or receive")
    res = await _dashboard(f"/mode/{mode}", "POST")
    if res and res.get("_error"):
        raise HTTPException(502, res["_error"])
    return JSONResponse({"ok": True, "mode": mode})


# -- Text bot console ----------------------------------------------------------------


@router.post("/console")
async def console(request: Request) -> JSONResponse:
    """Run one text command exactly as a DM would be handled, without the radio."""
    body = await _body(request)
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "text required")
    bot = _bot(request)
    t0 = time.monotonic()
    command, location = await bot._parse(text)
    # Exactly the DM path, with the browser as the "sender": one message
    # per command, "more" continues the last long reply.
    sender_key = "console:" + (request.client.host if request.client else "local")
    chunk, has_more = bot.reply_chunk(command, location, sender_key)
    traffic_log.record("console", sender=sender_key, text=text, command=command, location=location,
                       chars=len(chunk or ""), transport="console")
    return JSONResponse({
        "text": text, "command": command, "location": location,
        "reply": chunk, "has_more": has_more, "chars": len(chunk or ""),
        "ms": int((time.monotonic() - t0) * 1000),
    })


@router.get("/console/help")
async def console_help(request: Request) -> JSONResponse:
    from meshcore_weather.main import HELP_TEXT
    return JSONResponse({"help": HELP_TEXT})


# -- Request/reply traffic: what the bot sees on its channel and by DM -----------------


def _kinds_arg(kinds: str | None) -> tuple[str, ...] | None:
    if not kinds:
        return None
    out = tuple(k for k in kinds.split(",") if k in TRAFFIC_KINDS)
    return out or None


@router.get("/traffic")
async def traffic(n: int = Query(200, ge=1, le=1000), kinds: str | None = None,
                  since_id: int = Query(0, ge=0)) -> JSONResponse:
    """Recent conversation events (full detail: this is the admin side) and the counters."""
    return JSONResponse({"events": traffic_log.recent(n, kinds=_kinds_arg(kinds), since_id=since_id),
                         "stats": _traffic_stats(), "kinds": list(TRAFFIC_KINDS)})


def _traffic_stats() -> dict:
    st = traffic_log.stats()
    st["delivery"] = delivery_tracker.stats()
    return st


@router.get("/traffic/stream")
async def traffic_stream():
    """SSE stream of new conversation events."""
    return sse_response(traffic_log.subscribe())


# What "help" says, spelled out for a web page.
PUBLIC_COMMANDS = [
    {"cmd": "wx <city ST>", "what": "current conditions, today's high/low and any active warnings for a place",
     "example": "wx round rock tx"},
    {"cmd": "forecast <city ST>", "what": "the next days, one line per day", "example": "forecast austin tx"},
    {"cmd": "warn <city ST or ST>", "what": "active NWS watches, warnings and advisories", "example": "warn TX"},
    {"cmd": "storm <ST>", "what": "storm reports from the last 6 hours (hail, wind, tornado, flooding)",
     "example": "storm TX"},
    {"cmd": "rain <ST>", "what": "rainfall totals reported by stations in the state", "example": "rain TX"},
    {"cmd": "metar <ICAO>", "what": "the latest airport observation", "example": "metar KAUS"},
    {"cmd": "space", "what": "space weather: Kp, geomagnetic storm and solar alerts", "example": "space"},
    {"cmd": "more", "what": "the next page of the last long reply", "example": "more"},
    {"cmd": "help", "what": "the command list", "example": "help"},
]

_PUBLIC_RADIO_CACHE: dict = {"t": 0.0, "info": None}


async def _radio_info_cached(radio) -> dict | None:
    """The public page is polled by anyone; ask the node at most every 30 s."""
    now = time.time()
    if now - _PUBLIC_RADIO_CACHE["t"] < 30:
        return _PUBLIC_RADIO_CACHE["info"]
    info = None
    if radio.connected:
        try:
            info = await radio.info()
        except Exception:
            info = None
    _PUBLIC_RADIO_CACHE.update(t=now, info=info)
    return info


def _preset_label(info: dict | None) -> str | None:
    if not info:
        return None
    for p in RADIO_PRESETS.values():
        if (abs((info.get("radio_freq") or 0) - p["freq_mhz"]) < 0.001 and abs((info.get("radio_bw") or 0) - p["bw_khz"]) < 0.1
                and info.get("radio_sf") == p["sf"] and info.get("radio_cr") == p["cr"]):
            return p["label"]
    return None


@router.get("/public/bot")
async def public_bot(request: Request, n: int = Query(50, ge=1, le=200)) -> JSONResponse:
    """Read-only bundle for the public dashboard: who the bot is, how to reach
    it, what it answers, the request/reply counters and the recent traffic in
    its redacted (public) form. No settings, no contacts, no keys of others."""
    bot = _bot(request)
    radio = bot.radio
    info = await _radio_info_cached(radio)
    started = getattr(bot, "_started_at", None)
    from meshcore_weather.main import HELP_TEXT
    peers = radio.peer_bots() if radio.connected and hasattr(radio, "peer_bots") else []
    return JSONResponse({
        "t": time.time(),
        "bot": {
            "name": (info or {}).get("name"),
            "public_key": (info or {}).get("public_key"),
            "lat": (info or {}).get("adv_lat"), "lon": (info or {}).get("adv_lon"),
            "connected": radio.connected,
            "tx_enabled": settings.tx_enabled,
            "reply_mode": settings.reply_mode,
            "uptime_s": int(time.time() - started) if started else None,
            "products": len(bot.store._products),
            "last_advert_at": getattr(radio, "last_advert_at", 0) or None,
            "version": _git_rev(),
        },
        "radio": None if not info else {
            "freq_mhz": info.get("radio_freq"), "bw_khz": info.get("radio_bw"),
            "sf": info.get("radio_sf"), "cr": info.get("radio_cr"),
            "tx_power_dbm": info.get("tx_power"), "preset": _preset_label(info),
        },
        "channels": {"text": settings.meshcore_channel, "data": settings.meshwx_channel},
        "coverage": {"home": settings.home_cities, "radius_km": settings.home_radius_km,
                     "states": settings.home_states, "timezone": settings.timezone},
        "commands": PUBLIC_COMMANDS,
        "help": HELP_TEXT,
        "peers": [{"name": p["name"], "lat": p["lat"], "lon": p["lon"]} for p in peers],
        "stats": _traffic_stats(),
        "broadcasts": {"24h": activity_log.stats(1440), "1h": activity_log.stats(60)},
        "recent": traffic_log.recent(n, public=True),
    })


# -- Logs, host, settings -------------------------------------------------------------


@router.get("/logs")
async def logs(n: int = Query(300, ge=1, le=2000), level: str | None = None,
               cat: str | None = None, q: str | None = None) -> JSONResponse:
    return JSONResponse({"lines": logbuf.tail(n, level, cat, q), "counts": logbuf.counts()})


@router.get("/logs/stream")
async def logs_stream():
    """SSE stream of every new log line, tagged with its category."""
    return sse_response(logbuf.subscribe())


def _git_rev() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], timeout=3,
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def _host() -> dict:
    out: dict = {"hostname": os.uname().nodename}
    try:
        out["load"] = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        pass
    try:
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            mem[k] = int(v.strip().split()[0])
        out["mem_total_mb"] = mem.get("MemTotal", 0) // 1024
        out["mem_available_mb"] = mem.get("MemAvailable", 0) // 1024
    except Exception:
        pass
    du = shutil.disk_usage("/")
    out["disk_free_gb"] = round(du.free / 1e9, 1)
    out["disk_used_pct"] = int(du.used * 100 / du.total)
    try:
        out["temp_c"] = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except Exception:
        pass
    try:
        out["uptime_s"] = int(float(Path("/proc/uptime").read_text().split()[0]))
    except Exception:
        pass
    return out


def _coverage_info(bot) -> dict:
    """The coverage the broadcasts filter on, as configured right now."""
    from meshcore_weather.protocol.coverage import Coverage
    broadcaster = getattr(bot, "_broadcaster", None)
    cov = broadcaster.coverage if broadcaster is not None else None
    if cov is None:
        try:
            cov = Coverage.from_config()
        except Exception:
            cov = None
    src = (cov.sources if cov is not None else None) or {}
    empty = cov is None or cov.is_empty()
    return {
        "cities": list(src.get("cities") or []),
        "states": list(src.get("states") or []),
        "wfos": list(src.get("wfos") or []),
        "radius_km": settings.home_radius_km,
        "zones": 0 if empty else len(cov.zones),
        "summary": "No coverage filter: broadcasts cover everything the feed carries" if empty else cov.summary(),
    }


@router.get("/system")
async def system(request: Request) -> JSONResponse:
    bot = _bot(request)
    started = getattr(bot, "_started_at", None)
    safe = {k: v for k, v in settings.model_dump(mode="json").items()
            if not any(s in k for s in ("password", "key", "secret", "token"))}
    return JSONResponse({
        "bot": {
            "uptime_s": int(time.time() - started) if started else None,
            "git": _git_rev(),
            "tx_enabled": settings.tx_enabled,
            "products": len(bot.store._products),
            "radio_connected": bot.radio.connected,
            "broadcaster": bot._broadcaster is not None,
        },
        "host": _host(),
        "coverage": _coverage_info(bot),
        "settings": safe,
        "env_writable": sorted(ENV_WRITABLE),
        "live_keys": sorted(_LIVE_KEYS),
    })


def _audit_summary() -> dict:
    """The most recent scripts/audit.py run (data/audit.json), summarised."""
    import json
    path = Path(settings.data_dir) / "audit.json"
    if not path.exists():
        return {"available": False}
    try:
        d = json.loads(path.read_text())
    except Exception as e:
        return {"available": False, "error": str(e)}
    res = d.get("results", [])
    fails = [r for r in res if not r.get("ok")]
    by_check: dict = {}
    for r in res:
        c = by_check.setdefault(r["check"], {"pass": 0, "fail": 0})
        c["pass" if r.get("ok") else "fail"] += 1
    return {"available": True, "at": d.get("at"), "passed": len(res) - len(fails), "failed": len(fails),
            "by_check": by_check, "failures": fails[:20]}


@router.get("/overview")
async def overview(request: Request) -> JSONResponse:
    """Everything the Overview page and the header strip show, in one call:
    dish, feed, radio, transmit, text bot, broadcasts, problems, audit, host."""
    bot = _bot(request)
    radio = bot.radio
    receiver = await _dashboard("/api/state") or {}
    feed = await _feed_stats(bot)
    st = receiver.get("stats") or {}
    info = await _radio_info_cached(radio) or {}
    tstats = _traffic_stats()
    w1, w24 = tstats["windows"]["1h"], tstats["windows"]["24h"]
    broadcaster = getattr(bot, "_broadcaster", None)
    jobs_total = jobs_enabled = 0
    next_in = None
    if broadcaster is not None:
        cfg = broadcaster.scheduler.current_config()
        for job in cfg.jobs:
            jobs_total += 1
            if not job.enabled:
                continue
            jobs_enabled += 1
            n = broadcaster.scheduler.job_status(job.id).get("next_run_in_seconds")
            if n is not None:
                next_in = n if next_in is None else min(next_in, n)
    counts = logbuf.counts()
    started = getattr(bot, "_started_at", None)
    return JSONResponse({
        "t": time.time(),
        "satellite": {
            "reachable": not receiver.get("_error"),
            "error": receiver.get("_error"),
            "mode": receiver.get("mode"),
            "locked": bool(st.get("locked")),
            "vit_avg": st.get("vit_avg"),
            "drops": st.get("drops"),
        },
        "feed": {k: feed.get(k) for k in ("source", "products_total", "products_last_hour",
                                          "warnings_last_hour", "newest_age_s")},
        "radio": {
            "connected": radio.connected,
            "error": None if radio.connected else (getattr(bot, "_radio_last_error", None) or "not connected"),
            "name": info.get("name"),
            "freq_mhz": info.get("radio_freq"),
            "battery_mv": info.get("battery_mv"),
            "tx_enabled": settings.tx_enabled,
            "reply_mode": settings.reply_mode,
        },
        "textbot": {
            "requests_1h": w1["requests"], "replies_1h": w1["replies"], "dropped_1h": w1["dropped"],
            "requests_24h": w24["requests"], "replies_24h": w24["replies"], "dropped_24h": w24["dropped"],
            "senders_24h": w24["senders"],
            "last_request_at": tstats.get("last_request_at"), "last_reply_at": tstats.get("last_reply_at"),
            "delivery": tstats["delivery"],
        },
        "broadcasts": {
            "running": broadcaster is not None,
            "jobs_total": jobs_total, "jobs_enabled": jobs_enabled, "next_run_in_s": next_in,
            "1h": activity_log.stats(60), "24h": activity_log.stats(1440),
        },
        "problems": {"last_hour": counts.get("problems", 0)},
        "audit": _audit_summary(),
        "host": _host(),
        "bot": {"uptime_s": int(time.time() - started) if started else None, "git": _git_rev()},
        "recent": traffic_log.recent(12),
    })


# Keys the running bot can take on board without a restart.
_LIVE_KEYS = {"MCW_TIMEZONE", "MCW_LOG_LEVEL", "MCW_HOME_CITIES", "MCW_HOME_RADIUS_KM",
              "MCW_HOME_STATES", "MCW_HOME_WFOS", "MCW_SERIAL_PORT", "MCW_SERIAL_BAUD", "MCW_TX_ENABLED",
              "MCW_REPLY_MODE", "MCW_CHANNEL_REPLY_MAX_HOPS", "MCW_ADVERT_INTERVAL_HOURS", "MCW_PEER_BOT_PREFIX",
              "MCW_CONTACT_HOUSEKEEPING", "MCW_CONTACT_KEEP_FREE", "MCW_SDR_DASHBOARD_URL",
              "MCW_RETRANSMIT_MAX", "MCW_ECHO_WINDOW_S", "MCW_RETRANSMIT_PER_HOUR", "MCW_SCOPE_URL", "MCW_SCOPE_MODE",
              "MCW_SCOPE_MIN_OBSERVERS"}

# Value checks, run before anything touches .env: a bad value must never be
# persisted, because the next start would refuse the file.
_INT_KEYS = {"MCW_SERIAL_BAUD", "MCW_HOME_RADIUS_KM", "MCW_SDR_POLL_INTERVAL", "MCW_CHANNEL_REPLY_MAX_HOPS",
             "MCW_ADVERT_INTERVAL_HOURS", "MCW_CONTACT_KEEP_FREE", "MCW_RETRANSMIT_MAX", "MCW_RETRANSMIT_PER_HOUR",
             "MCW_SCOPE_MIN_OBSERVERS"}
_FLOAT_KEYS = {"MCW_ECHO_WINDOW_S"}
_BOOL_KEYS = {"MCW_TX_ENABLED", "MCW_CONTACT_HOUSEKEEPING"}
_CHOICES = {"MCW_REPLY_MODE": ("dm", "channel", "dm_only"), "MCW_EMWIN_SOURCE": ("sdr", "internet"),
            "MCW_LOG_LEVEL": ("DEBUG", "INFO", "WARNING", "ERROR"), "MCW_SCOPE_MODE": ("stats", "decide")}
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


def _validate_updates(updates: dict[str, str]) -> None:
    for key, val in updates.items():
        if val == "":
            continue                      # empty = comment the key out, back to the default
        if key in _INT_KEYS and not val.lstrip("-").isdigit():
            raise HTTPException(400, f"{key}: must be a whole number")
        if key in _INT_KEYS and int(val) < 0:
            raise HTTPException(400, f"{key}: must not be negative")
        if key in _FLOAT_KEYS:
            try:
                if float(val) <= 0:
                    raise ValueError
            except ValueError:
                raise HTTPException(400, f"{key}: must be a number above 0")
        if key == "MCW_SCOPE_URL" and not val.startswith(("http://", "https://")):
            raise HTTPException(400, f"{key}: must start with http:// or https://")
        if key in _BOOL_KEYS and val.lower() not in _TRUE + _FALSE:
            raise HTTPException(400, f"{key}: must be true or false")
        if key in _CHOICES and val not in _CHOICES[key]:
            raise HTTPException(400, f"{key}: must be one of " + ", ".join(_CHOICES[key]))
        if key.endswith("_CHANNEL") and not (val.startswith("#") or val.isdigit()):
            raise HTTPException(400, f"{key}: must start with '#' or be a slot number")


async def _apply_live(bot, updates: dict[str, str]) -> list[str]:
    """Apply what can be applied now; return the keys that were."""
    import logging
    from meshcore_weather.geodata import resolver
    applied: list[str] = []
    coverage_changed = False
    for key, val in updates.items():
        if key not in _LIVE_KEYS or val == "":
            continue                      # an emptied key takes its default at the next start
        if key == "MCW_TIMEZONE":
            settings.timezone = val
        elif key == "MCW_LOG_LEVEL":
            logging.getLogger().setLevel(getattr(logging, val.upper(), logging.INFO))
            settings.log_level = val
        elif key == "MCW_TX_ENABLED":
            settings.tx_enabled = val.strip().lower() in _TRUE
        elif key == "MCW_REPLY_MODE":
            settings.reply_mode = val
        elif key == "MCW_SDR_DASHBOARD_URL":
            settings.sdr_dashboard_url = val
        elif key in ("MCW_CHANNEL_REPLY_MAX_HOPS", "MCW_ADVERT_INTERVAL_HOURS"):
            setattr(settings, key[4:].lower(), int(val))
        elif key == "MCW_PEER_BOT_PREFIX":
            settings.peer_bot_prefix = val
        elif key == "MCW_CONTACT_HOUSEKEEPING":
            settings.contact_housekeeping = val.strip().lower() in _TRUE
        elif key == "MCW_CONTACT_KEEP_FREE":
            settings.contact_keep_free = max(0, int(val))
        elif key in ("MCW_RETRANSMIT_MAX", "MCW_RETRANSMIT_PER_HOUR", "MCW_SCOPE_MIN_OBSERVERS"):
            setattr(settings, key[4:].lower(), max(1, int(val)) if key == "MCW_SCOPE_MIN_OBSERVERS" else int(val))
        elif key == "MCW_ECHO_WINDOW_S":
            settings.echo_window_s = float(val)
        elif key == "MCW_SCOPE_URL":
            settings.scope_url = val
        elif key == "MCW_SCOPE_MODE":
            settings.scope_mode = val
        elif key in ("MCW_HOME_CITIES", "MCW_HOME_STATES", "MCW_HOME_WFOS", "MCW_HOME_RADIUS_KM"):
            attr = key[4:].lower()
            setattr(settings, attr, int(val) if key.endswith("_KM") else val)
            coverage_changed = True
        elif key in ("MCW_SERIAL_PORT", "MCW_SERIAL_BAUD"):
            setattr(settings, key[4:].lower(), int(val) if key.endswith("BAUD") else val)
        applied.append(key)
    if coverage_changed:
        try:
            resolver._set_home_from_settings()
        except Exception:
            pass
        if bot._broadcaster is not None:
            try:
                bot._broadcaster.scheduler.reload_coverage()
            except Exception:
                pass
    if "MCW_SERIAL_PORT" in applied or "MCW_SERIAL_BAUD" in applied:
        await bot.reconnect_radio()
    return applied


@router.post("/settings/env")
async def settings_env(request: Request) -> JSONResponse:
    body = await _body(request)
    updates = {str(k): str(v).strip() for k, v in body.items()}
    if not updates:
        raise HTTPException(400, "nothing to change")
    bad = set(updates) - ENV_WRITABLE
    if bad:
        raise HTTPException(400, f"not writable: {', '.join(sorted(bad))}")
    _validate_updates(updates)
    _write_env(updates)
    applied = await _apply_live(_bot(request), updates)
    pending = sorted(set(updates) - set(applied))
    if applied and pending:
        note = "Applied now: " + ", ".join(sorted(applied)) + ". Needs a restart: " + ", ".join(pending)
    elif applied:
        note = "Applied now"
    else:
        note = "Saved; needs a restart: " + ", ".join(pending)
    return JSONResponse({"ok": True, "applied": sorted(applied), "restart_needed": pending, "note": note})


@router.post("/system/restart")
async def system_restart(request: Request) -> JSONResponse:
    """Clean exit; systemd restarts the bot with the current .env."""
    _bot(request).request_restart()
    return JSONResponse({"ok": True, "note": "Restarting; back in about 40 seconds"})


@router.post("/radio/reconnect")
async def radio_reconnect(request: Request) -> JSONResponse:
    await _bot(request).reconnect_radio()
    bot = _bot(request)
    return JSONResponse({"ok": True, "connected": bot.radio.connected, "error": bot._radio_last_error})


# -- Audit: structured answers for scripts/audit.py -----------------------------------
#
# The bot never touches the internet; the audit script does, and compares
# these structured answers with api.weather.gov, IEM, aviationweather.gov
# and SWPC. Same services the text and binary replies use.


@router.get("/audit/warnings")
async def audit_warnings(request: Request, state: str = Query(..., min_length=2, max_length=2)) -> JSONResponse:
    from meshcore_weather.protocol.warnings import extract_active_warnings
    st = state.upper()
    out = []
    for w in extract_active_warnings(_bot(request).store, coverage=None):
        ugcs = [u for u in (w.get("ugcs") or w.get("zones") or [])]
        if not any(u[:2] == st for u in ugcs):
            continue
        out.append({
            "office": w.get("vtec_office"), "phenomenon": w.get("vtec_phenomenon"),
            "significance": w.get("vtec_significance"), "etn": w.get("vtec_etn"),
            "expires": w["expires_at"].isoformat() if w.get("expires_at") else None,
            "ugcs": sorted(u for u in ugcs if u[:2] == st),
            "headline": (w.get("headline") or "")[:120],
        })
    return JSONResponse({"state": st, "events": out})


@router.get("/audit/storms")
async def audit_storms(request: Request, state: str = Query(..., min_length=2, max_length=2),
                       hours: int = Query(6, ge=1, le=48)) -> JSONResponse:
    from meshcore_weather.core import services
    sr = services.storm_reports_for(_bot(request).store, state=state.upper(), limit=200, max_age_hours=hours)
    return JSONResponse({"state": state.upper(), "reports": [
        {"at": e["at"].isoformat(), "event": e["event"], "location": e["location"], "county": e.get("county"),
         "state": e.get("state"), "mag": e.get("mag")} for e in (sr.entries if sr else [])]})


@router.get("/audit/obs")
async def audit_obs(request: Request, station: str = Query(..., min_length=4, max_length=4)) -> JSONResponse:
    from meshcore_weather.core import services
    from meshcore_weather.geodata import resolver
    loc = resolver.resolve(station.upper())
    if not loc:
        raise HTTPException(404, "unknown station")
    raw = services.raw_metar_for(_bot(request).store, loc)
    ob = services.observation_for(_bot(request).store, loc)
    return JSONResponse({
        "station": station.upper(),
        "metar": raw[2] if raw else None, "metar_station": raw[0] if raw else None,
        "obs": None if ob is None else {"station": ob.station, "temp_f": ob.temp_f, "dewpoint_f": ob.dewpoint_f,
                                        "wind_dir_deg": ob.wind_dir_deg, "wind_speed_mph": ob.wind_speed_mph,
                                        "wind_gust_mph": ob.wind_gust_mph, "obs_utc_min": ob.obs_utc_min},
    })


@router.get("/audit/forecast")
async def audit_forecast(request: Request, place: str = Query(..., min_length=2)) -> JSONResponse:
    from meshcore_weather.core import services
    from meshcore_weather.geodata import resolver
    loc = resolver.resolve(place)
    if not loc:
        raise HTTPException(404, "unknown place")
    fc = services.forecast_for(_bot(request).store, loc)
    return JSONResponse({
        "place": loc.get("name"), "lat": loc.get("lat"), "lon": loc.get("lon"),
        "point": fc.point_name if fc else None, "point_km": fc.distance_km if fc else None,
        "wfo": fc.wfo if fc else None, "issued": fc.issued_at.isoformat() if fc and fc.issued_at else None,
        "start_date": fc.start_date.date().isoformat() if fc and fc.start_date else None,
        "periods": [{"day": p["period_id"], "high_f": p["high_f"], "low_f": p["low_f"], "precip_pct": p.get("precip_pct", 0)}
                    for p in (fc.periods if fc else [])],
    })


@router.get("/audit/space")
async def audit_space(request: Request) -> JSONResponse:
    from meshcore_weather.core import space_weather
    sw = space_weather.space_weather_for(_bot(request).store)
    if sw is None:
        return JSONResponse({"available": False})
    return JSONResponse({"available": True, "issued": sw.issued_at.isoformat(), "kp_max_24h": sw.kp_max_24h,
                         "kp_forecast": sw.kp_forecast, "g_forecast": sw.g_forecast,
                         "first_day": sw.first_day.isoformat() if sw.first_day else None,
                         "kp_now": sw.kp_now, "kp_expected": sw.kp_expected, "sfi": sw.sfi, "ssn": sw.ssn})


@router.get("/audit/last")
async def audit_last() -> JSONResponse:
    """Result of the most recent scripts/audit.py run (data/audit.json)."""
    return JSONResponse(_audit_summary())
