"""Sensors for Kydax Light: current illuminance and reduction."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import LIGHT_LUX, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KydaxConfigEntry
from .const import custom_label
from .coordinator import KydaxEngine
from .entity import KydaxEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KydaxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    engine = entry.runtime_data
    async_add_entities(
        [KydaxIlluminanceSensor(engine), KydaxReductionSensor(engine)]
    )


class KydaxIlluminanceSensor(KydaxEntity, SensorEntity):
    """Current outdoor illuminance from the configured source."""

    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = LIGHT_LUX

    def __init__(self, engine: KydaxEngine) -> None:
        super().__init__(engine)
        self._attr_unique_id = f"{engine.entry.entry_id}_illuminance"
        custom = custom_label(engine.entry.options, "illuminance")
        if custom:
            self._attr_name = custom
        else:
            self._attr_translation_key = "illuminance"

    @property
    def available(self) -> bool:
        return self._engine.current_lux is not None

    @property
    def native_value(self) -> float | None:
        return self._engine.current_lux

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"source": self._engine.source_mode}


class KydaxReductionSensor(KydaxEntity, SensorEntity):
    """Current reduction applied on top of the configured percentages."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:brightness-4"

    def __init__(self, engine: KydaxEngine) -> None:
        super().__init__(engine)
        self._attr_unique_id = f"{engine.entry.entry_id}_reduction"
        custom = custom_label(engine.entry.options, "reduction")
        if custom:
            self._attr_name = custom
        else:
            self._attr_translation_key = "reduction"

    @property
    def native_value(self) -> int:
        return self._engine.reduction_pct
