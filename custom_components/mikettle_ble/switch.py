"""Switch entities: boil mode (aa05) and the BLE pause kill switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MiKettleConfigEntry
from .const import CONF_PAUSED
from .coordinator import MiKettleCoordinator
from .entity import MiKettleControlEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MiKettleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switch entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [MiKettleBoilModeSwitch(coordinator), MiKettleBlePauseSwitch(coordinator, entry)]
    )


class MiKettleBoilModeSwitch(MiKettleControlEntity, SwitchEntity):
    """aa05: 1 = switch off after boiling, 0 = fall through to keep warm.

    This is the "Do not boil again" setting of the official app (confirmed by
    watching the app toggle it).
    """

    _attr_translation_key = "turn_off_after_boil"

    def __init__(self, coordinator: MiKettleCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator, "turn_off_after_boil")
        self._optimistic: bool | None = None

    @property
    def is_on(self) -> bool | None:
        """Return the last known value; aa05 is not readable everywhere."""
        if self.coordinator.data is not None:
            value = self.coordinator.data.turn_off_after_boil
            if value is not None:
                return value
        return self._optimistic

    @property
    def assumed_state(self) -> bool:
        """Return True when the value could not be read back."""
        return (
            self.coordinator.data is None
            or self.coordinator.data.turn_off_after_boil is None
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch off the kettle after boiling."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Keep the kettle running after boiling."""
        await self._async_set(False)

    async def _async_set(self, value: bool) -> None:
        await self.coordinator.async_apply(turn_off_after_boil=value)
        self._optimistic = value
        self.async_write_ha_state()


class MiKettleBlePauseSwitch(MiKettleControlEntity, SwitchEntity):
    """Kill switch: while on, the integration never connects to the kettle.

    The state is stored in the config entry options so that it survives a restart,
    including the state read performed during setup.
    """

    _attr_translation_key = "ble_pause"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: MiKettleCoordinator, entry: MiKettleConfigEntry
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator, "ble_pause")
        self._entry = entry

    @property
    def is_on(self) -> bool:
        """Return True when BLE access is paused."""
        return self.coordinator.paused

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Stop the integration from using BLE."""
        await self._async_set_paused(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Allow the integration to use BLE again."""
        await self._async_set_paused(False)

    async def _async_set_paused(self, value: bool) -> None:
        # Set the flag straight away for an immediate UI response; the write to the
        # options is what makes the pause survive a restart. The update listener
        # sets the same value.
        self.coordinator.paused = value
        self.async_write_ha_state()
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_PAUSED: value}
        )
