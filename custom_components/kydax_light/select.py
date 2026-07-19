"""Preset select for Kydax Light."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import KydaxConfigEntry
from .const import PRESETS
from .coordinator import KydaxEngine
from .entity import KydaxEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KydaxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([KydaxPresetSelect(entry.runtime_data)])


class KydaxPresetSelect(KydaxEntity, SelectEntity, RestoreEntity):
    """Day / Evening / Night / None. Selecting applies the preset now."""

    _attr_translation_key = "preset"
    _attr_options = PRESETS
    _attr_icon = "mdi:palette-outline"

    def __init__(self, engine: KydaxEngine) -> None:
        super().__init__(engine)
        self._attr_unique_id = f"{engine.entry.entry_id}_preset"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._engine.restore_preset(last.state)

    @property
    def current_option(self) -> str:
        return self._engine.active_preset

    async def async_select_option(self, option: str) -> None:
        await self._engine.async_apply_preset(option)
