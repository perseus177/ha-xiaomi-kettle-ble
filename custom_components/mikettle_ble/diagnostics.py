"""Diagnostics support for MiKettle BLE."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import MiKettleConfigEntry
from .const import CONF_TOKEN

TO_REDACT = {CONF_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MiKettleConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
            "source": entry.source,
        },
        "coordinator": {
            "paused": coordinator.paused,
            "poll_interval": coordinator.poll_interval,
            "last_update_success": coordinator.last_update_success,
            "state": asdict(coordinator.data) if coordinator.data else None,
        },
    }
