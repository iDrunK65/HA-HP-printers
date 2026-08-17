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
from .const import (
    CONF_FIRMWARE,
    CONF_MODEL,
    CONF_SERIAL,
    DEFAULT_DEVICE_NAME,
    ENDPOINT_IDENTITY,
    ENDPOINT_SERVICES_DISCOVERY,
)
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
    collect_cdm_paths,
    find_value,
    identity_candidates,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: HpCdmConfigEntry) -> bool:
    """Set up an HP CDM printer from a config entry."""
    # The printer serves HTTPS with a self-signed certificate and redirects
    # plain HTTP, so certificate verification has to be off.
    session = async_get_clientsession(hass, verify_ssl=False)
    client = HpCdmClient(session, entry.data[CONF_HOST])

    device = await _async_resolve_device(hass, client, entry)

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
    hass: HomeAssistant, client: HpCdmClient, entry: HpCdmConfigEntry
) -> HpCdmDeviceInfo:
    """Build the device identity, degrading gracefully at every step.

    Setup must never depend on any of this: every request here is optional and
    every failure just means a less detailed device page.
    """
    model = entry.data.get(CONF_MODEL)
    serial = entry.data.get(CONF_SERIAL)
    firmware = entry.data.get(CONF_FIRMWARE)

    # Probing costs requests on hardware that dislikes them, so only do it
    # while something is still missing; once resolved the values are cached on
    # the config entry.
    if not model or not serial:
        found = await _async_probe_identity(client)
        model = model or found.get("model")
        serial = serial or found.get("serial")
        firmware = firmware or found.get("firmware")

        updates = {CONF_MODEL: model, CONF_SERIAL: serial, CONF_FIRMWARE: firmware}
        if any(value and entry.data.get(key) != value for key, value in updates.items()):
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, **{k: v for k, v in updates.items() if v}}
            )

    return HpCdmDeviceInfo(
        name=model or entry.title or DEFAULT_DEVICE_NAME,
        model=model,
        serial_number=serial,
        firmware=firmware,
    )


async def _async_probe_identity(client: HpCdmClient) -> dict[str, str]:
    """Look for the model, serial and firmware across the CDM tree.

    ``/cdm/system/v1/identity`` is tried first, then any *advertised* path that
    looks device-scoped. Candidate URLs are never invented: they are read back
    from servicesDiscovery, which is the firmware's own index of itself. That
    matters because guessed URLs on this API are how you end up addressing
    something you did not mean to.
    """
    found: dict[str, str] = {}
    candidates = [ENDPOINT_IDENTITY]

    try:
        services = await client.async_get(ENDPOINT_SERVICES_DISCOVERY, required=False)
    except HpCdmError as err:
        _LOGGER.debug("Could not read %s: %s", ENDPOINT_SERVICES_DISCOVERY, err)
    else:
        candidates += [
            path
            for path in identity_candidates(collect_cdm_paths(services))
            if path not in candidates
        ]

    for path in candidates:
        try:
            payload = await client.async_get(path, required=False)
        except HpCdmError as err:
            # A single flaky optional request must not abort the whole setup.
            _LOGGER.debug("Could not read %s: %s", path, err)
            continue
        if not payload:
            continue

        for field, keys in (
            ("model", IDENTITY_MODEL_KEYS),
            ("serial", IDENTITY_SERIAL_KEYS),
            ("firmware", IDENTITY_FIRMWARE_KEYS),
        ):
            if field not in found and (value := find_value(payload, keys)):
                found[field] = str(value).strip()

        if "model" in found and "serial" in found:
            break

    _LOGGER.debug("Identity probe over %s resolved %s", candidates, found)
    return found
