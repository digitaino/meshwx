"""Application configuration via environment variables and .env file."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {
        "env_prefix": "MCW_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    # Meshcore serial connection
    serial_port: str = "/dev/cu.usbserial-0001"
    serial_baud: int = 115200
    meshcore_channel: str = "#meshwx"  # request channel: one name for every bot, everywhere

    # EMWIN data source
    emwin_source: str = "internet"  # "internet" or "sdr"
    emwin_poll_interval: int = 120  # seconds between data refreshes
    # "sdr": goesproc's emwin output tree (YYYY-MM-DD/ dirs of .TXT files)
    sdr_emwin_dir: str = "~/goes-images/emwin"
    sdr_poll_interval: int = 30     # seconds between directory scans
    # goestools dashboard (signal stats, pointing mode) proxied into the admin portal
    sdr_dashboard_url: str = "http://127.0.0.1:8080"
    # How often the bot floods an advert so phones can DM it (hours)
    advert_interval_hours: int = 6
    # How text replies go out:
    #   dm       DM when we know the sender; a stranger who is close (few hops)
    #            gets one reply on our channel and an advert so the next
    #            exchange can be a DM
    #   channel  always reply on the channel (flood) — for testing/coverage
    #            demos; every repeater in range carries every reply
    #   dm_only  DM or silence, never a channel reply
    reply_mode: str = "dm"
    channel_reply_max_hops: int = 2       # strangers further than this get no channel reply
    peer_bot_prefix: str = "WX-"          # other weather bots advert with this name prefix
    # The node's contact table is what makes DMs possible: a person the node
    # has not stored cannot be DMed. The firmware reports its capacity (350 on
    # a Heltec V3); contact_slots is only the fallback when it does not.
    # Housekeeping removes repeaters, rooms and sensors from it (a DM path is
    # a list of repeater hashes, not contacts) and, when the people alone
    # approach the limit, the ones heard longest ago. The admin key and
    # peer bots are never removed. Off = the firmware's own behaviour.
    contact_housekeeping: bool = True
    contact_slots: int = 100              # fallback when DEVICE_INFO has no max_contacts
    contact_keep_free: int = 10           # room left for newcomers after a housekeeping run
    # Initial load uses 1-hour bundle for coverage, then polls 2-minute bundle
    emwin_base_url: str = "https://tgftp.nws.noaa.gov/SL.us008001/CU.EMWIN/DF.xt/DC.gsatR/OPS/txthrs01.zip"
    emwin_poll_url: str = "https://tgftp.nws.noaa.gov/SL.us008001/CU.EMWIN/DF.xt/DC.gsatR/OPS/txtmin02.zip"
    emwin_max_age_hours: int = 12  # Expire products older than this

    # Data storage
    data_dir: Path = Path("data")

    # Data channel: the binary broadcasts for the app (v4 frames). Empty = off.
    meshwx_channel: str = "#meshwx-data"
    # Discovery channel: apps ping here, the bot answers with a beacon
    meshwx_discover_channel: str = "#meshwx-discover"
    meshwx_refresh_cooldown: int = 300    # min seconds between app refreshes per region

    # Coverage targeting — bot broadcasts only data affecting these areas.
    # Comma-separated lists, all optional, all additive (union). Empty = broadcast everything.
    home_cities: str = ""  # e.g. "Austin TX,San Antonio TX,Dallas TX"
    # Coverage radius (km) around the FIRST home city. The bot covers every
    # public forecast zone whose polygon lies within this circle, plus any
    # extra cities/states/WFOs listed. 0 disables the radius.
    home_radius_km: int = 120
    home_states: str = ""  # e.g. "TX,OK"
    home_wfos: str = ""    # e.g. "EWX,FWD,HGX"

    # Local web portal
    portal_enabled: bool = False
    portal_host: str = "0.0.0.0"
    portal_port: int = 8080

    # Admin: pubkey prefix of admin user (can run admin DM commands)
    admin_key: str = ""

    # Radio transmit master switch. False = receive-only passive observer:
    # every outbound RF path (adverts, channel messages, binary broadcasts,
    # discovery beacons, DMs) becomes a logged no-op. Receiving, MQTT
    # publishing and the web portal are unaffected.
    tx_enabled: bool = True

    # MQTT: optional fire-and-forget publishing of received RF packets to a
    # broker for downstream consumers (e.g. CoreScope). Disabled by default;
    # turn on only after verifying the bot still works with mqtt_enabled=False.
    mqtt_enabled: bool = False
    mqtt_host: str = "mosquitto"  # Docker service name of the broker
    mqtt_port: int = 1883
    mqtt_topic_prefix: str = "meshcore"
    mqtt_iata: str = "AUS"
    mqtt_username: str = ""
    mqtt_password: str = ""
    # "origin" field in each published JSON envelope — this is the
    # observer name CoreScope and other consumers display.
    mqtt_origin: str = "meshcore-weather"

    # Local time zone for human-readable replies (IANA name). Wire messages
    # always carry UTC; only the text renderer uses this.
    timezone: str = "America/Chicago"

    # Logging
    log_level: str = "INFO"


settings = Settings()
