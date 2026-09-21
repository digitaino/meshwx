"""Simple command parser. No LLM needed.

Supported formats:
    wx Austin TX
    wx KAUS
    wx 78701            (a ZIP or ZIP+4 is a place like any other)
    forecast Miami FL
    warn FL
    metar KJFK
    taf KJFK
    radar Austin TX     (what the newest radar picture shows there)
    sat
    help
"""

import re


# Command must be first word, followed by location
COMMAND_RE = re.compile(
    r"^(wx|warn|warnings?|wanr|forecast|metar|taf|help|more|outlook|rain|radar|storm|storms|space|swx|solar)\b\s*(.*)",
    re.IGNORECASE,
)

# Receiver status takes no argument, so only the bare word counts:
# "satellite beach fl" and "signal mountain tn" stay places.
SAT_RE = re.compile(r"^(sat|satellite|goes|signal)[\s?!.]*$", re.IGNORECASE)

# What the bot covers. No argument either, so the bare word only: "cove tx"
# and "coverage of round rock" stay places.
COV_RE = re.compile(r"^(cov|coverage|covers)[\s?!.]*$", re.IGNORECASE)

# Normalize typos/aliases to canonical command names
_CMD_ALIASES = {"warnings": "warn", "warning": "warn", "wanr": "warn", "storms": "storm", "swx": "space", "solar": "space"}


async def parse_intent(text: str) -> dict:
    text = text.strip()
    if not text:
        return {"command": "help", "location": ""}

    if SAT_RE.match(text):
        return {"command": "sat", "location": ""}

    if COV_RE.match(text):
        return {"command": "cov", "location": ""}

    m = COMMAND_RE.match(text)
    if m:
        cmd = m.group(1).lower()
        cmd = _CMD_ALIASES.get(cmd, cmd)
        loc = m.group(2).strip()
        # Strip filler words
        for prefix in ["for ", "in ", "near ", "around "]:
            if loc.lower().startswith(prefix):
                loc = loc[len(prefix):]
        return {"command": cmd, "location": loc}

    # No recognized command - assume "wx" with the whole text as location
    return {"command": "wx", "location": text}
