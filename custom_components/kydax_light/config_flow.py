"""Config and options flows for Kydax Light."""

from __future__ import annotations

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
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
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
    return vol.All(
        NumberSelector(
            NumberSelectorConfig(
                min=minimum,
                max=maximum,
                step=1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement=unit,
            )
        ),
        vol.Coerce(int),
    )


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
            lights = {
                entity_id: {
                    KEY_DAY: DEFAULT_DAY,
                    KEY_EVENING: DEFAULT_EVENING,
                    KEY_NIGHT: DEFAULT_NIGHT,
                }
                for entity_id in user_input.get(CONF_LIGHTS, [])
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

        return self.async_show_form(
            step_id="lights",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_LIGHTS, default=[]): EntitySelector(
                        EntitySelectorConfig(domain="light", multiple=True)
                    )
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> KydaxOptionsFlow:
        return KydaxOptionsFlow()


class KydaxOptionsFlow(OptionsFlow):
    """Ongoing management: lights, pause buttons, source, schedule."""

    def __init__(self) -> None:
        self._edit_light: str | None = None
        self._edit_button_id: str | None = None

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
            menu_options=["lights", "pause_buttons", "source", "schedule"],
        )

    # --- lights ------------------------------------------------------------

    async def async_step_lights(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        menu = ["add_light"]
        if self._options.get(CONF_LIGHTS):
            menu += ["edit_light", "remove_light"]
        return self.async_show_menu(step_id="lights", menu_options=menu)

    async def async_step_add_light(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            options = self._options
            lights = dict(options.get(CONF_LIGHTS, {}))
            for entity_id in user_input.get(CONF_LIGHTS, []):
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

        return self.async_show_form(
            step_id="add_light",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LIGHTS, default=[]): EntitySelector(
                        EntitySelectorConfig(domain="light", multiple=True)
                    )
                }
            ),
        )

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
            # Drop removed lights from pause-button scopes too.
            options[CONF_PAUSE_BUTTONS] = [
                {
                    **button,
                    "lights": [
                        e for e in button.get("lights", []) if e not in removed
                    ],
                }
                for button in options.get(CONF_PAUSE_BUTTONS, [])
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
        return vol.Schema(
            {
                vol.Required("name"): TextSelector(),
                vol.Required("all", default=False): BooleanSelector(),
                vol.Optional("lights", default=[]): EntitySelector(
                    EntitySelectorConfig(include_entities=managed, multiple=True)
                ),
            }
        )

    async def async_step_add_button(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input["all"] and not user_input.get("lights"):
                errors["lights"] = "button_lights_required"
            else:
                options = self._options
                buttons = list(options.get(CONF_PAUSE_BUTTONS, []))
                buttons.append(
                    {
                        "id": uuid4().hex[:8],
                        "name": user_input["name"],
                        "all": user_input["all"],
                        "lights": [] if user_input["all"] else user_input["lights"],
                    }
                )
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
            if not user_input["all"] and not user_input.get("lights"):
                errors["lights"] = "button_lights_required"
            else:
                updated = {
                    "id": current["id"],
                    "name": user_input["name"],
                    "all": user_input["all"],
                    "lights": [] if user_input["all"] else user_input["lights"],
                }
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
