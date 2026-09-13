"""Human names for VTEC phenomenon.significance pairs.

Used by the text renderer today and destined for protocol.json as the v5
event table (one byte per event on the wire, this table on the client).
Short names are sized for a LoRa reply; long names for app UIs.
"""

# (short, long). Keep short names <= 16 chars.
VTEC_NAMES: dict[str, tuple[str, str]] = {
    "TO.W": ("TORNADO WRN", "Tornado Warning"),
    "TO.A": ("TORNADO WATCH", "Tornado Watch"),
    "SV.W": ("SVR TSTM WRN", "Severe Thunderstorm Warning"),
    "SV.A": ("SVR TSTM WATCH", "Severe Thunderstorm Watch"),
    "EW.W": ("EXTREME WIND WRN", "Extreme Wind Warning"),
    "FF.W": ("FLASH FLOOD WRN", "Flash Flood Warning"),
    "FF.A": ("FLASH FLOOD WATCH", "Flash Flood Watch"),
    "FA.W": ("FLOOD WRN", "Flood Warning"),
    "FA.Y": ("FLOOD ADV", "Flood Advisory"),
    "FA.A": ("FLOOD WATCH", "Flood Watch"),
    "FL.W": ("RIVER FLOOD WRN", "River Flood Warning"),
    "FL.Y": ("RIVER FLOOD ADV", "River Flood Advisory"),
    "FL.A": ("RIVER FLOOD WATCH", "River Flood Watch"),
    "HT.Y": ("HEAT ADV", "Heat Advisory"),
    "EH.W": ("EXTREME HEAT WRN", "Extreme Heat Warning"),
    "EH.A": ("EXTREME HEAT WATCH", "Extreme Heat Watch"),
    "XH.W": ("EXTREME HEAT WRN", "Extreme Heat Warning"),
    "WI.Y": ("WIND ADV", "Wind Advisory"),
    "HW.W": ("HIGH WIND WRN", "High Wind Warning"),
    "HW.A": ("HIGH WIND WATCH", "High Wind Watch"),
    "LW.Y": ("LAKE WIND ADV", "Lake Wind Advisory"),
    "FW.W": ("RED FLAG WRN", "Red Flag Warning"),
    "FW.A": ("FIRE WX WATCH", "Fire Weather Watch"),
    "WS.W": ("WINTER STORM WRN", "Winter Storm Warning"),
    "WS.A": ("WINTER STORM WATCH", "Winter Storm Watch"),
    "WW.Y": ("WINTER WX ADV", "Winter Weather Advisory"),
    "BZ.W": ("BLIZZARD WRN", "Blizzard Warning"),
    "IS.W": ("ICE STORM WRN", "Ice Storm Warning"),
    "LE.W": ("LAKE SNOW WRN", "Lake Effect Snow Warning"),
    "ZR.Y": ("FRZ RAIN ADV", "Freezing Rain Advisory"),
    "ZF.Y": ("FRZ FOG ADV", "Freezing Fog Advisory"),
    "FZ.W": ("FREEZE WRN", "Freeze Warning"),
    "FZ.A": ("FREEZE WATCH", "Freeze Watch"),
    "FR.Y": ("FROST ADV", "Frost Advisory"),
    "HZ.W": ("HARD FREEZE WRN", "Hard Freeze Warning"),
    "HZ.A": ("HARD FREEZE WATCH", "Hard Freeze Watch"),
    "CW.Y": ("COLD WX ADV", "Cold Weather Advisory"),
    "EC.W": ("EXTREME COLD WRN", "Extreme Cold Warning"),
    "WC.Y": ("WIND CHILL ADV", "Wind Chill Advisory"),
    "WC.W": ("WIND CHILL WRN", "Wind Chill Warning"),
    "FG.Y": ("DENSE FOG ADV", "Dense Fog Advisory"),
    "SM.Y": ("DENSE SMOKE ADV", "Dense Smoke Advisory"),
    "DS.W": ("DUST STORM WRN", "Dust Storm Warning"),
    "DU.Y": ("BLOWING DUST ADV", "Blowing Dust Advisory"),
    "DU.W": ("BLOWING DUST WRN", "Blowing Dust Warning"),
    "AF.Y": ("ASHFALL ADV", "Ashfall Advisory"),
    "AS.Y": ("AIR STAGNATION", "Air Stagnation Advisory"),
    "SQ.W": ("SNOW SQUALL WRN", "Snow Squall Warning"),
    "TR.W": ("TROP STORM WRN", "Tropical Storm Warning"),
    "TR.A": ("TROP STORM WATCH", "Tropical Storm Watch"),
    "HU.W": ("HURRICANE WRN", "Hurricane Warning"),
    "HU.A": ("HURRICANE WATCH", "Hurricane Watch"),
    "SS.W": ("STORM SURGE WRN", "Storm Surge Warning"),
    "SS.A": ("STORM SURGE WATCH", "Storm Surge Watch"),
    "CF.W": ("COASTAL FLOOD WRN", "Coastal Flood Warning"),
    "CF.Y": ("COASTAL FLOOD ADV", "Coastal Flood Advisory"),
    "CF.A": ("COASTAL FLOOD WATCH", "Coastal Flood Watch"),
    "LS.W": ("LAKESHORE FLD WRN", "Lakeshore Flood Warning"),
    "LS.Y": ("LAKESHORE FLD ADV", "Lakeshore Flood Advisory"),
    "SU.Y": ("HIGH SURF ADV", "High Surf Advisory"),
    "SU.W": ("HIGH SURF WRN", "High Surf Warning"),
    "RP.S": ("RIP CURRENT", "Rip Current Statement"),
    "BH.S": ("BEACH HAZARD", "Beach Hazards Statement"),
    "SC.Y": ("SMALL CRAFT ADV", "Small Craft Advisory"),
    "GL.W": ("GALE WRN", "Gale Warning"),
    "GL.A": ("GALE WATCH", "Gale Watch"),
    "SR.W": ("STORM WRN", "Storm Warning"),
    "SE.W": ("HAZ SEAS WRN", "Hazardous Seas Warning"),
    "SE.A": ("HAZ SEAS WATCH", "Hazardous Seas Watch"),
    "HF.W": ("HURR FORCE WIND", "Hurricane Force Wind Warning"),
    "MA.W": ("SPECIAL MARINE", "Special Marine Warning"),
    "MF.Y": ("MARINE FOG ADV", "Marine Dense Fog Advisory"),
    "TS.W": ("TSUNAMI WRN", "Tsunami Warning"),
    "TS.A": ("TSUNAMI WATCH", "Tsunami Watch"),
    "TS.Y": ("TSUNAMI ADV", "Tsunami Advisory"),
    "SPS": ("SPECIAL WX STMT", "Special Weather Statement"),
}


def short_name(phenomenon: str | None, significance: str | None) -> str:
    """Compact event name for a LoRa reply, e.g. 'HEAT ADV'."""
    if not phenomenon:
        return VTEC_NAMES["SPS"][0]
    key = f"{phenomenon}.{significance}"
    if key in VTEC_NAMES:
        return VTEC_NAMES[key][0]
    sig = {"W": "WRN", "A": "WATCH", "Y": "ADV", "S": "STMT"}.get(significance or "", significance or "")
    return f"{phenomenon} {sig}".strip()


def long_name(phenomenon: str | None, significance: str | None) -> str:
    if not phenomenon:
        return VTEC_NAMES["SPS"][1]
    key = f"{phenomenon}.{significance}"
    return VTEC_NAMES.get(key, (key, key))[1]


# -- Wire event codes -----------------------------------------------------------
#
# One byte per warning on the wire. Code = position in VTEC_NAMES + 1, so the
# table above is APPEND-ONLY: never reorder or delete an entry, or every
# client's decode table shifts. 0 = unknown event. Exported to
# client_data/protocol.json as "events" by scripts/build_client_data.py.

EVENT_CODES: list[str] = list(VTEC_NAMES.keys())
_EVENT_INDEX: dict[str, int] = {k: i + 1 for i, k in enumerate(EVENT_CODES)}
EVENT_UNKNOWN = 0

# Severity is derived from the VTEC significance on the client side; these
# mirror protocol.meshwx SEV_* so the server can fill legacy fields.
_SIG_SEVERITY = {"W": 3, "A": 2, "Y": 1, "S": 1}


def event_code(phenomenon: str | None, significance: str | None) -> int:
    """Wire code for a VTEC phenomenon.significance pair (SPS when None)."""
    key = f"{phenomenon}.{significance}" if phenomenon else "SPS"
    return _EVENT_INDEX.get(key, EVENT_UNKNOWN)


def event_key(code: int) -> str | None:
    """'HT.Y' for a wire code, or None for unknown."""
    if 1 <= code <= len(EVENT_CODES):
        return EVENT_CODES[code - 1]
    return None


def event_from_code(code: int) -> tuple[str | None, str | None]:
    """(phenomenon, significance) for a wire code; (None, None) for SPS/unknown."""
    key = event_key(code)
    if not key or "." not in key:
        return None, None
    ph, sig = key.split(".", 1)
    return ph, sig


def severity_for(significance: str | None) -> int:
    return _SIG_SEVERITY.get(significance or "", 1)
