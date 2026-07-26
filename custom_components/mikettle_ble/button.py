"""Button entities: refresh the state and dump the characteristics."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MiKettleConfigEntry
from .coordinator import MiKettleCoordinator
from .entity import MiKettleControlEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MiKettleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    coordinator = entry.runtime_data
    async_add_entities(
        [MiKettleRefreshButton(coordinator), MiKettleDumpButton(coordinator)]
    )


class MiKettleRefreshButton(MiKettleControlEntity, ButtonEntity):
    """Read the state once (a short connection)."""

    _attr_translation_key = "refresh"

    def __init__(self, coordinator: MiKettleCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator, "refresh")

    async def async_press(self) -> None:
        """Refresh the state now."""
        await self.coordinator.async_manual_refresh()


class MiKettleDumpButton(MiKettleControlEntity, ButtonEntity):
    """Log every characteristic - used to hunt for undocumented settings."""

    _attr_translation_key = "dump_characteristics"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: MiKettleCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator, "dump_characteristics")

    async def async_press(self) -> None:
        """Dump all characteristics into the log."""
        dump = await self.coordinator.async_dump_characteristics()
        _LOGGER.warning(
            "MiKettle %s - characteristics dump:\n%s",
            self.coordinator.address,
            "\n".join(f"  {key} = {value}" for key, value in sorted(dump.items())),
        )
