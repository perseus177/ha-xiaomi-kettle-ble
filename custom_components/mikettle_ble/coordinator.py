"""Coordinator for MiKettle BLE - serialises the short-lived BLE sessions."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import timedelta

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .kettle import (
    KEEP_WARM_TYPE_MAP,
    MiKettleAuthError,
    MiKettleBusyError,
    MiKettleClient,
    MiKettleError,
    MiKettleState,
)

_LOGGER = logging.getLogger(__name__)


class MiKettleCoordinator(DataUpdateCoordinator[MiKettleState | None]):
    """Holds the kettle state and makes sure only one BLE session runs at a time."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: MiKettleClient,
        address: str,
        poll_interval: int,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(minutes=poll_interval) if poll_interval else None,
        )
        self.client = client
        self.address = address
        self.poll_interval = poll_interval
        # While paused, the integration does not connect to the kettle at all -
        # neither to read nor to write - so BLE stays free for the Xiaomi app.
        self.paused = False
        self._lock = asyncio.Lock()

    def _get_device(self) -> bluetooth.BLEDevice:
        """Return the BLEDevice for an active connection."""
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_in_range",
                translation_placeholders={"address": self.address},
            )
        return device

    def _ensure_not_paused(self) -> None:
        """Block every BLE operation while the pause switch is on."""
        if self.paused:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="ble_paused"
            )

    def _as_ha_error(self, err: Exception) -> HomeAssistantError:
        """Translate a protocol error into a user-facing, translatable error."""
        if isinstance(err, MiKettleBusyError):
            key = "device_busy"
        elif isinstance(err, MiKettleAuthError):
            key = "auth_failed"
        else:
            key = "communication_failed"
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders={"address": self.address, "details": str(err)},
        )

    async def _async_update_data(self) -> MiKettleState | None:
        """Read the state - used by the refresh button and by polling."""
        if self.paused:
            _LOGGER.debug("BLE pause is on - skipping the state read")
            return self.data
        try:
            async with self._lock:
                return await self.client.async_read_state(self._get_device())
        except HomeAssistantError as err:
            raise UpdateFailed(str(err)) from err
        except MiKettleError as err:
            raise UpdateFailed(str(err)) from err

    async def async_manual_refresh(self) -> None:
        """Refresh triggered by the button - reports failures to the user."""
        self._ensure_not_paused()
        await self.async_refresh()
        if not self.last_update_success:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="refresh_failed",
                translation_placeholders={"details": str(self.last_exception or "")},
            )

    def _optimistic_state(self, previous: MiKettleState | None, changes: dict):
        """Build the state the kettle will have once the write lands.

        A full BLE session (connect, authenticate, write, wait for the status
        notification, read aa04/aa05, disconnect) takes several seconds, so
        publishing only the verified state makes the UI look frozen. The requested
        value is therefore published straight away and reconciled afterwards.
        """
        if previous is None:
            return None
        updates = {}
        keep_warm = changes.get("keep_warm")
        if keep_warm is not None:
            updates["keep_warm_type"] = KEEP_WARM_TYPE_MAP.get(keep_warm[0])
            updates["target_temperature"] = keep_warm[1]
        half_hours = changes.get("half_hours")
        if half_hours is not None:
            updates["keep_warm_limit"] = half_hours / 2
        turn_off_after_boil = changes.get("turn_off_after_boil")
        if turn_off_after_boil is not None:
            updates["turn_off_after_boil"] = turn_off_after_boil
        if not updates:
            return None
        return replace(previous, **updates)

    async def async_apply(self, **kwargs) -> None:
        """Write settings and pick up the new state.

        The write and the read share one connection, which is closed immediately.
        The requested value is published optimistically first so the UI reacts at
        once; it is replaced by the verified state when the session finishes, or
        rolled back if the write fails.
        """
        self._ensure_not_paused()
        previous = self.data
        optimistic = self._optimistic_state(previous, kwargs)
        if optimistic is not None:
            self.async_set_updated_data(optimistic)
        try:
            async with self._lock:
                state = await self.client.async_apply(self._get_device(), **kwargs)
        except MiKettleError as err:
            if optimistic is not None:
                self.async_set_updated_data(previous)
            raise self._as_ha_error(err) from err
        except ValueError as err:
            if optimistic is not None:
                self.async_set_updated_data(previous)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_value",
                translation_placeholders={"details": str(err)},
            ) from err
        except HomeAssistantError:
            if optimistic is not None:
                self.async_set_updated_data(previous)
            raise
        self.async_set_updated_data(state)

    async def async_require_keep_warm_type(self) -> int:
        """Return the current keep-warm type (0/1).

        aa01 is written as [type, temperature] in one go, so without knowing the
        current type a temperature change would overwrite it blindly. If it is
        unknown, try one read and otherwise fail with an explanation.
        """
        state = self.data
        if state is None or state.keep_warm_type is None:
            await self.async_refresh()
            state = self.data
        if state is None or state.keep_warm_type is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="unknown_keep_warm_type"
            )
        codes = {name: code for code, name in KEEP_WARM_TYPE_MAP.items()}
        return codes[state.keep_warm_type]

    async def async_probe(
        self, characteristic: str, payload: bytes | None, listen_seconds: float
    ) -> dict[str, object]:
        """Diagnose a single characteristic (notifications + optional write)."""
        self._ensure_not_paused()
        try:
            async with self._lock:
                return await self.client.async_probe(
                    self._get_device(), characteristic, payload, listen_seconds
                )
        except MiKettleError as err:
            raise self._as_ha_error(err) from err

    async def async_dump_characteristics(self) -> dict[str, str]:
        """Dump every characteristic into the log."""
        self._ensure_not_paused()
        try:
            async with self._lock:
                return await self.client.async_dump_characteristics(self._get_device())
        except MiKettleError as err:
            raise self._as_ha_error(err) from err
