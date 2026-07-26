"""The MiKettle BLE integration - local control of a Xiaomi Mi Smart Kettle."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_PAUSED,
    CONF_POLL_INTERVAL,
    CONF_PRODUCT_ID,
    CONF_TOKEN,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PRODUCT_ID,
    DOMAIN,
    PLATFORMS,
    SERVICE_PROBE,
)
from .coordinator import MiKettleCoordinator
from .kettle import MiKettleClient

_LOGGER = logging.getLogger(__name__)

type MiKettleConfigEntry = ConfigEntry[MiKettleCoordinator]

PROBE_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Required("characteristic"): cv.string,
        vol.Optional("payload"): cv.string,
        vol.Optional("listen_seconds", default=3.0): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=15)
        ),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: MiKettleConfigEntry) -> bool:
    """Set up MiKettle BLE from a config entry."""
    address: str = entry.data[CONF_ADDRESS].upper()
    client = MiKettleClient(
        address=address,
        product_id=entry.data.get(CONF_PRODUCT_ID, DEFAULT_PRODUCT_ID),
        token=bytes.fromhex(entry.data[CONF_TOKEN]),
    )
    coordinator = MiKettleCoordinator(
        hass,
        entry,
        client,
        address,
        entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
    )

    # Load the pause flag BEFORE the first read so that a restart does not touch
    # the kettle while the integration is paused.
    coordinator.paused = entry.options.get(CONF_PAUSED, False)

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)

    # Read the state once after setup, but do NOT block startup on it: a BLE round
    # trip (connect + authenticate + read) takes seconds, and tens of seconds when
    # the kettle answers slowly. Deliberately not async_config_entry_first_refresh()
    # either - a kettle that is off its base must not fail the setup; the entities
    # simply stay without values until the first successful read.
    if not coordinator.paused:
        entry.async_create_background_task(
            hass, coordinator.async_refresh(), f"{DOMAIN}_initial_refresh"
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: MiKettleConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: MiKettleConfigEntry) -> None:
    """Propagate a BLE pause toggle to the coordinator.

    Nothing is reloaded here on purpose - reloading on an options change is
    handled by OptionsFlowWithReload. The pause is only a flag; a reload would
    needlessly rebuild the entities and cancel any running operation.
    """
    entry.runtime_data.paused = entry.options.get(CONF_PAUSED, False)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the diagnostic action once per Home Assistant instance."""
    if hass.services.has_service(DOMAIN, SERVICE_PROBE):
        return

    async def _async_probe(call: ServiceCall) -> ServiceResponse:
        """Subscribe to a characteristic and optionally write to it.

        Without a payload this is a safe read. With a payload it is a deliberate
        experiment on a live appliance, meant for tracking down undocumented
        settings (for example "Extended Warm Up", which is not exposed on any
        readable characteristic).
        """
        entries = [
            entry
            for entry in hass.config_entries.async_loaded_entries(DOMAIN)
            if call.data.get("entry_id") in (None, entry.entry_id)
        ]
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_kettle_loaded"
            )
        if len(entries) > 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="multiple_kettles"
            )

        raw = call.data.get("payload")
        payload: bytes | None = None
        if raw:
            try:
                payload = bytes.fromhex(raw.replace(" ", "").replace(":", ""))
            except ValueError as err:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="invalid_payload",
                    translation_placeholders={"payload": raw},
                ) from err

        return await entries[0].runtime_data.async_probe(
            call.data["characteristic"], payload, call.data["listen_seconds"]
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_PROBE,
        _async_probe,
        schema=PROBE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
