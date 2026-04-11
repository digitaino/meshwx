"""JSON/API routes for the portal (HTMX partials and data endpoints)."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from meshcore_weather.geodata import resolver
from meshcore_weather.protocol.coverage import Coverage
from meshcore_weather.protocol.warnings import extract_active_warnings

router = APIRouter()


# -- Coverage preview & save --

@router.get("/coverage/preview")
async def coverage_preview(
    cities: str = Query("", description="Comma-separated city,state pairs"),
    states: str = Query("", description="Comma-separated 2-letter state codes"),
    wfos: str = Query("", description="Comma-separated 3-letter WFO codes"),
) -> JSONResponse:
    """Compute coverage from the given inputs without saving it."""
    city_list = [c.strip() for c in cities.split(",") if c.strip()]
    state_list = [s.strip() for s in states.split(",") if s.strip()]
    wfo_list = [w.strip() for w in wfos.split(",") if w.strip()]

    cov = Coverage.from_sources(cities=city_list, states=state_list, wfos=wfo_list)
    return JSONResponse({
        "zones": sorted(cov.zones),
        "zone_count": len(cov.zones),
        "bbox": cov.bbox,
        "region_ids": sorted(cov.region_ids),
        "summary": cov.summary(),
    })


@router.post("/coverage/save")
async def coverage_save(request: Request) -> JSONResponse:
    """Deprecated — coverage is bootstrap config loaded from environment.

    Previously this endpoint tried to rewrite `.env` in place, which
    crashed because the container runs as a non-root user and the file
    is owned by root. More importantly, the whole premise — "web UI
    edits environment variables that the process has already loaded" —
    doesn't actually work cleanly since env vars are process-start
    state.

    Runtime broadcast configuration lives in `data/broadcast_config.json`
    now and is managed by the /schedule page. This endpoint remains so
    the existing /config form doesn't 404, but it returns a clear
    explanation instead of attempting a file write.
    """
    return JSONResponse(
        {
            "ok": False,
            "error": "coverage_is_bootstrap_config",
            "message": (
                "Coverage (home_cities/home_states/home_wfos) is loaded "
                "from environment variables at bot startup and cannot be "
                "changed live from the portal. To change coverage, edit "
                ".env on the host and restart the container. To change "
                "what the bot broadcasts without touching coverage, use "
                "the Schedule page (/schedule) — that lets you add, "
                "remove, and configure individual broadcast jobs at "
                "runtime without a restart."
            ),
        },
        status_code=400,
    )


# -- Autocomplete helpers --

@router.get("/autocomplete/city")
async def autocomplete_city(q: str = Query("", min_length=2)) -> JSONResponse:
    """Suggest city+state matches for the config form."""
    resolver.load()
    q_upper = q.upper().strip()
    matches = []
    for place in resolver._places:
        name, state = place[0], place[1]
        if name.upper().startswith(q_upper):
            matches.append(f"{name.title()}, {state}")
            if len(matches) >= 10:
                break
    return JSONResponse({"matches": matches})


@router.get("/autocomplete/wfo")
async def autocomplete_wfo(q: str = Query("", min_length=1)) -> JSONResponse:
    """Suggest WFO codes."""
    resolver.load()
    q_upper = q.upper().strip()
    wfos = sorted({z["w"] for z in resolver._zones.values() if z.get("w")})
    matches = [w for w in wfos if w.startswith(q_upper)][:10]
    return JSONResponse({"matches": matches})


# -- Warnings --

@router.get("/warnings")
async def list_warnings(request: Request) -> JSONResponse:
    """List all active warnings with coverage tag."""
    bot = request.app.state.bot
    broadcaster = getattr(bot, "_broadcaster", None)
    coverage = broadcaster.coverage if broadcaster else None

    # Get all warnings (no filter)
    all_warnings = extract_active_warnings(bot.store, coverage=None)

    # Build JSON-safe response objects. Warning dicts contain a datetime
    # `expires_at` field (added in the v3 pyIEM port) which json.dumps can't
    # serialize directly — convert to ISO 8601 string here.
    out: list[dict] = []
    for w in all_warnings:
        if coverage is None or coverage.is_empty():
            in_cov = True
        else:
            zones = w.get("zones", [])
            in_cov = (
                coverage.covers_any(zones)
                or coverage.covers_polygon(w.get("vertices", []))
            )

        verts = w.get("vertices", [])
        bbox = None
        if verts:
            lats = [v[0] for v in verts]
            lons = [v[1] for v in verts]
            bbox = [min(lats), min(lons), max(lats), max(lons)]

        expires_at = w.get("expires_at")
        out.append({
            "warning_type": w.get("warning_type"),
            "severity": w.get("severity"),
            "expires_at": expires_at.isoformat() if expires_at else None,
            "expiry_minutes": w.get("expiry_minutes"),
            "headline": w.get("headline"),
            "zones": w.get("zones", []),
            "ugcs": w.get("ugcs", []),
            "product_type": w.get("product_type"),
            "vtec_action": w.get("vtec_action"),
            "vtec_phenomenon": w.get("vtec_phenomenon"),
            "vtec_significance": w.get("vtec_significance"),
            "vtec_office": w.get("vtec_office"),
            "vtec_etn": w.get("vtec_etn"),
            "in_coverage": in_cov,
            "bbox": bbox,
            "vertices": verts,  # kept for /data map view
        })

    return JSONResponse({"warnings": out, "count": len(out)})


# -- EMWIN product browser --

@router.get("/products")
async def list_products(
    request: Request,
    type: str = Query(""),
    office: str = Query(""),
    state: str = Query(""),
    q: str = Query(""),
    limit: int = Query(100),
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
        # Get first non-empty line as preview
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

    return JSONResponse({"products": results, "count": len(results)})


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


# -- Status + actions --

@router.get("/status")
async def get_status(request: Request) -> JSONResponse:
    """Bot operational status."""
    bot = request.app.state.bot
    broadcaster = getattr(bot, "_broadcaster", None)
    return JSONResponse({
        "radio": {
            "channel_idx": bot.radio.channel_idx,
            "data_channel_idx": bot.radio.data_channel_idx,
        },
        "store": {
            "product_count": len(bot.store._products),
        },
        "broadcaster": {
            "running": broadcaster is not None,
            "coverage": broadcaster.coverage.summary() if broadcaster else None,
        },
        "contacts": {
            "known": len(bot._known_contacts) if hasattr(bot, "_known_contacts") else 0,
        },
    })


@router.post("/actions/broadcast")
async def trigger_broadcast(request: Request) -> JSONResponse:
    """Manually trigger a broadcast cycle."""
    bot = request.app.state.bot
    broadcaster = getattr(bot, "_broadcaster", None)
    if not broadcaster:
        raise HTTPException(400, "Broadcaster not running")
    await broadcaster._broadcast_all()
    return JSONResponse({"ok": True})


@router.post("/actions/v2-request")
async def trigger_v2_request(request: Request) -> JSONResponse:
    """Simulate a v2 data request for testing (bypasses rate limit with force flag).

    Body: {"data_type": "wx"|"forecast"|"metar", "location": "Austin TX"}
    """
    from meshcore_weather.protocol.meshwx import (
        DATA_FORECAST, DATA_METAR, DATA_WX, LOC_STATION, LOC_ZONE
    )
    body = await request.json()
    data_type_str = body.get("data_type", "wx")
    location_str = body.get("location", "")

    bot = request.app.state.bot
    broadcaster = getattr(bot, "_broadcaster", None)
    if not broadcaster:
        raise HTTPException(400, "Broadcaster not running")

    # Resolve the location string to a zone or station
    resolved = resolver.resolve(location_str)
    if not resolved:
        raise HTTPException(400, f"Could not resolve: {location_str}")

    # Prefer zone, fall back to station
    zones = resolved.get("zones", [])
    station = resolved.get("station")
    if zones:
        loc = {"type": LOC_ZONE, "zone": zones[0]}
    elif station:
        loc = {"type": LOC_STATION, "station": station}
    else:
        raise HTTPException(400, "Could not build location ref")

    data_map = {"wx": DATA_WX, "forecast": DATA_FORECAST, "metar": DATA_METAR}
    data_type = data_map.get(data_type_str)
    if data_type is None:
        raise HTTPException(400, f"Unknown data_type: {data_type_str}")

    # Bypass rate limit by clearing this entry
    loc_key = broadcaster._location_key(loc)
    rate_key = f"{data_type}:{loc_key}"
    if hasattr(broadcaster, "_v2_rate_limit"):
        broadcaster._v2_rate_limit.pop(rate_key, None)

    req = {"data_type": data_type, "location": loc, "client_newest": 0, "flags": 0}
    await broadcaster.respond_to_data_request(req)
    return JSONResponse({"ok": True, "location": loc, "data_type": data_type_str})
