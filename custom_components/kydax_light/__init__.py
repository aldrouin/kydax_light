"""The Kydax Light integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import CONF_PAUSE_BUTTONS
from .coordinator import KydaxEngine

PLATFORMS = [Platform.SELECT, Platform.SENSOR, Platform.SWITCH]

type KydaxConfigEntry = ConfigEntry[KydaxEngine]


async def async_setup_entry(hass: HomeAssistant, entry: KydaxConfigEntry) -> bool:
    """Set up Kydax Light from a config entry."""
    engine = KydaxEngine(hass, entry)
    entry.runtime_data = engine

    _async_prune_stale_pause_entities(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await engine.async_start()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: KydaxConfigEntry) -> bool:
    """Unload a config entry."""
    entry.runtime_data.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: KydaxConfigEntry) -> None:
    """Reload the entry when options change so entities match the config."""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_prune_stale_pause_entities(
    hass: HomeAssistant, entry: KydaxConfigEntry
) -> None:
    """Remove registry entries for pause buttons deleted from the options."""
    registry = er.async_get(hass)
    valid_ids = {
        f"{entry.entry_id}_pause_{button['id']}"
        for button in entry.options.get(CONF_PAUSE_BUTTONS, [])
    }
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if "_pause_" in reg_entry.unique_id and reg_entry.unique_id not in valid_ids:
            registry.async_remove(reg_entry.entity_id)
