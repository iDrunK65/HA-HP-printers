"""Diagnostics support for the HP CDM printer integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import CONF_SERIAL, CONF_UUID
from .coordinator import HpCdmConfigEntry

# Cartridge serial numbers, the printer serial and anything that locates the
# machine on a network are stripped: these dumps end up attached to public
# issues.
TO_REDACT = {
    CONF_HOST,
    CONF_SERIAL,
    CONF_UUID,
    "adminurl",
    "deviceUuid",
    "hostName",
    "ipAddress",
    "macAddress",
    "serialNumber",
    "uuid",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HpCdmConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = entry.runtime_data

    return async_redact_data(
        {
            "entry": {
                "title": entry.title,
                "data": dict(entry.data),
                "unique_id": entry.unique_id,
            },
            "device": {
                "name": data.device.name,
                "model": data.device.model,
                "serialNumber": data.device.serial_number,
                "firmware": data.device.firmware,
            },
            "coordinators": {
                "status": {
                    "last_update_success": data.status.last_update_success,
                    "data": data.status.data,
                },
                "supply": {
                    "last_update_success": data.supply.last_update_success,
                    "data": data.supply.data,
                },
                "usage": {
                    "last_update_success": data.usage.last_update_success,
                    "data": data.usage.data,
                },
            },
        },
        TO_REDACT,
    )
