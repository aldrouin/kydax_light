"""Dimming engine for Kydax Light.

Runs on a 60 s heartbeat. Lights are grouped into zones (plus an implicit
default zone for unassigned lights); each zone has its own dim session and
may override the central schedule and lux source — absent overrides inherit
the central configuration. Per zone, the engine:
- reads the zone's light source (own lux sensor, else the central source)
- computes the outdoor-light reduction percentage from the zone's lux
- starts the evening dim session (sunset offset, or early on sustained low lux)
- advances the session one step per interval, freezing paused lights
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_OFF,
    STATE_ON,
    SUN_EVENT_SUNSET,
)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

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
    DEFAULT_HIGH_LUX,
    DEFAULT_LOW_LUX,
    DEFAULT_MID_PCT,
    DEFAULT_OFFSET_MIN,
    DEFAULT_START_LUX,
    DEFAULT_STEP_MIN,
    DEFAULT_STEPS,
    DEFAULT_STRONG_PCT,
    DEFAULT_WINDOW_MIN,
    HEARTBEAT_SECONDS,
    KEY_EVENING,
    LUX_DEBOUNCE_MIN,
    PRESET_NONE,
    PRESETS,
    SOURCE_LUX,
    TEST_STEP_SECONDS,
    ZONE_DEFAULT,
    signal_update,
)

_LOGGER = logging.getLogger(__name__)

# Multiplier applied to the clear-sky estimate per weather condition when no
# cloud_coverage attribute is available.
WEATHER_FACTORS = {
    "sunny": 1.0,
    "clear-night": 1.0,
    "windy": 0.9,
    "windy-variant": 0.8,
    "partlycloudy": 0.55,
    "cloudy": 0.3,
    "fog": 0.15,
    "hail": 0.15,
    "rainy": 0.2,
    "pouring": 0.08,
    "lightning": 0.2,
    "lightning-rainy": 0.12,
    "snowy": 0.25,
    "snowy-rainy": 0.15,
    "exceptional": 0.5,
}

# Autostart is skipped entirely once we are this long past sunset (e.g. HA
# restarted late in the evening — do not suddenly dim at 23:00).
AUTOSTART_GRACE = timedelta(hours=1)

# When the auto-update switch is on, pending updates install in this window.
AUTO_UPDATE_HOUR = 4
AUTO_UPDATE_WINDOW_MIN = 10

# Releases whose notes contain this marker install at the next window even
# when auto-update is off.
CRITICAL_MARKER = "[critical]"

# Zone override keys that fall back to the central schedule when absent.
ZONE_OVERRIDE_KEYS = (
    CONF_OFFSET_MIN,
    CONF_STEP_MIN,
    CONF_STEPS,
    CONF_START_LUX,
    CONF_WINDOW_MIN,
)


@dataclass
class LightProgress:
    """Per-light progress within a dim session."""

    start_pct: float
    step: int = 0
    done: bool = False


@dataclass
class DimSession:
    """An active evening dim session."""

    started: datetime
    last_advance: datetime
    lights: dict[str, LightProgress] = field(default_factory=dict)
    # test sessions: step every N seconds instead of the configured minutes
    step_seconds: int | None = None
    # test sessions restore pre-test levels when cancelled
    is_test: bool = False


@dataclass
class ZoneState:
    """A dimming zone: a set of lights with optional config overrides."""

    zone_id: str
    name: str
    lights: list[str]
    config: dict = field(default_factory=dict)
    lux: float | None = None
    reduction: int = 0
    lux_below_since: datetime | None = None
    last_session_date: date | None = None
    session: DimSession | None = None


class KydaxEngine:
    """Holds all runtime state and drives the managed lights."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

        self.current_lux: float | None = None  # central source
        self.reduction_pct: int = 0  # from central source
        self.active_preset: str = PRESET_NONE

        self.zones: list[ZoneState] = self._build_zones()
        self._zone_by_light: dict[str, ZoneState] = {
            entity_id: zone for zone in self.zones for entity_id in zone.lights
        }

        self._paused_buttons: set[str] = set()
        self._unsubs: list[CALLBACK_TYPE] = []
        self._fast_unsub: CALLBACK_TYPE | None = None

        self.auto_update_enabled: bool = False
        self._last_auto_update_date: date | None = None

    # --- configuration accessors -------------------------------------------

    def _opt(self, key: str, default):
        return self.entry.options.get(key, default)

    @property
    def lights(self) -> dict[str, dict]:
        """Managed lights: {entity_id: {day, evening, night}}."""
        return self._opt(CONF_LIGHTS, {})

    @property
    def pause_buttons(self) -> list[dict]:
        return self._opt(CONF_PAUSE_BUTTONS, [])

    @property
    def source_mode(self) -> str:
        return self._opt(CONF_SOURCE_MODE, "weather")

    def _build_zones(self) -> list[ZoneState]:
        """Configured zones in order, then a default zone with the rest.

        A light claimed by several zones belongs to the first one listing it.
        """
        zones: list[ZoneState] = []
        assigned: set[str] = set()
        for conf in self._opt(CONF_ZONES, []):
            members = [
                entity_id
                for entity_id in conf.get("lights", [])
                if entity_id in self.lights and entity_id not in assigned
            ]
            assigned.update(members)
            zones.append(
                ZoneState(
                    zone_id=conf["id"],
                    name=conf["name"],
                    lights=members,
                    config=conf,
                )
            )
        remaining = [e for e in self.lights if e not in assigned]
        zones.append(ZoneState(zone_id=ZONE_DEFAULT, name="", lights=remaining))
        return zones

    def _zone_opt(self, zone: ZoneState, key: str, default):
        """Zone override if set, else the central value."""
        value = zone.config.get(key)
        return value if value is not None else self._opt(key, default)

    def get_zone(self, zone_id: str) -> ZoneState | None:
        return next((z for z in self.zones if z.zone_id == zone_id), None)

    # --- lifecycle ---------------------------------------------------------

    async def async_start(self) -> None:
        """Start the heartbeat and state listeners."""
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._async_heartbeat,
                timedelta(seconds=HEARTBEAT_SECONDS),
            )
        )
        if self.lights:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, list(self.lights), self._async_light_changed
                )
            )
        await self._async_tick(dt_util.now())

    @callback
    def async_stop(self) -> None:
        """Tear down listeners."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._stop_fast_timer()

    # --- heartbeat ---------------------------------------------------------

    async def _async_heartbeat(self, _now: datetime) -> None:
        await self._async_tick(dt_util.now())

    async def _async_tick(self, now: datetime) -> None:
        self.current_lux = self._read_central_lux()
        self.reduction_pct = self._reduction_for(self.current_lux)

        sunset = get_astral_event_date(self.hass, SUN_EVENT_SUNSET, now.date())
        if sunset is not None:
            sunset = dt_util.as_local(sunset)

        for zone in self.zones:
            zone.lux = self._zone_lux(zone)
            zone.reduction = self._reduction_for(zone.lux)
            self._update_lux_debounce(zone, now)
            if sunset is not None:
                await self._async_maybe_autostart(zone, sunset, now)
            await self._async_advance_session(zone, now)

        await self._async_maybe_auto_update(now)
        self._dispatch()

    # --- auto-update -------------------------------------------------------

    def _find_update_entity(self) -> str | None:
        """The HACS update entity for this integration, if present."""
        for state in self.hass.states.async_all("update"):
            if "kydax" in state.entity_id:
                return state.entity_id
        return None

    async def _async_maybe_auto_update(self, now: datetime) -> None:
        if now.hour != AUTO_UPDATE_HOUR or now.minute >= AUTO_UPDATE_WINDOW_MIN:
            return
        if self._last_auto_update_date == now.date():
            return
        entity_id = self._find_update_entity()
        if entity_id is None:
            return
        state = self.hass.states.get(entity_id)
        if state is None or state.state != STATE_ON:
            return
        summary = (state.attributes.get("release_summary") or "").lower()
        critical = CRITICAL_MARKER in summary
        if not self.auto_update_enabled and not critical:
            return
        self._last_auto_update_date = now.date()
        _LOGGER.info(
            "Auto-updating Kydax Light via %s (critical=%s)", entity_id, critical
        )
        try:
            await self.hass.services.async_call(
                "update", "install", {ATTR_ENTITY_ID: entity_id}, blocking=True
            )
        except Exception:  # noqa: BLE001 — never let an update break the engine
            _LOGGER.exception("Kydax Light auto-update failed")
            return
        await self.hass.services.async_call(
            "homeassistant", "restart", {}, blocking=False
        )

    # --- light source ------------------------------------------------------

    def _read_central_lux(self) -> float | None:
        if self.source_mode == SOURCE_LUX:
            return self._read_lux_sensor(self._opt(CONF_LUX_ENTITY, None))
        return self._estimate_lux_from_weather()

    def _zone_lux(self, zone: ZoneState) -> float | None:
        """The zone's own lux sensor if configured, else the central source."""
        if zone.config.get(CONF_LUX_ENTITY):
            return self._read_lux_sensor(zone.config[CONF_LUX_ENTITY])
        return self.current_lux

    def _read_lux_sensor(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    def _estimate_lux_from_weather(self) -> float | None:
        entity_id = self._opt(CONF_WEATHER_ENTITY, None)
        if not entity_id:
            return None
        weather = self.hass.states.get(entity_id)
        sun = self.hass.states.get("sun.sun")
        if weather is None or sun is None:
            return None

        elevation = sun.attributes.get("elevation")
        if elevation is None:
            return None

        # Clear-sky estimate from solar elevation, with a civil-twilight ramp.
        if elevation <= -6:
            base = 0.0
        elif elevation <= 0:
            base = 400.0 * (elevation + 6) / 6
        else:
            base = 120000.0 * math.sin(math.radians(min(elevation, 90.0)))

        coverage = weather.attributes.get("cloud_coverage")
        if coverage is not None:
            factor = 1.0 - 0.75 * (float(coverage) / 100.0)
        else:
            factor = WEATHER_FACTORS.get(weather.state, 0.5)

        return round(base * factor, 1)

    def _reduction_for(self, lux: float | None) -> int:
        high = self._opt(CONF_HIGH_LUX, DEFAULT_HIGH_LUX)
        low = self._opt(CONF_LOW_LUX, DEFAULT_LOW_LUX)
        if lux is None or lux >= high:
            return 0
        if lux <= low:
            return self._opt(CONF_STRONG_PCT, DEFAULT_STRONG_PCT)
        return self._opt(CONF_MID_PCT, DEFAULT_MID_PCT)

    def _update_lux_debounce(self, zone: ZoneState, now: datetime) -> None:
        start_lux = self._zone_opt(zone, CONF_START_LUX, DEFAULT_START_LUX)
        if zone.lux is not None and zone.lux < start_lux:
            if zone.lux_below_since is None:
                zone.lux_below_since = now
        else:
            zone.lux_below_since = None

    # --- pause -------------------------------------------------------------

    def is_button_paused(self, button_id: str) -> bool:
        return button_id in self._paused_buttons

    def is_light_paused(self, entity_id: str) -> bool:
        """A light is paused while ANY active pause button covers it."""
        zone = self._zone_by_light.get(entity_id)
        for button in self.pause_buttons:
            if button["id"] not in self._paused_buttons:
                continue
            if button.get("all") or entity_id in button.get("lights", []):
                return True
            if zone is not None and zone.zone_id in button.get("zones", []):
                return True
        return False

    @callback
    def set_button_paused(
        self, button_id: str, paused: bool, dispatch: bool = True
    ) -> None:
        if paused:
            self._paused_buttons.add(button_id)
        else:
            self._paused_buttons.discard(button_id)
        if dispatch:
            self._dispatch()

    # --- dim session -------------------------------------------------------

    async def _async_maybe_autostart(
        self, zone: ZoneState, sunset: datetime, now: datetime
    ) -> None:
        if zone.session is not None or zone.last_session_date == now.date():
            return

        if now > sunset + AUTOSTART_GRACE:
            zone.last_session_date = now.date()
            return

        offset = timedelta(
            minutes=self._zone_opt(zone, CONF_OFFSET_MIN, DEFAULT_OFFSET_MIN)
        )
        window = timedelta(
            minutes=self._zone_opt(zone, CONF_WINDOW_MIN, DEFAULT_WINDOW_MIN)
        )
        scheduled = sunset - offset

        if now >= scheduled:
            await self.async_start_session(zone.zone_id, now)
        elif (
            now >= sunset - window
            and zone.lux_below_since is not None
            and now - zone.lux_below_since >= timedelta(minutes=LUX_DEBOUNCE_MIN)
        ):
            _LOGGER.debug(
                "Zone %s: starting dim session early on low illuminance",
                zone.zone_id,
            )
            await self.async_start_session(zone.zone_id, now)

    async def async_start_session(
        self,
        zone_id: str,
        now: datetime | None = None,
        *,
        fast: bool = False,
        mark_day: bool = True,
    ) -> None:
        """Start a dim session over the zone's lights that are currently on.

        Test sessions pass mark_day=False so the evening autostart still runs
        today, and fast=True to step every TEST_STEP_SECONDS instead of the
        configured minutes.
        """
        zone = self.get_zone(zone_id)
        if zone is None:
            return
        now = now or dt_util.now()
        progress: dict[str, LightProgress] = {}
        for entity_id in zone.lights:
            state = self.hass.states.get(entity_id)
            if state is None or state.state != STATE_ON:
                continue
            brightness = state.attributes.get("brightness")
            if brightness is not None:
                start_pct = round(brightness / 255 * 100, 1)
            else:
                start_pct = float(self.lights[entity_id].get("day", 100))
            progress[entity_id] = LightProgress(start_pct=start_pct)

        if mark_day:
            zone.last_session_date = now.date()
        if not progress:
            _LOGGER.debug("Zone %s: no lights on, nothing to dim", zone.zone_id)
            return

        zone.session = DimSession(
            started=now,
            last_advance=now,
            lights=progress,
            step_seconds=TEST_STEP_SECONDS if fast else None,
            is_test=not mark_day,
        )
        self.active_preset = PRESET_NONE
        if fast:
            self._ensure_fast_timer()
        _LOGGER.info(
            "Zone %s: dim session started (%s) - %d light(s), %d step(s) every %s",
            zone.name or zone.zone_id,
            "test" if not mark_day else "evening",
            len(progress),
            self._zone_opt(zone, CONF_STEPS, DEFAULT_STEPS),
            f"{TEST_STEP_SECONDS} s"
            if fast
            else f"{self._zone_opt(zone, CONF_STEP_MIN, DEFAULT_STEP_MIN)} min",
        )
        self._dispatch()

    def session_info(self, zone_id: str) -> dict | None:
        """Progress of the zone's running dim session, for entity attributes."""
        zone = self.get_zone(zone_id)
        if zone is None or zone.session is None:
            return None
        session = zone.session
        steps = self._zone_opt(zone, CONF_STEPS, DEFAULT_STEPS)
        if session.step_seconds is not None:
            interval = timedelta(seconds=session.step_seconds)
        else:
            interval = timedelta(
                minutes=self._zone_opt(zone, CONF_STEP_MIN, DEFAULT_STEP_MIN)
            )
        remaining = max(
            (0 if p.done else steps - p.step) for p in session.lights.values()
        )
        return {
            "started": session.started.isoformat(),
            "is_test": session.is_test,
            "steps_total": steps,
            "steps_remaining": remaining,
            "next_step_at": (session.last_advance + interval).isoformat(),
            "lights_done": sum(p.done for p in session.lights.values()),
            "lights_total": len(session.lights),
        }

    @callback
    def cancel_session(self, zone_id: str) -> None:
        zone = self.get_zone(zone_id)
        if zone is not None:
            zone.session = None
        self._maybe_stop_fast_timer()
        self._dispatch()

    async def async_cancel_all_tests(self) -> None:
        """Stop every running test session and restore pre-test levels."""
        for zone in self.zones:
            session = zone.session
            if session is not None and session.is_test:
                zone.session = None
                for entity_id, progress in session.lights.items():
                    await self._async_apply_pct(entity_id, progress.start_pct)
        self._maybe_stop_fast_timer()
        self._dispatch()

    async def async_cancel_session(self, zone_id: str) -> None:
        """Cancel a session; a cancelled TEST session restores pre-test levels."""
        zone = self.get_zone(zone_id)
        if zone is None:
            return
        session = zone.session
        zone.session = None
        if session is not None and session.is_test:
            for entity_id, progress in session.lights.items():
                await self._async_apply_pct(entity_id, progress.start_pct)
        self._maybe_stop_fast_timer()
        self._dispatch()

    def _ensure_fast_timer(self) -> None:
        if self._fast_unsub is None:
            self._fast_unsub = async_track_time_interval(
                self.hass,
                self._async_fast_tick,
                timedelta(seconds=TEST_STEP_SECONDS),
            )

    @callback
    def _stop_fast_timer(self) -> None:
        if self._fast_unsub is not None:
            self._fast_unsub()
            self._fast_unsub = None

    @callback
    def _maybe_stop_fast_timer(self) -> None:
        if not any(
            z.session is not None and z.session.step_seconds is not None
            for z in self.zones
        ):
            self._stop_fast_timer()

    async def _async_fast_tick(self, _now: datetime) -> None:
        """Advance fast test sessions, one step per firing."""
        now = dt_util.now()
        for zone in self.zones:
            if zone.session is not None and zone.session.step_seconds is not None:
                await self._async_advance_session(zone, now, force=True)
        self._maybe_stop_fast_timer()
        self._dispatch()

    async def _async_advance_session(
        self, zone: ZoneState, now: datetime, force: bool = False
    ) -> None:
        session = zone.session
        if session is None:
            return

        if not force:
            if session.step_seconds is not None:
                # fast test sessions advance on the fast timer, not the heartbeat
                return
            step_min = self._zone_opt(zone, CONF_STEP_MIN, DEFAULT_STEP_MIN)
            if now - session.last_advance < timedelta(minutes=step_min):
                return
        session.last_advance = now

        steps = self._zone_opt(zone, CONF_STEPS, DEFAULT_STEPS)
        for entity_id, progress in session.lights.items():
            if progress.done or self.is_light_paused(entity_id):
                continue
            progress.step += 1
            target = self.lights[entity_id].get(KEY_EVENING, 0) * (
                1 - zone.reduction / 100
            )
            if progress.step >= steps:
                progress.done = True
                pct = target
            else:
                pct = progress.start_pct + (target - progress.start_pct) * (
                    progress.step / steps
                )
            await self._async_apply_pct(entity_id, pct)
            _LOGGER.info(
                "Zone %s: %s dimmed to %.0f%% (step %d/%d, %d call(s) left)",
                zone.name or zone.zone_id,
                entity_id,
                pct,
                progress.step,
                steps,
                max(0, steps - progress.step),
            )

        if all(p.done for p in session.lights.values()):
            zone.session = None
            _LOGGER.info(
                "Zone %s: dim session finished after %d step(s)",
                zone.name or zone.zone_id,
                steps,
            )

    @callback
    def _async_light_changed(self, event: Event[EventStateChangedData]) -> None:
        """A managed light turned off during a session -> stop dimming it."""
        entity_id = event.data["entity_id"]
        zone = self._zone_by_light.get(entity_id)
        if zone is None or zone.session is None:
            return
        new_state = event.data["new_state"]
        progress = zone.session.lights.get(entity_id)
        if (
            progress is not None
            and not progress.done
            and (new_state is None or new_state.state == STATE_OFF)
        ):
            progress.done = True
            if all(p.done for p in zone.session.lights.values()):
                zone.session = None
            self._dispatch()

    # --- presets -----------------------------------------------------------

    async def async_apply_preset(self, preset: str) -> None:
        """Apply a preset immediately; cancels all in-flight sessions."""
        if preset not in PRESETS:
            return
        self.active_preset = preset
        for zone in self.zones:
            zone.session = None
        if preset != PRESET_NONE:
            for entity_id, conf in self.lights.items():
                if self.is_light_paused(entity_id):
                    continue
                zone = self._zone_by_light.get(entity_id)
                reduction = zone.reduction if zone else self.reduction_pct
                pct = conf.get(preset, 0) * (1 - reduction / 100)
                await self._async_apply_pct(entity_id, pct)
        self._dispatch()

    @callback
    def restore_preset(self, preset: str) -> None:
        """Restore the displayed preset after a restart without applying it."""
        if preset in PRESETS:
            self.active_preset = preset

    # --- helpers -----------------------------------------------------------

    async def _async_apply_pct(self, entity_id: str, pct: float) -> None:
        if pct < 0.5:
            await self.hass.services.async_call(
                "light", "turn_off", {ATTR_ENTITY_ID: entity_id}, blocking=False
            )
        else:
            await self.hass.services.async_call(
                "light",
                "turn_on",
                {ATTR_ENTITY_ID: entity_id, "brightness_pct": round(pct)},
                blocking=False,
            )

    @callback
    def _dispatch(self) -> None:
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))
