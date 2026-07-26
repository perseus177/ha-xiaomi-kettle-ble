"""Sensor entities - the state read during the last connection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MiKettleConfigEntry
from .coordinator import MiKettleCoordinator
from .entity import MiKettleEntity
from .kettle import MiKettleState

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class MiKettleSensorDescription(SensorEntityDescription):
    """Describes a kettle sensor and how to read its value from the state."""

    value_fn: Callable[[MiKettleState], float | str | None]


SENSORS: tuple[MiKettleSensorDescription, ...] = (
    MiKettleSensorDescription(
        key="current_temperature",
        translation_key="current_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.current_temperature,
    ),
    MiKettleSensorDescription(
        key="target_temperature",
        translation_key="target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda state: state.target_temperature,
    ),
    MiKettleSensorDescription(
        key="action",
        translation_key="action",
        device_class=SensorDeviceClass.ENUM,
        options=["idle", "heating", "cooling", "keep_warm"],
        value_fn=lambda state: state.action,
    ),
    MiKettleSensorDescription(
        key="mode",
        translation_key="mode",
        device_class=SensorDeviceClass.ENUM,
        options=["none", "boil", "keep_warm"],
        value_fn=lambda state: state.mode,
    ),
    MiKettleSensorDescription(
        key="keep_warm_elapsed",
        translation_key="keep_warm_elapsed",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda state: state.keep_warm_elapsed,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MiKettleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(MiKettleSensor(coordinator, description) for description in SENSORS)


class MiKettleSensor(MiKettleEntity, SensorEntity):
    """A single kettle sensor."""

    entity_description: MiKettleSensorDescription

    def __init__(
        self, coordinator: MiKettleCoordinator, description: MiKettleSensorDescription
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        """Return the value from the last read state."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
