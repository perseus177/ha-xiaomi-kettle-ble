"""Select entities: keep-warm mode and temperature presets."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MiKettleConfigEntry
from .const import DOMAIN
from .coordinator import MiKettleCoordinator
from .entity import MiKettleControlEntity
from .kettle import KEEP_WARM_TYPE_MAP

PARALLEL_UPDATES = 1

# Presets offered by the official Xiaomi app (temperature -> purpose).
PRESETS = {"p90": 90, "p80": 80, "p70": 70, "p50": 50, "p40": 40}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MiKettleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the select entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [MiKettleKeepWarmTypeSelect(coordinator), MiKettlePresetSelect(coordinator)]
    )


class MiKettleKeepWarmTypeSelect(MiKettleControlEntity, SelectEntity):
    """Keep-warm type - the first byte of aa01."""

    _attr_translation_key = "keep_warm_type"
    _attr_options = ["cool_down", "warm_up"]

    def __init__(self, coordinator: MiKettleCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator, "keep_warm_type")

    @property
    def current_option(self) -> str | None:
        """Return the configured keep-warm type."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.keep_warm_type

    async def async_select_option(self, option: str) -> None:
        """Write the type; aa01 needs the temperature as well."""
        state = self.coordinator.data
        if state is None or state.target_temperature is None:
            await self.coordinator.async_refresh()
            state = self.coordinator.data
        if state is None or state.target_temperature is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="unknown_target_temperature"
            )
        codes = {name: code for code, name in KEEP_WARM_TYPE_MAP.items()}
        await self.coordinator.async_apply(
            keep_warm=(codes[option], state.target_temperature)
        )


class MiKettlePresetSelect(MiKettleControlEntity, SelectEntity):
    """Temperature presets mirroring the app (coffee, white tea, milk, ...).

    This is not a kettle feature - the app simply writes that temperature to
    aa01. The same is done here so the number box does not have to be used.
    """

    _attr_translation_key = "preset"
    _attr_options = list(PRESETS)

    def __init__(self, coordinator: MiKettleCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator, "preset")

    @property
    def current_option(self) -> str | None:
        """Return the preset only when the configured temperature matches one."""
        if self.coordinator.data is None:
            return None
        target = self.coordinator.data.target_temperature
        for key, temperature in PRESETS.items():
            if temperature == target:
                return key
        return None

    async def async_select_option(self, option: str) -> None:
        """Write the preset temperature, keeping the current keep-warm type."""
        keep_warm_type = await self.coordinator.async_require_keep_warm_type()
        await self.coordinator.async_apply(keep_warm=(keep_warm_type, PRESETS[option]))
