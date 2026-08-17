"""The HP CDM printer integration.

Talks to the local, anonymous ``/cdm/*`` REST API exposed by recent HP
firmwares, which replaced the old ``/DevMgmt/*.xml`` interface. No credentials
are ever requested or stored, and only HTTP GET is ever issued.
"""

from __future__ import annotations

import logging

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HpCdmClient, HpCdmError
from .const import CONF_MODEL, DEFAULT_DEVICE_NAME, ENDPOINT_IDENTITY
from .coordinator import (
    HpCdmConfigEntry,
    HpCdmData,
    HpCdmDeviceInfo,
    HpCdmStatusCoordinator,
    HpCdmSupplyCoordinator,
    HpCdmUsageCoordinator,
)
from .util import (
    IDENTITY_FIRMWARE_KEYS,
    IDENTITY_MODEL_KEYS,
    IDENTITY_SERIAL_KEYS,
    find_value,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: HpCdmConfigEntry) -> bool:
    """Set up an HP CDM printer from a config entry."""
    # The printer serves HTTPS with a self-signed certificate and redirects
    # plain HTTP, so certificate verification has to be off.
    session = async_get_clientsession(hass, verify_ssl=False)
    client = HpCdmClient(session, entry.data[CONF_HOST])

    device = await _async_resolve_device(client, entry)

    status_coordinator = HpCdmStatusCoordinator(hass, entry, client)
    supply_coordinator = HpCdmSupplyCoordinator(hass, entry, client)
    usage_coordinator = HpCdmUsageCoordinator(hass, entry, client)

    # Sequential on purpose, never asyncio.gather: three concurrent bursts at
    # startup is exactly the pattern that makes this hardware time out.
    await status_coordinator.async_config_entry_first_refresh()
    await supply_coordinator.async_config_entry_first_refresh()
    await usage_coordinator.async_config_entry_first_refresh()

    entry.runtime_data = HpCdmData(
        client=client,
        device=device,
        status=status_coordinator,
        supply=supply_coordinator,
        usage=usage_coordinator,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HpCdmConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_resolve_device(
    client: HpCdmClient, entry: HpCdmConfigEntry
) -> HpCdmDeviceInfo:
    """Build the device identity, degrading gracefully at every step.

    Setup must never depend on the identity endpoint: it is optional, its
    schema is unverified, and the model name discovered over mDNS is a
    perfectly good fallback.
    """
    identity: dict | None = None
    try:
        identity = await client.async_get(ENDPOINT_IDENTITY, required=False)
    except HpCdmError as err:
        # A single flaky optional request must not abort the whole setup.
        _LOGGER.debug("Could not read %s: %s", ENDPOINT_IDENTITY, err)

    model = find_value(identity, IDENTITY_MODEL_KEYS) if identity else None
    serial = find_value(identity, IDENTITY_SERIAL_KEYS) if identity else None
    firmware = find_value(identity, IDENTITY_FIRMWARE_KEYS) if identity else None

    # The zeroconf "ty" TXT record already carries a human-readable model.
    model = str(model) if model else entry.data.get(CONF_MODEL)

    return HpCdmDeviceInfo(
        name=model or entry.title or DEFAULT_DEVICE_NAME,
        model=model,
        serial_number=str(serial) if serial else None,
        firmware=str(firmware) if firmware else None,
    )
