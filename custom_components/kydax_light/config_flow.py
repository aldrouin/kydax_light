"""Config and options flows for Kydax Light."""

from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.components.http import StaticPathConfig
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    FileSelector,
    FileSelectorConfig,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_HIGH_LUX,
    CONF_LIGHTS,
    CONF_LOW_LUX,
    CONF_LUX_ENTITY,
    CONF_MID_PCT,
    CONF_OFFSET_MIN,
    CONF_PAUSE_BUTTONS,
    CONF_SOURCE_MODE,
    CONF_START_LUX,
    CONF_STEP_MIN,
    CONF_STEPS,
    CONF_STRONG_PCT,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_MIN,
    CONF_ZONES,
    DEFAULT_DAY,
    DEFAULT_EVENING,
    DEFAULT_HIGH_LUX,
    DEFAULT_LOW_LUX,
    DEFAULT_MID_PCT,
    DEFAULT_NIGHT,
    DEFAULT_OFFSET_MIN,
    DEFAULT_START_LUX,
    DEFAULT_STEP_MIN,
    DEFAULT_STEPS,
    DEFAULT_STRONG_PCT,
    DEFAULT_WINDOW_MIN,
    DOMAIN,
    KEY_DAY,
    KEY_EVENING,
    KEY_NIGHT,
    SOURCE_LUX,
    SOURCE_WEATHER,
)


def _int_number(minimum: float, maximum: float, unit: str | None = None):
    config = NumberSelectorConfig(
        min=minimum, max=maximum, step=1, mode=NumberSelectorMode.BOX
    )
    if unit is not None:
        config["unit_of_measurement"] = unit
    return vol.All(NumberSelector(config), vol.Coerce(int))


SOURCE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SOURCE_MODE, default=SOURCE_WEATHER): SelectSelector(
            SelectSelectorConfig(
                options=[SOURCE_LUX, SOURCE_WEATHER],
                translation_key="source_mode",
                mode=SelectSelectorMode.LIST,
            )
        ),
        vol.Optional(CONF_LUX_ENTITY): EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="illuminance")
        ),
        vol.Optional(CONF_WEATHER_ENTITY): EntitySelector(
            EntitySelectorConfig(domain="weather")
        ),
    }
)

SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_OFFSET_MIN, default=DEFAULT_OFFSET_MIN): _int_number(
            0, 360, "min"
        ),
        vol.Required(CONF_STEP_MIN, default=DEFAULT_STEP_MIN): _int_number(
            1, 60, "min"
        ),
        vol.Required(CONF_STEPS, default=DEFAULT_STEPS): _int_number(1, 60),
        vol.Required(CONF_START_LUX, default=DEFAULT_START_LUX): _int_number(
            0, 20000, "lx"
        ),
        vol.Required(CONF_WINDOW_MIN, default=DEFAULT_WINDOW_MIN): _int_number(
            0, 720, "min"
        ),
        vol.Required(CONF_HIGH_LUX, default=DEFAULT_HIGH_LUX): _int_number(
            0, 100000, "lx"
        ),
        vol.Required(CONF_LOW_LUX, default=DEFAULT_LOW_LUX): _int_number(
            0, 100000, "lx"
        ),
        vol.Required(CONF_MID_PCT, default=DEFAULT_MID_PCT): _int_number(0, 100, "%"),
        vol.Required(CONF_STRONG_PCT, default=DEFAULT_STRONG_PCT): _int_number(
            0, 100, "%"
        ),
    }
)

PCT_SCHEMA = vol.Schema(
    {
        vol.Required(KEY_DAY, default=DEFAULT_DAY): _int_number(0, 100, "%"),
        vol.Required(KEY_EVENING, default=DEFAULT_EVENING): _int_number(0, 100, "%"),
        vol.Required(KEY_NIGHT, default=DEFAULT_NIGHT): _int_number(0, 100, "%"),
    }
)


def _all_light_entity_ids(hass, include_groups: bool = False) -> list[str]:
    """Every light entity; light groups only when explicitly requested.

    Groups are excluded by default because managing a group and its member
    lights at the same time double-commands the same bulbs.
    """
    return [
        state.entity_id
        for state in hass.states.async_all("light")
        if include_groups
        or not isinstance(state.attributes.get("entity_id"), (list, tuple))
    ]


LIGHT_PICK_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_LIGHTS, default=[]): EntitySelector(
            EntitySelectorConfig(domain="light", multiple=True)
        ),
        vol.Required("add_all", default=False): BooleanSelector(),
        vol.Required("add_groups", default=False): BooleanSelector(),
    }
)


# import/export: what describes a site, without the entities and sensors
# that only exist on one installation
PORTABLE_KEYS = (
    CONF_LIGHTS,
    CONF_ZONES,
    CONF_PAUSE_BUTTONS,
    CONF_OFFSET_MIN,
    CONF_STEP_MIN,
    CONF_STEPS,
    CONF_START_LUX,
    CONF_WINDOW_MIN,
    CONF_HIGH_LUX,
    CONF_LOW_LUX,
    CONF_MID_PCT,
    CONF_STRONG_PCT,
)
DEFAULT_CONFIG_FILE = "kydax_light.json"
DEFAULT_LIGHTS_FILE = "kydax_light_all_lights.json"
# exports live under www/ (also reachable at /local/... after a restart) and
# are served immediately from our own route
DOWNLOAD_DIR = "www"
_STATIC_URL = "/kydax_light_files"
_STATIC_REGISTERED = "kydax_light_static_registered"


def _strip_comments(value: Any) -> Any:
    """Drop the readable annotations added on export (keys starting with _)."""
    if isinstance(value, dict):
        return {
            key: _strip_comments(item)
            for key, item in value.items()
            if not key.startswith("_")
        }
    if isinstance(value, list):
        return [_strip_comments(item) for item in value]
    return value


def _export_payload(
    hass, options: dict[str, Any], entry_id: str | None = None
) -> dict[str, Any]:
    """The portable configuration, annotated with the names behind the ids.

    Every managed light is named, and zones and pause buttons also carry the
    friendly name of the entity they created. Everything prefixed with _ is
    a comment and is ignored on import.
    """

    def _label(entity_id: str) -> str:
        state = hass.states.get(entity_id) if hass else None
        name = state.name if state else None
        return f"{name} ({entity_id})" if name else entity_id

    entities: dict[str, str] = {}
    if hass is not None and entry_id:
        for reg_entry in er.async_entries_for_config_entry(
            er.async_get(hass), entry_id
        ):
            state = hass.states.get(reg_entry.entity_id)
            friendly = (
                state.name
                if state
                else (reg_entry.name or reg_entry.original_name or reg_entry.entity_id)
            )
            suffix = reg_entry.unique_id.removeprefix(f"{entry_id}_")
            entities[suffix] = f"{friendly} ({reg_entry.entity_id})"

    data: dict[str, Any] = {}
    for key in PORTABLE_KEYS:
        if key not in options:
            continue
        value = options[key]
        if key == CONF_LIGHTS and isinstance(value, dict):
            value = {
                **value,
                "_names": {
                    entity_id: _label(entity_id) for entity_id in value
                },
            }
        elif key in (CONF_ZONES, CONF_PAUSE_BUTTONS) and isinstance(value, list):
            prefix = "gradation_" if key == CONF_ZONES else "pause_"
            annotated = []
            for item in value:
                entry = {
                    **item,
                    "_lights": [_label(e) for e in item.get("lights", [])],
                }
                entity = entities.get(f"{prefix}{item.get('id')}")
                if entity:
                    entry["_entity"] = entity
                annotated.append(entry)
            value = annotated
        data[key] = value
    return {
        "_comment": (
            "Kydax Light configuration. Lines starting with _ are comments "
            "and are ignored on import. Light source sensors are not "
            "included, as they differ per installation."
        ),
        "kydax_light": data,
    }


def _all_lights_payload(hass, include_groups: bool) -> dict[str, Any]:
    """Every light in this installation, ready to edit and import back."""
    lights: dict[str, Any] = {}
    listing: list[dict[str, Any]] = []
    for entity_id in _all_light_entity_ids(hass, include_groups):
        state = hass.states.get(entity_id)
        members = state.attributes.get("entity_id") if state else None
        listing.append(
            {
                "entity_id": entity_id,
                "name": state.name if state else entity_id,
                "is_group": isinstance(members, (list, tuple)),
            }
        )
        lights[entity_id] = {
            KEY_DAY: DEFAULT_DAY,
            KEY_EVENING: DEFAULT_EVENING,
            KEY_NIGHT: DEFAULT_NIGHT,
        }
    return {"available_lights": listing, "kydax_light": {CONF_LIGHTS: lights}}


def _validate_payload(payload: Any) -> str | None:
    """Return an error key when the imported content is unusable."""
    if not isinstance(payload, dict):
        return "invalid_file"
    lights = payload.get(CONF_LIGHTS)
    if lights is not None:
        if not isinstance(lights, dict):
            return "invalid_file"
        for entity_id, values in lights.items():
            if not entity_id.startswith("light.") or not isinstance(values, dict):
                return "invalid_lights"
            for key in (KEY_DAY, KEY_EVENING, KEY_NIGHT):
                value = values.get(key)
                if not isinstance(value, int) or not 0 <= value <= 100:
                    return "invalid_lights"
    for key in (CONF_ZONES, CONF_PAUSE_BUTTONS):
        value = payload.get(key)
        if value is not None and (
            not isinstance(value, list)
            or not all(isinstance(item, dict) and item.get("id") for item in value)
        ):
            return "invalid_file"
    return None


def _validate_source(user_input: dict[str, Any]) -> dict[str, str]:
    """The entity matching the chosen source mode is required."""
    errors: dict[str, str] = {}
    if user_input[CONF_SOURCE_MODE] == SOURCE_LUX and not user_input.get(
        CONF_LUX_ENTITY
    ):
        errors[CONF_LUX_ENTITY] = "lux_entity_required"
    if user_input[CONF_SOURCE_MODE] == SOURCE_WEATHER and not user_input.get(
        CONF_WEATHER_ENTITY
    ):
        errors[CONF_WEATHER_ENTITY] = "weather_entity_required"
    return errors


class KydaxConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup: source -> schedule -> first lights."""

    VERSION = 1

    def __init__(self) -> None:
        self._source: dict[str, Any] = {}
        self._schedule: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_source(user_input)
            if not errors:
                self._source = user_input
                return await self.async_step_schedule()

        return self.async_show_form(
            step_id="user", data_schema=SOURCE_SCHEMA, errors=errors
        )

    async def async_step_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._schedule = user_input
            return await self.async_step_lights()

        return self.async_show_form(step_id="schedule", data_schema=SCHEDULE_SCHEMA)

    async def async_step_lights(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            selected = list(user_input.get(CONF_LIGHTS, []))
            if user_input.get("add_all"):
                selected += _all_light_entity_ids(
                    self.hass, include_groups=user_input.get("add_groups", False)
                )
            lights = {
                entity_id: {
                    KEY_DAY: DEFAULT_DAY,
                    KEY_EVENING: DEFAULT_EVENING,
                    KEY_NIGHT: DEFAULT_NIGHT,
                }
                for entity_id in selected
            }
            return self.async_create_entry(
                title="Kydax Light",
                data={},
                options={
                    **self._source,
                    **self._schedule,
                    CONF_LIGHTS: lights,
                    CONF_PAUSE_BUTTONS: [],
                },
            )

        return self.async_show_form(step_id="lights", data_schema=LIGHT_PICK_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> KydaxOptionsFlow:
        return KydaxOptionsFlow()


class KydaxOptionsFlow(OptionsFlow):
    """Ongoing management: lights, pause buttons, source, schedule."""

    def __init__(self) -> None:
        self._edit_light: str | None = None
        self._edit_button_id: str | None = None
        self._edit_zone_id: str | None = None

    @property
    def _options(self) -> dict[str, Any]:
        return dict(self.config_entry.options)

    def _save(self, new_options: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(title="", data=new_options)

    def _light_select_options(self) -> list[SelectOptionDict]:
        options = []
        for entity_id in self._options.get(CONF_LIGHTS, {}):
            state = self.hass.states.get(entity_id)
            label = state.name if state else entity_id
            options.append(SelectOptionDict(value=entity_id, label=label))
        return options

    def _button_select_options(self) -> list[SelectOptionDict]:
        return [
            SelectOptionDict(value=button["id"], label=button["name"])
            for button in self._options.get(CONF_PAUSE_BUTTONS, [])
        ]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "lights",
                "zones",
                "pause_buttons",
                "source",
                "schedule",
                "backup",
                "tests",
            ],
        )

    # --- import / export ------------------------------------------------------

    async def async_step_backup(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="backup",
            menu_options=["export", "export_lights", "import"],
        )

    async def _async_write_file(self, name: str, payload: dict) -> str:
        """Write the export and return a URL the browser can fetch now.

        Home Assistant only serves /config/www at /local when that folder
        already existed at startup, so a first export would 404 until a
        restart. Registering our own static route avoids that entirely.
        """
        name = name.strip() or DEFAULT_CONFIG_FILE
        directory = self.hass.config.path(DOWNLOAD_DIR)
        path = self.hass.config.path(DOWNLOAD_DIR, name)

        def _write() -> None:
            os.makedirs(directory, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)

        await self.hass.async_add_executor_job(_write)
        if not self.hass.data.get(_STATIC_REGISTERED):
            await self.hass.http.async_register_static_paths(
                [StaticPathConfig(_STATIC_URL, directory, False)]
            )
            self.hass.data[_STATIC_REGISTERED] = True
        return f"{_STATIC_URL}/{name}"

    async def async_step_export(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Write lights, zones, pause buttons and the schedule to a file."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                url = await self._async_write_file(
                    user_input["path"],
                    _export_payload(
                        self.hass, self._options, self.config_entry.entry_id
                    ),
                )
            except OSError:
                errors["path"] = "write_failed"
            else:
                return self.async_abort(
                    reason="exported", description_placeholders={"url": url}
                )

        return self.async_show_form(
            step_id="export",
            data_schema=vol.Schema(
                {vol.Required("path", default=DEFAULT_CONFIG_FILE): TextSelector()}
            ),
            errors=errors,
        )

    async def async_step_export_lights(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """List every light of this installation into an importable file."""
        errors: dict[str, str] = {}
        if user_input is not None:
            payload = _all_lights_payload(
                self.hass, user_input.get("add_groups", False)
            )
            try:
                url = await self._async_write_file(user_input["path"], payload)
            except OSError:
                errors["path"] = "write_failed"
            else:
                return self.async_abort(
                    reason="lights_exported",
                    description_placeholders={
                        "url": url,
                        "count": str(len(payload["available_lights"])),
                    },
                )

        return self.async_show_form(
            step_id="export_lights",
            data_schema=vol.Schema(
                {
                    vol.Required("path", default=DEFAULT_LIGHTS_FILE): TextSelector(),
                    vol.Required("add_groups", default=False): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_import(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace lights, zones, pause buttons and schedule from a file."""
        errors: dict[str, str] = {}
        if user_input is not None:
            def _read() -> Any:
                with process_uploaded_file(self.hass, user_input["file"]) as path:
                    with open(path, encoding="utf-8") as handle:
                        return json.load(handle)

            try:
                data = await self.hass.async_add_executor_job(_read)
            except (OSError, ValueError, KeyError):
                errors["file"] = "invalid_file"
            else:
                payload = _strip_comments(
                    data.get("kydax_light", data) if isinstance(data, dict) else data
                )
                problem = _validate_payload(payload)
                if problem:
                    errors["file"] = problem
                else:
                    options = self._options
                    for key in PORTABLE_KEYS:
                        if payload.get(key) is not None:
                            options[key] = payload[key]
                    return self._save(options)

        return self.async_show_form(
            step_id="import",
            data_schema=vol.Schema(
                {
                    vol.Required("file"): FileSelector(
                        FileSelectorConfig(accept=".json,application/json")
                    )
                }
            ),
            errors=errors,
        )

    # --- tests --------------------------------------------------------------

    async def async_step_tests(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Run a dim-session test on a zone, or stop running tests.

        Runs the action immediately and returns to the menu; nothing is
        saved. Test sessions do not consume the daily autostart.
        """
        engine = self.config_entry.runtime_data
        if user_input is not None:
            action = user_input["action"]
            if action == "stop":
                await engine.async_cancel_all_tests()
            else:
                await engine.async_start_session(
                    user_input["zone"], fast=action == "fast", mark_day=False
                )
            return await self.async_step_init()

        zone_options = [
            SelectOptionDict(value=zone.zone_id, label=zone.name)
            for zone in engine.zones
        ]
        schema = vol.Schema(
            {
                vol.Required("zone", default=zone_options[0]["value"]): SelectSelector(
                    SelectSelectorConfig(
                        options=zone_options, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Required("action", default="test"): SelectSelector(
                    SelectSelectorConfig(
                        options=["test", "fast", "stop"],
                        translation_key="test_action",
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="tests", data_schema=schema)

    # --- lights ------------------------------------------------------------

    async def async_step_lights(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        menu = ["add_light"]
        if self._options.get(CONF_LIGHTS):
            menu += ["bulk_edit_light", "edit_light", "remove_light"]
        return self.async_show_menu(step_id="lights", menu_options=menu)

    async def async_step_bulk_edit_light(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Apply the same day/evening/night percentages to many lights at once."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options = self._options
            lights = dict(options.get(CONF_LIGHTS, {}))
            targets = (
                list(lights)
                if user_input.get("all")
                else user_input.get("lights", [])
            )
            if not targets:
                errors["lights"] = "lights_required"
            else:
                values = {
                    KEY_DAY: user_input[KEY_DAY],
                    KEY_EVENING: user_input[KEY_EVENING],
                    KEY_NIGHT: user_input[KEY_NIGHT],
                }
                for entity_id in targets:
                    if entity_id in lights:
                        lights[entity_id] = dict(values)
                options[CONF_LIGHTS] = lights
                return self._save(options)

        schema = vol.Schema(
            {
                vol.Required("all", default=False): BooleanSelector(),
                vol.Optional("lights", default=[]): SelectSelector(
                    SelectSelectorConfig(
                        options=self._light_select_options(),
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                **PCT_SCHEMA.schema,
            }
        )
        return self.async_show_form(
            step_id="bulk_edit_light", data_schema=schema, errors=errors
        )

    async def async_step_add_light(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            options = self._options
            lights = dict(options.get(CONF_LIGHTS, {}))
            selected = list(user_input.get(CONF_LIGHTS, []))
            if user_input.get("add_all"):
                selected += _all_light_entity_ids(
                    self.hass, include_groups=user_input.get("add_groups", False)
                )
            for entity_id in selected:
                lights.setdefault(
                    entity_id,
                    {
                        KEY_DAY: DEFAULT_DAY,
                        KEY_EVENING: DEFAULT_EVENING,
                        KEY_NIGHT: DEFAULT_NIGHT,
                    },
                )
            options[CONF_LIGHTS] = lights
            return self._save(options)

        return self.async_show_form(step_id="add_light", data_schema=LIGHT_PICK_SCHEMA)

    async def async_step_edit_light(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._edit_light = user_input["light"]
            return await self.async_step_edit_light_pct()

        return self.async_show_form(
            step_id="edit_light",
            data_schema=vol.Schema(
                {
                    vol.Required("light"): SelectSelector(
                        SelectSelectorConfig(
                            options=self._light_select_options(),
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_edit_light_pct(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = self._options
        lights = dict(options.get(CONF_LIGHTS, {}))

        if user_input is not None:
            lights[self._edit_light] = {
                KEY_DAY: user_input[KEY_DAY],
                KEY_EVENING: user_input[KEY_EVENING],
                KEY_NIGHT: user_input[KEY_NIGHT],
            }
            options[CONF_LIGHTS] = lights
            return self._save(options)

        current = lights.get(self._edit_light, {})
        return self.async_show_form(
            step_id="edit_light_pct",
            data_schema=self.add_suggested_values_to_schema(PCT_SCHEMA, current),
            description_placeholders={"light": self._edit_light},
        )

    async def async_step_remove_light(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            options = self._options
            lights = dict(options.get(CONF_LIGHTS, {}))
            removed = user_input.get("lights", [])
            for entity_id in removed:
                lights.pop(entity_id, None)
            options[CONF_LIGHTS] = lights
            # Drop removed lights from pause-button and zone scopes too.
            options[CONF_PAUSE_BUTTONS] = [
                {
                    **button,
                    "lights": [
                        e for e in button.get("lights", []) if e not in removed
                    ],
                }
                for button in options.get(CONF_PAUSE_BUTTONS, [])
            ]
            options[CONF_ZONES] = [
                {
                    **zone,
                    "lights": [
                        e for e in zone.get("lights", []) if e not in removed
                    ],
                }
                for zone in options.get(CONF_ZONES, [])
            ]
            return self._save(options)

        return self.async_show_form(
            step_id="remove_light",
            data_schema=vol.Schema(
                {
                    vol.Required("lights", default=[]): SelectSelector(
                        SelectSelectorConfig(
                            options=self._light_select_options(),
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    # --- zones --------------------------------------------------------------

    def _zone_select_options(self) -> list[SelectOptionDict]:
        return [
            SelectOptionDict(value=zone["id"], label=zone["name"])
            for zone in self._options.get(CONF_ZONES, [])
        ]

    def _zone_schema(self) -> vol.Schema:
        managed = list(self._options.get(CONF_LIGHTS, {}))
        return vol.Schema(
            {
                vol.Required("name"): TextSelector(),
                vol.Required("lights", default=[]): EntitySelector(
                    EntitySelectorConfig(include_entities=managed, multiple=True)
                ),
                vol.Optional(CONF_LUX_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor", device_class="illuminance")
                ),
                vol.Optional(CONF_OFFSET_MIN): _int_number(0, 360, "min"),
                vol.Optional(CONF_STEP_MIN): _int_number(1, 60, "min"),
                vol.Optional(CONF_STEPS): _int_number(1, 60),
                vol.Optional(CONF_START_LUX): _int_number(0, 20000, "lx"),
                vol.Optional(CONF_WINDOW_MIN): _int_number(0, 720, "min"),
            }
        )

    @staticmethod
    def _zone_from_input(zone_id: str, user_input: dict[str, Any]) -> dict[str, Any]:
        """Only submitted override keys are stored; absent keys inherit."""
        zone = {
            "id": zone_id,
            "name": user_input["name"],
            "lights": user_input.get("lights", []),
        }
        for key in (
            CONF_LUX_ENTITY,
            CONF_OFFSET_MIN,
            CONF_STEP_MIN,
            CONF_STEPS,
            CONF_START_LUX,
            CONF_WINDOW_MIN,
        ):
            if user_input.get(key) is not None:
                zone[key] = user_input[key]
        return zone

    async def async_step_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        menu = ["add_zone"]
        if self._options.get(CONF_ZONES):
            menu += ["edit_zone", "remove_zone"]
        return self.async_show_menu(step_id="zones", menu_options=menu)

    async def async_step_add_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get("lights"):
                errors["lights"] = "zone_lights_required"
            else:
                options = self._options
                zones = list(options.get(CONF_ZONES, []))
                zones.append(self._zone_from_input(uuid4().hex[:8], user_input))
                options[CONF_ZONES] = zones
                return self._save(options)

        return self.async_show_form(
            step_id="add_zone", data_schema=self._zone_schema(), errors=errors
        )

    async def async_step_edit_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._edit_zone_id = user_input["zone"]
            return await self.async_step_edit_zone_form()

        return self.async_show_form(
            step_id="edit_zone",
            data_schema=vol.Schema(
                {
                    vol.Required("zone"): SelectSelector(
                        SelectSelectorConfig(
                            options=self._zone_select_options(),
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_edit_zone_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = self._options
        zones = list(options.get(CONF_ZONES, []))
        current = next(
            (z for z in zones if z["id"] == self._edit_zone_id), None
        )
        if current is None:
            return await self.async_step_zones()

        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get("lights"):
                errors["lights"] = "zone_lights_required"
            else:
                updated = self._zone_from_input(current["id"], user_input)
                options[CONF_ZONES] = [
                    updated if z["id"] == current["id"] else z for z in zones
                ]
                return self._save(options)

        return self.async_show_form(
            step_id="edit_zone_form",
            data_schema=self.add_suggested_values_to_schema(
                self._zone_schema(), current
            ),
            errors=errors,
        )

    async def async_step_remove_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            options = self._options
            removed = set(user_input.get("zones", []))
            options[CONF_ZONES] = [
                z for z in options.get(CONF_ZONES, []) if z["id"] not in removed
            ]
            # Drop deleted zones from pause-button scopes too.
            options[CONF_PAUSE_BUTTONS] = [
                {
                    **button,
                    "zones": [
                        z for z in button.get("zones", []) if z not in removed
                    ],
                }
                for button in options.get(CONF_PAUSE_BUTTONS, [])
            ]
            return self._save(options)

        return self.async_show_form(
            step_id="remove_zone",
            data_schema=vol.Schema(
                {
                    vol.Required("zones", default=[]): SelectSelector(
                        SelectSelectorConfig(
                            options=self._zone_select_options(),
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    # --- pause buttons ------------------------------------------------------

    async def async_step_pause_buttons(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        menu = ["add_button"]
        if self._options.get(CONF_PAUSE_BUTTONS):
            menu += ["edit_button", "remove_button"]
        return self.async_show_menu(step_id="pause_buttons", menu_options=menu)

    def _button_schema(self) -> vol.Schema:
        managed = list(self._options.get(CONF_LIGHTS, {}))
        schema: dict[Any, Any] = {
            vol.Required("name"): TextSelector(),
            vol.Required("all", default=False): BooleanSelector(),
            vol.Optional("lights", default=[]): EntitySelector(
                EntitySelectorConfig(include_entities=managed, multiple=True)
            ),
        }
        if self._options.get(CONF_ZONES):
            schema[vol.Optional("zones", default=[])] = SelectSelector(
                SelectSelectorConfig(
                    options=self._zone_select_options(),
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )
        return vol.Schema(schema)

    @staticmethod
    def _button_scope_valid(user_input: dict[str, Any]) -> bool:
        return bool(
            user_input["all"]
            or user_input.get("lights")
            or user_input.get("zones")
        )

    @staticmethod
    def _button_from_input(
        button_id: str, user_input: dict[str, Any]
    ) -> dict[str, Any]:
        covers_all = user_input["all"]
        return {
            "id": button_id,
            "name": user_input["name"],
            "all": covers_all,
            "lights": [] if covers_all else user_input.get("lights", []),
            "zones": [] if covers_all else user_input.get("zones", []),
        }

    async def async_step_add_button(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not self._button_scope_valid(user_input):
                errors["lights"] = "button_lights_required"
            else:
                options = self._options
                buttons = list(options.get(CONF_PAUSE_BUTTONS, []))
                buttons.append(self._button_from_input(uuid4().hex[:8], user_input))
                options[CONF_PAUSE_BUTTONS] = buttons
                return self._save(options)

        return self.async_show_form(
            step_id="add_button", data_schema=self._button_schema(), errors=errors
        )

    async def async_step_edit_button(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._edit_button_id = user_input["button"]
            return await self.async_step_edit_button_form()

        return self.async_show_form(
            step_id="edit_button",
            data_schema=vol.Schema(
                {
                    vol.Required("button"): SelectSelector(
                        SelectSelectorConfig(
                            options=self._button_select_options(),
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_edit_button_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = self._options
        buttons = list(options.get(CONF_PAUSE_BUTTONS, []))
        current = next(
            (b for b in buttons if b["id"] == self._edit_button_id), None
        )
        if current is None:
            return await self.async_step_pause_buttons()

        errors: dict[str, str] = {}
        if user_input is not None:
            if not self._button_scope_valid(user_input):
                errors["lights"] = "button_lights_required"
            else:
                updated = self._button_from_input(current["id"], user_input)
                options[CONF_PAUSE_BUTTONS] = [
                    updated if b["id"] == current["id"] else b for b in buttons
                ]
                return self._save(options)

        return self.async_show_form(
            step_id="edit_button_form",
            data_schema=self.add_suggested_values_to_schema(
                self._button_schema(), current
            ),
            errors=errors,
        )

    async def async_step_remove_button(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            options = self._options
            removed = set(user_input.get("buttons", []))
            options[CONF_PAUSE_BUTTONS] = [
                b
                for b in options.get(CONF_PAUSE_BUTTONS, [])
                if b["id"] not in removed
            ]
            return self._save(options)

        return self.async_show_form(
            step_id="remove_button",
            data_schema=vol.Schema(
                {
                    vol.Required("buttons", default=[]): SelectSelector(
                        SelectSelectorConfig(
                            options=self._button_select_options(),
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    # --- source & schedule --------------------------------------------------

    async def async_step_source(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_source(user_input)
            if not errors:
                options = self._options
                options[CONF_SOURCE_MODE] = user_input[CONF_SOURCE_MODE]
                options[CONF_LUX_ENTITY] = user_input.get(CONF_LUX_ENTITY)
                options[CONF_WEATHER_ENTITY] = user_input.get(CONF_WEATHER_ENTITY)
                return self._save(options)

        return self.async_show_form(
            step_id="source",
            data_schema=self.add_suggested_values_to_schema(
                SOURCE_SCHEMA, self._options
            ),
            errors=errors,
        )

    async def async_step_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._save({**self._options, **user_input})

        return self.async_show_form(
            step_id="schedule",
            data_schema=self.add_suggested_values_to_schema(
                SCHEDULE_SCHEMA, self._options
            ),
        )
