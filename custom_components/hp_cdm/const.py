"""Constants for the HP CDM printer integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "hp_cdm"
MANUFACTURER: Final = "HP"

# Shown when /cdm/system/v1/identity is unavailable or exposes no model name.
# The endpoint answers anonymously on the reference hardware, but other models
# may not offer it at all, so the integration must never depend on it.
DEFAULT_DEVICE_NAME: Final = "HP Printer"

# Config entry keys. CONF_HOST comes from homeassistant.const.
CONF_UUID: Final = "uuid"
CONF_MODEL: Final = "model"
# Identity fields are cached on the config entry once resolved, so a printer
# that is asleep or slow at restart still shows a complete device page.
CONF_MODEL_ID: Final = "model_id"
CONF_SERIAL: Final = "serial_number"
CONF_FIRMWARE: Final = "firmware"

# --- Endpoints -------------------------------------------------------------
# Every one of these is readable anonymously on the reference firmware
# (Color LaserJet Pro MFP 3302, 6.28.3.30). This integration is GET-only: the
# CDM API also exposes unauthenticated *destructive* writes (systemReset,
# networkReset, reboot, secureErase...) which must never be reachable from
# here, not even by accident.
ENDPOINT_SERVICES_DISCOVERY: Final = "/cdm/servicesDiscovery"
ENDPOINT_STATUS: Final = "/cdm/system/v1/status"
ENDPOINT_ALERTS: Final = "/cdm/alert/v1/alerts"
ENDPOINT_STATISTICS: Final = "/cdm/system/v1/statistics"
ENDPOINT_POWER_CONFIG: Final = "/cdm/power/v1/configuration"
ENDPOINT_SUPPLIES: Final = "/cdm/supply/v1/suppliesPublic"
ENDPOINT_USAGE: Final = "/cdm/deviceUsage/v1/lifetimeCounters"
ENDPOINT_IDENTITY: Final = "/cdm/system/v1/identity"

# --- Polling ---------------------------------------------------------------
# Deliberately staggered: the printer copes badly with bursts of requests (a
# full snmpwalk was enough to make it time out), which is the most likely
# cause of the "Unavailable" entities seen with other integrations.
UPDATE_INTERVAL_STATUS: Final = timedelta(seconds=60)
UPDATE_INTERVAL_SUPPLY: Final = timedelta(minutes=10)
UPDATE_INTERVAL_USAGE: Final = timedelta(minutes=15)

REQUEST_TIMEOUT: Final = 15
# Minimum idle time enforced between two HTTP requests, on top of the fact
# that requests are already serialised by a lock.
MIN_REQUEST_INTERVAL: Final = 0.5

# --- Units -----------------------------------------------------------------
UNIT_PAGES: Final = "pages"
UNIT_SHEETS: Final = "sheets"
UNIT_IMAGES: Final = "images"
UNIT_JOBS: Final = "jobs"
UNIT_ALERTS: Final = "alerts"

# --- Coordinator payload keys ---------------------------------------------
DATA_STATUS: Final = "status"
DATA_ALERTS: Final = "alerts"
DATA_STATISTICS: Final = "statistics"
DATA_POWER: Final = "power"

# --- Alerts ----------------------------------------------------------------
ALERT_STATUS_OK: Final = "ok"
ALERT_STATUS_WARNING: Final = "warning"
ALERT_STATUS_ERROR: Final = "error"

# Toner colour codes seen on the reference hardware. Anything else falls back
# to a generic, slot-numbered entity name.
SUPPLY_COLOR_CODES: Final = ("K", "C", "M", "Y")
