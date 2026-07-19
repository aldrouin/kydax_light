"""Constants for the Kydax Light integration."""

DOMAIN = "kydax_light"

# --- light source ---
CONF_SOURCE_MODE = "source_mode"  # "lux" | "weather"
CONF_LUX_ENTITY = "lux_entity"
CONF_WEATHER_ENTITY = "weather_entity"

SOURCE_LUX = "lux"
SOURCE_WEATHER = "weather"

# --- schedule ---
CONF_OFFSET_MIN = "sunset_offset_minutes"  # start N minutes before sunset
CONF_STEP_MIN = "step_minutes"  # minutes between dim steps
CONF_STEPS = "steps"  # number of dim steps
CONF_START_LUX = "start_lux"  # start early when illuminance drops below this
CONF_WINDOW_MIN = "early_window_minutes"  # lux may only advance the start within this window before sunset

DEFAULT_OFFSET_MIN = 120
DEFAULT_STEP_MIN = 5
DEFAULT_STEPS = 12
DEFAULT_START_LUX = 800
DEFAULT_WINDOW_MIN = 240

# --- reduction from outdoor light ---
CONF_HIGH_LUX = "high_lux"  # above: no reduction
CONF_LOW_LUX = "low_lux"  # below: strong reduction
CONF_MID_PCT = "mid_reduction_pct"
CONF_STRONG_PCT = "strong_reduction_pct"

DEFAULT_HIGH_LUX = 5000
DEFAULT_LOW_LUX = 800
DEFAULT_MID_PCT = 10
DEFAULT_STRONG_PCT = 20

# --- zones ---
# [{"id": str, "name": str, "lights": [entity_id],
#   optional overrides: lux_entity, sunset_offset_minutes, step_minutes,
#   steps, start_lux, early_window_minutes — absent keys inherit the
#   central configuration}]
CONF_ZONES = "zones"

# id of the implicit zone holding managed lights not assigned to any zone
ZONE_DEFAULT = "default"

# --- managed lights & pause buttons ---
CONF_LIGHTS = "lights"  # {entity_id: {"day": int, "evening": int, "night": int}}
CONF_PAUSE_BUTTONS = "pause_buttons"  # [{"id": str, "name": str, "all": bool, "lights": [entity_id]}]

KEY_DAY = "day"
KEY_EVENING = "evening"
KEY_NIGHT = "night"

DEFAULT_DAY = 90
DEFAULT_EVENING = 30
DEFAULT_NIGHT = 0

PRESET_NONE = "none"
PRESET_DAY = "day"
PRESET_EVENING = "evening"
PRESET_NIGHT = "night"
PRESETS = [PRESET_DAY, PRESET_EVENING, PRESET_NIGHT, PRESET_NONE]

# minutes the illuminance must stay below start_lux before an early start
LUX_DEBOUNCE_MIN = 10

# engine heartbeat interval (seconds)
HEARTBEAT_SECONDS = 60

# how often to refresh the weather forecast (minutes) in weather mode
FORECAST_REFRESH_MIN = 15


def signal_update(entry_id: str) -> str:
    """Dispatcher signal for entity state refreshes."""
    return f"{DOMAIN}_{entry_id}_update"
