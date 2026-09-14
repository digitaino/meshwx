"""Admin API: the radio, the satellite receiver, the text bot console, logs,
host status and .env settings. Everything here is operator-only; the portal
sits behind HTTP Basic auth when MCW_ADMIN_KEY is set (see server.py)."""

from __future__ import annotations

import asyncio
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

from meshcore_weather.config import settings
from meshcore_weather.portal import logbuf

router = APIRouter()

# .env keys the portal may write. Anything else is refused.
ENV_WRITABLE = {
    "MCW_SERIAL_PORT", "MCW_SERIAL_BAUD",
    "MCW_MESHCORE_CHANNEL", "MCW_MESHWX_CHANNEL", "MCW_MESHWX_DISCOVER_CHANNEL",
    "MCW_HOME_CITIES", "MCW_HOME_RADIUS_KM", "MCW_HOME_STATES", "MCW_HOME_WFOS",
    "MCW_TIMEZONE", "MCW_TX_ENABLED", "MCW_EMWIN_SOURCE", "MCW_SDR_EMWIN_DIR",
    "MCW_SDR_POLL_INTERVAL", "MCW_SDR_DASHBOARD_URL", "MCW_LOG_LEVEL",
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
    await _run(_radio_call(request).set_channel_name(idx, name, secret))
    return JSONResponse({"ok": True, "channels": await _radio_call(request).list_channels()})


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
    settings.tx_enabled = enabled
    _write_env({"MCW_TX_ENABLED": "true" if enabled else "false"})
    return JSONResponse({"ok": True, "tx_enabled": enabled})


@router.get("/radio/contacts")
async def radio_contacts(request: Request) -> JSONResponse:
    return JSONResponse({"contacts": await _run(_radio_call(request).contacts())})


@router.get("/radio/stats")
async def radio_stats(request: Request) -> JSONResponse:
    return JSONResponse(await _run(_radio_call(request).stats()))


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


@router.post("/sdr/gain")
async def sdr_gain(request: Request) -> JSONResponse:
    body = await _body(request)
    try:
        gain = int(body["gain"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "gain required")
    res = await _dashboard(f"/meter/gain/{gain}", "POST")
    if res and res.get("_error"):
        raise HTTPException(502, res["_error"])
    return JSONResponse({"ok": True, "gain": gain})


# -- Text bot console ----------------------------------------------------------------


@router.post("/console")
async def console(request: Request) -> JSONResponse:
    """Run one text command exactly as a DM would be handled, without the radio."""
    body = await _body(request)
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "text required")
    bot = _bot(request)
    from meshcore_weather.parser.weather import paginate
    t0 = time.monotonic()
    command, location = await bot._parse(text)
    reply = bot._process_command(command, location)
    chunks = []
    if reply:
        offset, more = 0, True
        while more and len(chunks) < 6:
            chunk, offset, more = paginate(reply, offset)
            chunks.append(chunk)
    return JSONResponse({
        "text": text, "command": command, "location": location,
        "reply": reply, "chunks": chunks, "ms": int((time.monotonic() - t0) * 1000),
    })


@router.get("/console/help")
async def console_help(request: Request) -> JSONResponse:
    from meshcore_weather.main import HELP_TEXT
    return JSONResponse({"help": HELP_TEXT})


# -- Logs, host, settings -------------------------------------------------------------


@router.get("/logs")
async def logs(n: int = Query(200, ge=1, le=600), level: str | None = None) -> JSONResponse:
    return JSONResponse({"lines": logbuf.tail(n, level)})


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
        "settings": safe,
        "env_writable": sorted(ENV_WRITABLE),
    })


@router.post("/settings/env")
async def settings_env(request: Request) -> JSONResponse:
    body = await _body(request)
    updates = {str(k): str(v) for k, v in body.items()}
    _write_env(updates)
    return JSONResponse({"ok": True, "updated": sorted(updates), "note": "Restart the bot to apply"})
