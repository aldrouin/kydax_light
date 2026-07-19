# Kydax Light

Custom Home Assistant integration for adaptive evening dimming, fully managed through the UI. Replaces the old `kydax_dimmer` implementation — no YAML, no hard-coded entity IDs.

## What it does

- **Managed lights** — pick any lights, each with its own Day / Evening / Night percentage.
- **Evening dimming** — starts at *sunset − offset* (default 2 h before), or **early** when the configured light source stays below the start threshold for 10 minutes (only within the allowed window before sunset, once per day). Dims one step every N minutes toward `evening % × (1 − reduction %)`.
- **Pause buttons** — create as many as you need, scoped to *all lights* or a group. Each is a switch: **on = pause active**. A light is paused while *any* button covering it is on, so "unpause all" never clears a smaller group's pause. Paused lights freeze mid-dim and resume where they were. Pause states survive restarts.
- **Presets** — a select with Day / Evening / Night / None. Choosing one applies each light's percentage immediately (minus reduction) and cancels in-flight dimming. Paused lights are left untouched.
- **Outdoor-light reduction** — from a lux meter **or** a weather-derived estimate (your explicit choice, no silent fallback): above `high_lux` no reduction, below `low_lux` strong reduction, in between medium reduction.
- **Zones** — group lights into zones (e.g. sections of a restaurant) that dim on their own schedule and/or their own lux sensor. A zone only stores what you override; empty fields inherit the central configuration live. Each zone gets its own dimming switch; lights not in any zone follow the central schedule. A zone's lux sensor drives both its early start and its reduction.

## Entities

| Entity | Behavior |
|---|---|
| `switch.kydax_light_<button name>` | one per pause button; on = pause active |
| `switch.kydax_light_evening_dimming` | on while dimming runs; turn on = start now, off = cancel tonight |
| `select.kydax_light_preset` | Day / Evening / Night / None |
| `sensor.kydax_light_outdoor_illuminance` | current lux from the configured source |
| `sensor.kydax_light_reduction` | reduction % currently applied |

## Install (private deploy — no HACS)

On the HA box (SSH/Terminal add-on):

```bash
cd /config
git clone git@github.com:aldrouin/kydax_light.git /config/kydax_light-src
ln -s /config/kydax_light-src/custom_components/kydax_light /config/custom_components/kydax_light
```

Restart HA, then **Settings → Devices & Services → Add integration → Kydax Light**.

Update later with:

```bash
cd /config/kydax_light-src && git pull
```

and restart HA.

## Configure

Everything lives in the config entry — initial setup asks for the light source, schedule, and first lights. Afterwards, **⚙ Configure** on the integration opens a menu to manage lights (add / edit percentages / remove), pause buttons (create / edit / delete), the light source, and the schedule. Changes reload the integration live; no restart needed.

## Notes

- If HA restarts mid-evening, the dim session does not resume (it will not auto-start more than 1 h past sunset either — no surprise dimming at 23:00).
- In weather mode, illuminance is estimated from solar elevation × cloud cover (or condition when cloud cover is unavailable) — good enough for thresholds, not a real lux value.
- Lights that are off when dimming starts are left off; a light turned off by hand mid-session stops being dimmed for the night.
