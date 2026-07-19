"""Switches for Kydax Light: pause buttons and the gradation switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import KydaxConfigEntry
from .const import CONF_PAUSE_BUTTONS, ZONE_DEFAULT
from .coordinator import KydaxEngine
from .entity import KydaxEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KydaxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one gradation switch per zone and one pause switch per button."""
    engine = entry.runtime_data
    entities: list[SwitchEntity] = [
        KydaxGradationSwitch(engine, zone.zone_id, zone.name)
        for zone in engine.zones
    ]
    entities.extend(
        KydaxPauseSwitch(engine, button)
        for button in entry.options.get(CONF_PAUSE_BUTTONS, [])
    )
    entities.append(KydaxAutoUpdateSwitch(engine))
    async_add_entities(entities)


class KydaxAutoUpdateSwitch(KydaxEntity, SwitchEntity, RestoreEntity):
    """Opt-in: install pending updates during the 4 AM window. Default off.

    Releases marked [critical] install at that window regardless.
    """

    _attr_translation_key = "auto_update"
    _attr_icon = "mdi:update"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, engine: KydaxEngine) -> None:
        super().__init__(engine)
        self._attr_unique_id = f"{engine.entry.entry_id}_auto_update"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_ON:
            self._engine.auto_update_enabled = True

    @property
    def is_on(self) -> bool:
        return self._engine.auto_update_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._engine.auto_update_enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._engine.auto_update_enabled = False
        self.async_write_ha_state()


class KydaxPauseSwitch(KydaxEntity, SwitchEntity, RestoreEntity):
    """On = this pause is active right now. Independent of other pauses."""

    _attr_icon = "mdi:pause-circle"

    def __init__(self, engine: KydaxEngine, button: dict) -> None:
        super().__init__(engine)
        self._button = button
        self._attr_name = button["name"]
        self._attr_unique_id = f"{engine.entry.entry_id}_pause_{button['id']}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_ON:
            self._engine.set_button_paused(
                self._button["id"], True, dispatch=False
            )

    @property
    def is_on(self) -> bool:
        return self._engine.is_button_paused(self._button["id"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self._button.get("all"):
            return {"scope": "all"}
        return {
            "scope": "group",
            "lights": self._button.get("lights", []),
            "zones": self._button.get("zones", []),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._engine.set_button_paused(self._button["id"], True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._engine.set_button_paused(self._button["id"], False)


class KydaxGradationSwitch(KydaxEntity, SwitchEntity):
    """On while the zone's evening dim session runs; turn off to cancel it."""

    _attr_icon = "mdi:weather-sunset-down"

    def __init__(self, engine: KydaxEngine, zone_id: str, zone_name: str) -> None:
        super().__init__(engine)
        self._zone_id = zone_id
        if zone_id == ZONE_DEFAULT:
            # keeps the pre-zones unique_id so history and dashboards survive
            self._attr_unique_id = f"{engine.entry.entry_id}_gradation"
            self._attr_translation_key = "gradation"
        else:
            self._attr_unique_id = f"{engine.entry.entry_id}_gradation_{zone_id}"
            self._attr_translation_key = "zone_gradation"
            self._attr_translation_placeholders = {"zone": zone_name}

    @property
    def is_on(self) -> bool:
        zone = self._engine.get_zone(self._zone_id)
        return zone is not None and zone.session is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        zone = self._engine.get_zone(self._zone_id)
        if zone is None:
            return {}
        attrs: dict[str, Any] = {
            "lights": zone.lights,
            "illuminance": zone.lux,
            "reduction": zone.reduction,
        }
        if zone.session is not None:
            attrs["started"] = zone.session.started.isoformat()
            attrs["progress"] = {
                entity_id: {"step": p.step, "done": p.done}
                for entity_id, p in zone.session.lights.items()
            }
        return attrs

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._engine.async_start_session(self._zone_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._engine.async_cancel_session(self._zone_id)
