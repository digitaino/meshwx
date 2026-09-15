"""The node profile: everything that makes the radio *this* bot, kept on
the Pi so a replacement radio can take it over.

A MeshCore node's identity is its Ed25519 key pair, stored only in the
radio's flash. Phones save the bot under that key, and the v5 bot id is
its first two bytes: a new radio with a new key is a different bot to
everyone. The companion protocol can export and import the private key,
so the bot snapshots it (with the name, position, LoRa parameters, TX
power and the stored contacts) after every successful connect, and writes
the snapshot onto a radio that reports a different key ("adoption").

The file holds a private key: mode 0600, in data/ (git-ignored).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from meshcore import EventType

from meshcore_weather.config import settings

logger = logging.getLogger(__name__)

PROFILE_PATH = Path(settings.data_dir) / "node_profile.json"
MAX_CONTACTS_RESTORED = 200
ADOPT_MODES = ("auto", "manual", "off")


def load(path: Path | None = None) -> dict | None:
    path = path or PROFILE_PATH
    try:
        if not path.exists():
            return None
        d = json.loads(path.read_text())
        return d if isinstance(d, dict) and d.get("public_key") else None
    except Exception as e:
        logger.warning("Ignoring node profile %s: %s", path, e)
        return None


def save(profile: dict, path: Path | None = None) -> None:
    path = path or PROFILE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(profile, f, indent=1)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def public_summary(profile: dict | None) -> dict | None:
    """What the portal may show: never the private key."""
    if not profile:
        return None
    return {
        "name": profile.get("name"), "public_key": profile.get("public_key"),
        "has_key": bool(profile.get("private_key")),
        "saved_at": profile.get("saved_at"), "model": profile.get("model"), "fw": profile.get("fw"),
        "adv_lat": profile.get("adv_lat"), "adv_lon": profile.get("adv_lon"),
        "radio": profile.get("radio"), "tx_power": profile.get("tx_power"),
        "contacts": len(profile.get("contacts") or []),
        "history": (profile.get("history") or [])[-5:],
    }


def contact_record(c: dict) -> dict | None:
    """One stored contact in the form update_contact() writes back."""
    key = c.get("public_key")
    if not key or len(key) != 64:
        return None
    return {
        "public_key": key, "type": int(c.get("type") or 1), "flags": int(c.get("flags") or 0),
        "out_path_len": int(c.get("out_path_len", -1) if c.get("out_path_len") is not None else -1),
        "out_path": c.get("out_path") or "", "out_path_hash_mode": int(c.get("out_path_hash_mode") or 0),
        "adv_name": str(c.get("adv_name") or "")[:31], "last_advert": int(c.get("last_advert") or 0),
        "adv_lat": float(c.get("adv_lat") or 0.0), "adv_lon": float(c.get("adv_lon") or 0.0),
    }


async def snapshot(mc, device: dict | None = None, previous: dict | None = None) -> dict:
    """Read the node's identity and settings into a profile dict. The
    private key comes from the node when its firmware allows the export;
    otherwise the previous profile's key is kept (it cannot have changed:
    the public key is the same)."""
    si = dict(mc.self_info or {})
    device = device or {}
    prof = {
        "version": 1,
        "saved_at": time.time(),
        "name": si.get("name"),
        "public_key": si.get("public_key"),
        "private_key": None,
        "key_export": "unknown",
        "adv_lat": si.get("adv_lat"), "adv_lon": si.get("adv_lon"),
        "radio": {"freq_mhz": si.get("radio_freq"), "bw_khz": si.get("radio_bw"),
                  "sf": si.get("radio_sf"), "cr": si.get("radio_cr")},
        "tx_power": si.get("tx_power"),
        "model": device.get("model"), "fw": device.get("ver"),
        "contacts": [],
        "history": list((previous or {}).get("history") or []),
    }
    try:
        res = await mc.commands.export_private_key()
        if res.type == EventType.PRIVATE_KEY:
            key = (res.payload or {}).get("private_key")
            if isinstance(key, (bytes, bytearray)) and len(key) == 64:
                prof["private_key"] = bytes(key).hex()
                prof["key_export"] = "ok"
        elif res.type == EventType.DISABLED:
            prof["key_export"] = "disabled by firmware"
        else:
            prof["key_export"] = f"error: {res.payload}"
    except Exception as e:
        prof["key_export"] = f"error: {e}"
    if not prof["private_key"] and previous and previous.get("public_key") == prof["public_key"]:
        prof["private_key"] = previous.get("private_key")
    contacts = []
    for c in (mc.contacts or {}).values():
        if c.get("type") != 1:
            continue                     # only people: repeaters and rooms are re-learned from adverts
        rec = contact_record(c)
        if rec:
            contacts.append((int(c.get("lastmod") or 0), rec))
    contacts.sort(key=lambda t: t[0], reverse=True)
    prof["contacts"] = [rec for _, rec in contacts[:MAX_CONTACTS_RESTORED]]
    return prof


def differs(profile: dict | None, self_info: dict | None) -> bool:
    """True when the radio on the port is not the node in the profile."""
    if not profile or not profile.get("public_key"):
        return False
    key = (self_info or {}).get("public_key")
    return bool(key) and key != profile["public_key"]


def can_adopt(profile: dict | None) -> str | None:
    """None when adoption is possible, else why not."""
    if not profile:
        return "no profile saved yet"
    if not profile.get("private_key"):
        return "the profile has no private key (" + str(profile.get("key_export") or "export never succeeded") + ")"
    return None


async def adopt(mc, profile: dict) -> list[str]:
    """Write the profile onto the connected node. Returns the steps done.
    The caller reboots the node and verifies the public key afterwards."""
    steps: list[str] = []
    res = await mc.commands.import_private_key(bytes.fromhex(profile["private_key"]))
    if res.type != EventType.OK:
        raise RuntimeError(f"node refused the identity key: {res.payload}")
    steps.append("identity key imported")

    si = dict(mc.self_info or {})
    if profile.get("name"):
        res = await mc.commands.set_name(profile["name"])
        steps.append("name " + (profile["name"] if res.type == EventType.OK else "refused"))
    if profile.get("adv_lat") is not None and profile.get("adv_lon") is not None:
        res = await mc.commands.set_coords(float(profile["adv_lat"]), float(profile["adv_lon"]))
        steps.append("position " + ("set" if res.type == EventType.OK else "refused"))
    r = profile.get("radio") or {}
    if all(r.get(k) is not None for k in ("freq_mhz", "bw_khz", "sf", "cr")):
        res = await mc.commands.set_radio(float(r["freq_mhz"]), float(r["bw_khz"]), int(r["sf"]), int(r["cr"]))
        steps.append(f"radio {r['freq_mhz']} MHz / {r['bw_khz']} kHz / SF{r['sf']} / CR{r['cr']} "
                     + ("set" if res.type == EventType.OK else "refused"))
    if profile.get("tx_power") is not None:
        # A different board may not reach the old power: the firmware
        # reports its ceiling and gets the nearest value under it.
        want = int(profile["tx_power"])
        ceiling = si.get("max_tx_power")
        dbm = min(want, int(ceiling)) if ceiling else want
        res = await mc.commands.set_tx_power(dbm)
        steps.append(f"tx power {dbm} dBm" + (f" (profile had {want}, this board tops out at {ceiling})"
                                              if dbm != want else "") + ("" if res.type == EventType.OK else " refused"))
    restored = failed = 0
    for c in profile.get("contacts") or []:
        try:
            res = await mc.commands.add_contact(dict(c))
            if res.type == EventType.OK:
                restored += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    if restored or failed:
        steps.append(f"{restored} contacts restored" + (f", {failed} refused" if failed else ""))
    return steps


def record_adoption(profile: dict, *, from_key: str | None, model: str | None, fw: str | None,
                    steps: list[str], ok: bool, note: str = "") -> None:
    hist = profile.setdefault("history", [])
    hist.append({"t": time.time(), "radio_key": from_key, "model": model, "fw": fw,
                 "ok": ok, "steps": steps, "note": note})
    del hist[:-20]
