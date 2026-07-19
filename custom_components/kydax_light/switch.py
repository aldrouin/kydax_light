"""Switches for Kydax Light: pause buttons and the gradation switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import KydaxConfigEntry
from .const import CONF_PAUSE_BUTTONS
from .coordinator import KydaxEngine
from .entity import KydaxEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KydaxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the gradation switch and one pause switch per configured button."""
    engine = entry.runtime_data
    entities: list[SwitchEntity] = [KydaxGradationSwitch(engine)]
    entities.extend(
        KydaxPauseSwitch(engine, button)
        for button in entry.options.get(CONF_PAUSE_BUTTONS, [])
    )
    async_add_entities(entities)


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
        return {"scope": "group", "lights": self._button.get("lights", [])}

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._engine.set_button_paused(self._button["id"], True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._engine.set_button_paused(self._button["id"], False)


class KydaxGradationSwitch(KydaxEntity, SwitchEntity):
    """On while the evening dim session runs; turn off to cancel it."""

    _attr_translation_key = "gradation"
    _attr_icon = "mdi:weather-sunset-down"

    def __init__(self, engine: KydaxEngine) -> None:
        super().__init__(engine)
        self._attr_unique_id = f"{engine.entry.entry_id}_gradation"

    @property
    def is_on(self) -> bool:
        return self._engine.session is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        session = self._engine.session
        if session is None:
            return {}
        return {
            "started": session.started.isoformat(),
            "lights": {
                entity_id: {"step": p.step, "done": p.done}
                for entity_id, p in session.lights.items()
            },
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._engine.async_start_session()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._engine.cancel_session()
