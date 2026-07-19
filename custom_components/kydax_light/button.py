"""Test buttons for Kydax Light.

Per zone: start a dim session immediately with the configured delays, or a
fast one stepping every TEST_STEP_SECONDS. Neither consumes the daily
autostart, so testing during the day leaves the evening schedule intact.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KydaxConfigEntry
from .const import ZONE_DEFAULT
from .coordinator import KydaxEngine
from .entity import KydaxEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KydaxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    engine = entry.runtime_data
    entities: list[ButtonEntity] = [KydaxStopTestsButton(engine)]
    for zone in engine.zones:
        entities.append(KydaxTestButton(engine, zone.zone_id, zone.name, fast=False))
        entities.append(KydaxTestButton(engine, zone.zone_id, zone.name, fast=True))
    async_add_entities(entities)


class KydaxStopTestsButton(KydaxEntity, ButtonEntity):
    """Stop every running test and restore pre-test brightness everywhere."""

    _attr_translation_key = "stop_tests"
    _attr_icon = "mdi:stop"

    def __init__(self, engine: KydaxEngine) -> None:
        super().__init__(engine)
        self._attr_unique_id = f"{engine.entry.entry_id}_stop_tests"

    async def async_press(self) -> None:
        await self._engine.async_cancel_all_tests()


class KydaxTestButton(KydaxEntity, ButtonEntity):
    """Start a test dim session for one zone."""

    def __init__(
        self, engine: KydaxEngine, zone_id: str, zone_name: str, fast: bool
    ) -> None:
        super().__init__(engine)
        self._zone_id = zone_id
        self._fast = fast
        suffix = "test_fast" if fast else "test"
        self._attr_unique_id = f"{engine.entry.entry_id}_{suffix}_{zone_id}"
        self._attr_icon = "mdi:fast-forward" if fast else "mdi:play"
        if zone_id == ZONE_DEFAULT:
            self._attr_translation_key = (
                "test_gradation_fast" if fast else "test_gradation"
            )
        else:
            self._attr_translation_key = (
                "zone_test_gradation_fast" if fast else "zone_test_gradation"
            )
            self._attr_translation_placeholders = {"zone": zone_name}

    async def async_press(self) -> None:
        await self._engine.async_start_session(
            self._zone_id, fast=self._fast, mark_day=False
        )
