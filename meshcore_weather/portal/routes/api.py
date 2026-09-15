"""JSON routes: the EMWIN product browser, the broadcast log and its
counters, the bot's channel names, and the broadcast schedule (CRUD, run now)."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from meshcore_weather.activity import activity_log
from meshcore_weather.portal.sse import sse_response
from meshcore_weather.schedule.models import (
    BroadcastJob,
    LOCATION_TYPES,
    PRODUCT_TYPES,
)

router = APIRouter()


def _get_scheduler(request: Request):
    """Return the bot's active Scheduler instance or raise 503."""
    bot = request.app.state.bot
    broadcaster = getattr(bot, "_broadcaster", None)
    if broadcaster is None or not hasattr(broadcaster, "scheduler"):
        raise HTTPException(503, "broadcasts are off: no data channel configured")
    return broadcaster.scheduler


# -- EMWIN product browser --

@router.get("/products/filters")
async def product_filters(request: Request) -> JSONResponse:
    """Return distinct filter values for the product browser dropdowns."""
    bot = request.app.state.bot
    types = sorted({p.product_type for p in bot.store._products.values()})
    offices = sorted({p.office for p in bot.store._products.values() if p.office})
    states = sorted({p.state for p in bot.store._products.values() if p.state})
    return JSONResponse({"types": types, "offices": offices, "states": states})


@router.get("/products")
async def list_products(
    request: Request,
    type: str = Query(""),
    office: str = Query(""),
    state: str = Query(""),
    q: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
) -> JSONResponse:
    """List ingested EMWIN products with optional filters."""
    bot = request.app.state.bot
    results = []
    q_lower = q.lower().strip()

    for prod in sorted(bot.store._products.values(), key=lambda p: p.timestamp, reverse=True):
        if type and prod.product_type != type:
            continue
        if office and prod.office != office:
            continue
        if state and prod.state != state:
            continue
        if q_lower and q_lower not in prod.raw_text.lower():
            continue
        preview = ""
        for line in prod.raw_text.splitlines():
            line = line.strip()
            if line and not line.startswith("$$"):
                preview = line[:120]
                break
        results.append({
            "filename": prod.filename,
            "emwin_id": prod.emwin_id,
            "product_type": prod.product_type,
            "office": prod.office,
            "state": prod.state,
            "timestamp": prod.timestamp.isoformat(),
            "preview": preview,
        })
        if len(results) >= limit:
            break

    return JSONResponse({"products": results, "count": len(results), "limit": limit})


@router.get("/products/{filename}")
async def get_product(request: Request, filename: str) -> JSONResponse:
    """Get the full raw text of a specific product."""
    bot = request.app.state.bot
    prod = bot.store._products.get(filename)
    if not prod:
        raise HTTPException(404, "Product not found")
    return JSONResponse({
        "filename": prod.filename,
        "emwin_id": prod.emwin_id,
        "product_type": prod.product_type,
        "office": prod.office,
        "state": prod.state,
        "timestamp": prod.timestamp.isoformat(),
        "raw_text": prod.raw_text,
    })


# -- Broadcast log (binary side: jobs, app requests, beacons) --

@router.get("/activity")
async def get_activity(
    limit: int = Query(100, ge=1, le=500),
) -> JSONResponse:
    """Return the most recent broadcast-log entries."""
    return JSONResponse({"events": activity_log.recent(limit)})


@router.get("/activity/stream")
async def activity_stream():
    """SSE stream of broadcast-log entries as they happen."""
    async def items():
        async for event in activity_log.subscribe():
            yield event.to_dict()
    return sse_response(items())


@router.get("/stats")
async def get_stats() -> JSONResponse:
    """Messages and bytes sent on the data channel over the standard windows."""
    return JSONResponse({"stats": [activity_log.stats(w) for w in (5, 15, 60, 360, 1440)]})


# -- Channel names --

@router.post("/settings/channels")
async def set_channels(request: Request) -> JSONResponse:
    """Change the bot's text / data / discovery channel names.

    Applied live when the radio is up (the slot is renamed in place, or an
    existing slot with that name is reused, or a free slot is created),
    kept in memory otherwise so the next radio connect uses them, and
    persisted to .env either way.
    """
    from meshcore_weather.config import settings
    from meshcore_weather.portal.routes.admin import _write_env

    body = await request.json()
    wanted = {
        "text": body.get("text_channel", "").strip(),
        "data": body.get("data_channel", "").strip(),
        "discover": body.get("discover_channel", "").strip(),
    }
    if not wanted["text"]:
        raise HTTPException(400, "text_channel is required")
    for role, val in wanted.items():
        if val and not val.startswith("#") and not val.isdigit():
            raise HTTPException(400, f"{role} channel must start with '#' or be a numeric index")
    if len({v for v in wanted.values() if v}) != len([v for v in wanted.values() if v]):
        raise HTTPException(400, "each role needs a different channel")

    bot = request.app.state.bot
    radio = bot.radio
    applied: dict[str, int | None] = {}
    if radio.connected:
        for role in ("text", "data", "discover"):
            try:
                applied[role] = await radio.assign_role(role, wanted[role])
            except ValueError as e:
                raise HTTPException(400, str(e))
            except Exception as e:
                raise HTTPException(500, f"radio error on {role} channel: {e}")
        # Discovery pings are only wired when a broadcaster exists; a data
        # channel that appeared just now needs the broadcaster started.
        if applied.get("data") is not None and bot._broadcaster is None:
            await bot._after_radio_connected()
    else:
        settings.meshcore_channel = wanted["text"]
        settings.meshwx_channel = wanted["data"]
        settings.meshwx_discover_channel = wanted["discover"]

    _write_env({
        "MCW_MESHCORE_CHANNEL": wanted["text"],
        "MCW_MESHWX_CHANNEL": wanted["data"],
        "MCW_MESHWX_DISCOVER_CHANNEL": wanted["discover"],
    })
    return JSONResponse({
        "ok": True,
        "applied": radio.connected,
        "slots": applied if radio.connected else None,
        "text_channel": wanted["text"], "data_channel": wanted["data"], "discover_channel": wanted["discover"],
        "note": "Applied on the node now" if radio.connected else "Saved; applied when the radio connects",
    })


# -- Broadcast schedule --

@router.post("/actions/broadcast")
async def run_due_jobs(request: Request) -> JSONResponse:
    """Run every enabled job whose interval has elapsed. A job that is not
    due sends nothing; use /schedule/jobs/{id}/run-now to force one."""
    scheduler = _get_scheduler(request)
    sent = await scheduler.tick()
    return JSONResponse({"ok": True, "messages_sent": sent})


# What each product is and which location types its builder understands
# (schedule/executor.py). Every PRODUCT_TYPES entry must appear here.
PRODUCT_INFO = {
    "warnings":       {"label": "Warnings (full)",        "desc": "Re-broadcast ALL active warnings (safety net)", "locations": ["coverage"]},
    "warnings_delta": {"label": "Warnings (delta)",       "desc": "Only new/changed warnings since last cycle", "locations": ["coverage"]},
    "warnings_near":  {"label": "Warnings near zone",     "desc": "Warnings affecting a specific zone", "locations": ["zone"]},
    "observation":    {"label": "Observation",            "desc": "Current conditions for a point", "locations": ["city", "station", "zone"]},
    "forecast":       {"label": "Forecast",               "desc": "Multi-day forecast for a point", "locations": ["city", "zone", "pfm_point"]},
    "outlook":        {"label": "Hazardous Outlook",      "desc": "Hazardous weather outlook", "locations": ["coverage", "wfo"]},
    "metar":          {"label": "METAR",                  "desc": "Raw METAR observation for a station", "locations": ["station"]},
    "taf":            {"label": "TAF",                    "desc": "Terminal aerodrome forecast for a station", "locations": ["station"]},
    "storm_reports":  {"label": "Storm Reports",          "desc": "Local storm reports (LSR)", "locations": ["coverage", "wfo"]},
    "rain_obs":       {"label": "Rain Observations",      "desc": "Rain-reporting cities", "locations": ["coverage"]},
    "fire_weather":   {"label": "Fire Weather",           "desc": "Fire weather forecast (FWF) for the zone a place is in", "locations": ["city", "zone"]},
    "daily_climate":  {"label": "Daily Climate",          "desc": "Regional temperature and precipitation summary (RTP)", "locations": ["coverage", "wfo"]},
    "nowcast":        {"label": "Nowcast",                "desc": "Short-term forecast (NOW) for a place or an office", "locations": ["city", "zone", "wfo"]},
    "afd":            {"label": "Area Forecast Discussion", "desc": "AFD text from a forecast office", "locations": ["wfo"]},
    "space_weather":  {"label": "Space Weather",          "desc": "SWPC space weather indices", "locations": ["coverage"]},
}

LOCATION_INFO = {
    "coverage":  {"label": "Coverage area",    "desc": "All zones in the operator's configured coverage", "placeholder": "(leave empty)"},
    "city":      {"label": "City",             "desc": "Resolved to nearest NWS zone", "placeholder": "e.g. Austin TX"},
    "station":   {"label": "Station (ICAO)",   "desc": "4-letter ICAO code", "placeholder": "e.g. KAUS"},
    "zone":      {"label": "NWS Zone",         "desc": "6-character UGC zone code", "placeholder": "e.g. TXZ192"},
    "wfo":       {"label": "Forecast Office",  "desc": "3-letter WFO code", "placeholder": "e.g. EWX"},
    "pfm_point": {"label": "PFM Point",        "desc": "Numeric index into pfm_points.json", "placeholder": "e.g. 103"},
}


@router.get("/schedule/meta")
async def schedule_meta() -> JSONResponse:
    """Metadata for the job form: products with descriptions, location
    types, and which locations each product supports."""
    return JSONResponse({
        "products": sorted(PRODUCT_TYPES),
        "location_types": sorted(LOCATION_TYPES),
        "product_info": PRODUCT_INFO,
        "location_info": LOCATION_INFO,
    })


@router.get("/schedule/jobs")
async def list_jobs(request: Request) -> JSONResponse:
    """List all configured broadcast jobs with their runtime status."""
    scheduler = _get_scheduler(request)
    cfg = scheduler.current_config()
    out = []
    for job in cfg.jobs:
        status = scheduler.job_status(job.id)
        out.append({
            **job.model_dump(),
            "last_run_unix": status.get("last_run_unix"),
            "last_run_seconds_ago": status.get("last_run_seconds_ago"),
            "next_run_in_seconds": status.get("next_run_in_seconds"),
            "total_runs": status.get("total_runs", 0),
            "total_bytes": status.get("total_bytes", 0),
            "last_bytes": status.get("last_bytes", 0),
            "last_msg_count": status.get("last_msg_count", 0),
        })
    return JSONResponse({"jobs": out, "count": len(out)})


@router.post("/schedule/jobs")
async def create_job(request: Request) -> JSONResponse:
    """Create a new broadcast job from a JSON body."""
    scheduler = _get_scheduler(request)
    body = await request.json()
    try:
        job = BroadcastJob(**body)
    except Exception as exc:
        raise HTTPException(400, f"invalid job: {exc}")
    cfg = scheduler.current_config()
    if cfg.get_job(job.id) is not None:
        raise HTTPException(409, f"job {job.id!r} already exists")
    cfg.upsert_job(job)
    await scheduler.save_config(cfg)
    return JSONResponse({"ok": True, "job": job.model_dump()})


@router.put("/schedule/jobs/{job_id}")
async def update_job(job_id: str, request: Request) -> JSONResponse:
    """Update an existing broadcast job. Body is the new job dict."""
    scheduler = _get_scheduler(request)
    body = await request.json()
    # The URL param is authoritative for id so the client can't
    # accidentally rename by changing the body.
    body["id"] = job_id
    try:
        job = BroadcastJob(**body)
    except Exception as exc:
        raise HTTPException(400, f"invalid job: {exc}")
    cfg = scheduler.current_config()
    if cfg.get_job(job_id) is None:
        raise HTTPException(404, f"job {job_id!r} not found")
    cfg.upsert_job(job)
    await scheduler.save_config(cfg)
    return JSONResponse({"ok": True, "job": job.model_dump()})


@router.delete("/schedule/jobs/{job_id}")
async def delete_job(job_id: str, request: Request) -> JSONResponse:
    """Delete a broadcast job by id."""
    scheduler = _get_scheduler(request)
    cfg = scheduler.current_config()
    if not cfg.delete_job(job_id):
        raise HTTPException(404, f"job {job_id!r} not found")
    await scheduler.save_config(cfg)
    return JSONResponse({"ok": True, "deleted": job_id})


@router.post("/schedule/jobs/{job_id}/toggle")
async def toggle_job(job_id: str, request: Request) -> JSONResponse:
    """Flip the `enabled` flag on a job."""
    scheduler = _get_scheduler(request)
    cfg = scheduler.current_config()
    job = cfg.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"job {job_id!r} not found")
    job.enabled = not job.enabled
    cfg.upsert_job(job)
    await scheduler.save_config(cfg)
    return JSONResponse({"ok": True, "id": job_id, "enabled": job.enabled})


@router.post("/schedule/jobs/{job_id}/run-now")
async def run_job_now(job_id: str, request: Request) -> JSONResponse:
    """Force-run a specific job immediately, ignoring its interval."""
    scheduler = _get_scheduler(request)
    cfg = scheduler.current_config()
    if cfg.get_job(job_id) is None:
        raise HTTPException(404, f"job {job_id!r} not found")
    n_msgs = await scheduler.run_job_now(job_id)
    return JSONResponse({"ok": True, "id": job_id, "messages_sent": n_msgs})
