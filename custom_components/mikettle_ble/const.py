"""Constants for the MiKettle BLE integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "mikettle_ble"

CONF_PRODUCT_ID = "product_id"
CONF_TOKEN = "token"
CONF_POLL_INTERVAL = "poll_interval"
# The pause flag lives in the config entry options so that it survives a restart,
# including the state read that happens during setup.
CONF_PAUSED = "paused"

# The product ID MUST match the kettle: mix_a()/mix_b() in the auth handshake are
# derived from it, so a wrong value ends with ATT 0x0E (Unlikely Error). The kettle
# itself reports the right model in characteristic 2a24, and Xiaomi-cloud-tokens-
# extractor prints it as MODEL. Discovery reads it from the BLE advertisement.
DEFAULT_PRODUCT_ID = 131
PRODUCT_IDS = {
    131: "yunmi.kettle.v1 (product ID 131)",
    275: "yunmi.kettle.v2 (product ID 275)",
    1116: "yunmi.kettle.v7 (product ID 1116)",
}

# 0 = no periodic polling (recommended - keeps the kettle free for the Xiaomi app)
DEFAULT_POLL_INTERVAL = 0

MANUFACTURER = "Xiaomi"
MODEL = "Mi Smart Kettle"

SERVICE_PROBE = "probe_characteristic"

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]
