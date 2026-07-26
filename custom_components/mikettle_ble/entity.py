"""Base entities for MiKettle BLE."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import MiKettleCoordinator


class MiKettleEntity(CoordinatorEntity[MiKettleCoordinator]):
    """Common base class for the kettle entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MiKettleCoordinator, key: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            identifiers={(DOMAIN, coordinator.address)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name="Mi Kettle",
        )


class MiKettleControlEntity(MiKettleEntity):
    """A control entity.

    It stays available even when the last read failed (the kettle may be off its
    base) - otherwise no value could be set at all. It shows the last known value
    and reports a failed write as an error.
    """

    @property
    def available(self) -> bool:
        """Return True - controls are always operable."""
        return True
