"""Number entities: keep-warm temperature and keep-warm time limit."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MiKettleConfigEntry
from .coordinator import MiKettleCoordinator
from .entity import MiKettleControlEntity
from .kettle import TEMP_MAX, TEMP_MIN

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MiKettleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the number entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            MiKettleTemperatureNumber(coordinator),
            MiKettleTimeLimitNumber(coordinator),
        ]
    )


class MiKettleTemperatureNumber(MiKettleControlEntity, NumberEntity):
    """Target keep-warm temperature (written to aa01)."""

    _attr_translation_key = "keep_warm_temperature"
    _attr_native_min_value = TEMP_MIN
    _attr_native_max_value = TEMP_MAX
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: MiKettleCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator, "keep_warm_temperature")

    @property
    def native_value(self) -> float | None:
        """Return the configured keep-warm temperature."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.target_temperature

    async def async_set_native_value(self, value: float) -> None:
        """Write the temperature; aa01 needs the keep-warm type as well."""
        keep_warm_type = await self.coordinator.async_require_keep_warm_type()
        await self.coordinator.async_apply(keep_warm=(keep_warm_type, int(value)))


class MiKettleTimeLimitNumber(MiKettleControlEntity, NumberEntity):
    """How long to keep the water warm before switching off (written to aa04)."""

    _attr_translation_key = "keep_warm_time_limit"
    _attr_native_min_value = 0
    _attr_native_max_value = 12
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: MiKettleCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator, "keep_warm_time_limit")

    @property
    def native_value(self) -> float | None:
        """Return the configured keep-warm time limit in hours."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.keep_warm_limit

    async def async_set_native_value(self, value: float) -> None:
        """Write the time limit as half-hour steps."""
        await self.coordinator.async_apply(half_hours=int(round(value * 2)))
