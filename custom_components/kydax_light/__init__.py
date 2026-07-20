"""The Kydax Light integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import CONF_PAUSE_BUTTONS, CONF_ZONES
from .coordinator import KydaxEngine

# test controls became a Tests option-menu action in 0.5.0; these mark the
# button entities older versions registered so they can be pruned
_TEST_MARKER = "_test_"
_STOP_TESTS_SUFFIX = "_stop_tests"

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
    """Remove registry entries for pause buttons and zones deleted from options."""
    registry = er.async_get(hass)
    valid_ids = {
        f"{entry.entry_id}_pause_{button['id']}"
        for button in entry.options.get(CONF_PAUSE_BUTTONS, [])
    }
    zone_ids = [zone["id"] for zone in entry.options.get(CONF_ZONES, [])]
    valid_ids.update(f"{entry.entry_id}_gradation_{zid}" for zid in zone_ids)
    # test buttons (_test_, _test_fast_, _stop_tests) are no longer created;
    # they moved to the options Tests menu in 0.5.0 and are pruned here
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            "_pause_" in reg_entry.unique_id
            or "_gradation_" in reg_entry.unique_id
            or _TEST_MARKER in reg_entry.unique_id
            or reg_entry.unique_id.endswith(_STOP_TESTS_SUFFIX)
        ) and reg_entry.unique_id not in valid_ids:
            registry.async_remove(reg_entry.entity_id)
