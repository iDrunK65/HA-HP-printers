# HP Printer (CDM) for Home Assistant

[![Validate](https://github.com/iDrunK65/HA-HP-printers/actions/workflows/validate.yml/badge.svg)](https://github.com/iDrunK65/HA-HP-printers/actions/workflows/validate.yml)
[![hacs](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Home Assistant integration for **recent HP printers**, built on their local
`/cdm/` REST API.

## Why this exists

Recent HP firmwares (nginx web server, forced HTTPS, HSTS) **removed the
`/DevMgmt/*.xml` API**. As a result:

- the community `hpprinter` integration fails with an HTTP 400,
- the built-in `ipp` integration only reports state and ink levels.

Those firmwares expose a full JSON REST API under `/cdm/` instead, a large part
of which is **readable with no authentication at all**. That is what this
integration uses: roughly forty entities from four anonymous endpoints, no
password, no cloud.

Developed against an **HP Color LaserJet Pro MFP 3302**, firmware
`6.28.3.30-202606111700`. The code stays generic and degrades gracefully on
other models: endpoints that are missing simply make their entities unavailable
instead of breaking the integration.

## Design guarantees

- **Read-only.** The HTTP client implements `GET` and nothing else. This is not
  a stylistic choice: the same unauthenticated CDM API also exposes
  `systemReset`, `networkReset`, `reboot`, `secureErase` and friends. There is
  no code path here that can reach them.
- **Anonymous.** The config flow asks for an address, nothing more. An OAuth2
  token is obtainable from the printer, but the main thing it unlocks —
  `jobManagement/v1/history` — exposes **printed file names and Windows user
  names**. On a shared printer that is a privacy problem, so it is out of scope
  by design.
- **Gentle on the hardware.** This printer copes badly with bursts of requests.
  Every call is serialised and spaced out, the three coordinators poll at
  staggered intervals (60 s / 10 min / 15 min), and the first refresh is
  sequential rather than concurrent.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=iDrunK65&repository=HA-HP-printers&category=integration)

Or manually: HACS → three dots → *Custom repositories* → add
`https://github.com/iDrunK65/HA-HP-printers` with the category *Integration*,
then install **HP Printer (CDM)** and restart Home Assistant.

### Manual

Copy `custom_components/hp_cdm/` into your `config/custom_components/`
directory and restart Home Assistant.

## Configuration

The printer is usually **discovered automatically** over mDNS (`_ipp._tcp`);
just confirm the discovery. Non-HP devices announcing the same service type are
filtered out.

To add it by hand: *Settings → Devices & services → Add integration → HP
Printer (CDM)*, then enter the IP address or hostname. HTTPS and the
self-signed certificate are handled for you.

If the printer answers but does not expose `/cdm/servicesDiscovery`, setup stops
with an explicit *not supported* message — that firmware is too old for this
integration, and the built-in `ipp` integration is the right choice instead.

## Entities

| Group | Endpoint | Interval |
| --- | --- | --- |
| State, alerts, diagnostics | `system/v1/status`, `alert/v1/alerts`, `system/v1/statistics`, `power/v1/configuration` | 60 s |
| Cartridges | `supply/v1/suppliesPublic` | 10 min |
| Lifetime counters | `deviceUsage/v1/lifetimeCounters` | 15 min |

**State** — printer status (raw firmware value, e.g. `ready`, `processing`,
`inPowerSave`), alert status (`ok` / `warning` / `error`, with the full alert
list in attributes), active alert count.

**Cartridges** — one sensor per cartridge showing remaining life in percent,
with the order part number, capacity, serial number, manufacture date, warranty
status, genuine-HP and refilled flags, and the estimated remaining pages in
attributes.

**Counters** — impressions (total, mono, colour, print, copy), sheets (total,
simplex, duplex), scanned images (total, send, copy, ADF), and job counts
(print, copy, email). Plus two derived percentages: colour ratio and duplex
ratio.

**Binary sensors** — `problem` (on as soon as an alert of severity *error* or
worse is present, with the reason in attributes) and `connectivity`.

### Dashboard card

The device page groups entities the way Home Assistant sees fit, which the
integration does not control. To get a grouped card with separators, add an
`entities` card with `section` rows:

```yaml
type: entities
title: Printer
show_header_toggle: false
entities:
  - entity: sensor.PRINTER_printer_status
  - entity: binary_sensor.PRINTER_problem
  - type: section
    label: Cartridges
  - entity: sensor.PRINTER_black_cartridge
  - entity: sensor.PRINTER_cyan_cartridge
  - entity: sensor.PRINTER_magenta_cartridge
  - entity: sensor.PRINTER_yellow_cartridge
  - type: section
    label: Counters
  - entity: sensor.PRINTER_total_impressions
  - entity: sensor.PRINTER_sheets
  - entity: sensor.PRINTER_scanned_images
  - entity: sensor.PRINTER_color_ratio
  - entity: sensor.PRINTER_duplex_ratio
```

Replace `PRINTER` with the slug of your device name (visible in
*Developer tools → States*). Use `- type: divider` instead of `- type: section`
for an unlabelled separator.

### Disabled by default

Everything fax-related, blank sides, network folder jobs, and the diagnostic
sensors (power cycles, available memory, sleep timeout) are created but
disabled, since they stay at zero on most machines. Enable them from the entity
settings if your printer actually uses them.

### Notes and known limits

- `approximatePagesRemainingDisplay` can be a **floor**, not an exact figure.
  When the firmware reports the `greaterThan` symbol, the attribute
  `pages_remaining_is_lower_bound` is `true` — treat the value as "more than N".
- `inPowerSave` is **not** an error. The printer keeps answering CDM while
  asleep.
- Cartridges are enumerated at setup. Fitting a cartridge into a slot that was
  empty at the time requires reloading the integration for the new entity to
  appear.
- The printer's own serial number and firmware version come from
  `/cdm/system/v1/identity`, which has never been observed in the wild. When it
  is absent, the device simply shows fewer details; nothing else changes. If
  your printer *does* answer on that endpoint, its payload would be a welcome
  issue report.

## Troubleshooting

Enable debug logging:

```yaml
logger:
  default: warning
  logs:
    custom_components.hp_cdm: debug
```

Then download the device diagnostics (device page → three dots → *Download
diagnostics*) before opening an issue. Serial numbers, hostnames, UUIDs and MAC
addresses are redacted automatically.

You can check what your printer exposes yourself, safely, with:

```bash
curl -k https://YOUR_PRINTER_IP/cdm/servicesDiscovery
```

## Contributing

Issues and pull requests are welcome, especially payloads from other HP models.
Two hard rules for any contribution: **GET only**, and **no credentials**.

Brand assets (icon and logo in
[home-assistant/brands](https://github.com/home-assistant/brands)) are not
required for a custom HACS repository; they would only be needed to submit this
integration to the default HACS store, which is a possible later step.

## License

[MIT](LICENSE)
