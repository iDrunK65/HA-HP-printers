"""Data update coordinators for the HP CDM printer integration.

Three coordinators run at three different cadences so the printer is never hit
by a burst of requests: the hardware is measurably fragile (a full SNMP walk is
enough to make it time out) and every request is additionally serialised by the
client itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HpCdmClient, HpCdmError
from .const import (
    DATA_ALERTS,
    DATA_POWER,
    DATA_STATISTICS,
    DATA_STATUS,
    DOMAIN,
    ENDPOINT_ALERTS,
    ENDPOINT_POWER_CONFIG,
    ENDPOINT_STATISTICS,
    ENDPOINT_STATUS,
    ENDPOINT_SUPPLIES,
    ENDPOINT_USAGE,
    UPDATE_INTERVAL_STATUS,
    UPDATE_INTERVAL_SUPPLY,
    UPDATE_INTERVAL_USAGE,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class HpCdmDeviceInfo:
    """Identity of the printer, resolved once at setup."""

    name: str
    model: str | None
    serial_number: str | None
    firmware: str | None


@dataclass
class HpCdmData:
    """Runtime data stored on the config entry."""

    client: HpCdmClient
    device: HpCdmDeviceInfo
    status: HpCdmStatusCoordinator
    supply: HpCdmSupplyCoordinator
    usage: HpCdmUsageCoordinator


type HpCdmConfigEntry = ConfigEntry[HpCdmData]


class HpCdmCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Base coordinator wiring the client, the entry and the logger together."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: HpCdmConfigEntry,
        client: HpCdmClient,
        name: str,
        update_interval: timedelta,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {name}",
            update_interval=update_interval,
            # Mandatory since Home Assistant 2026.8: omitting it raises.
            config_entry=entry,
        )
        self.client = client


class HpCdmStatusCoordinator(HpCdmCoordinator):
    """Poll printer state, alerts and diagnostics every minute."""

    def __init__(
        self, hass: HomeAssistant, entry: HpCdmConfigEntry, client: HpCdmClient
    ) -> None:
        """Initialise the status coordinator."""
        super().__init__(hass, entry, client, "status", UPDATE_INTERVAL_STATUS)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch state, alerts, statistics and power configuration.

        Requests are awaited one after another rather than gathered: the
        printer answers unreliably when several requests overlap.
        """
        try:
            status = await self.client.async_get(ENDPOINT_STATUS)
            alerts = await self.client.async_get(ENDPOINT_ALERTS, required=False)
            statistics = await self.client.async_get(
                ENDPOINT_STATISTICS, required=False
            )
            power = await self.client.async_get(ENDPOINT_POWER_CONFIG, required=False)
        except HpCdmError as err:
            raise UpdateFailed(str(err)) from err

        return {
            DATA_STATUS: status,
            DATA_ALERTS: alerts,
            DATA_STATISTICS: statistics,
            DATA_POWER: power,
        }


class HpCdmSupplyCoordinator(HpCdmCoordinator):
    """Poll cartridge levels every ten minutes."""

    def __init__(
        self, hass: HomeAssistant, entry: HpCdmConfigEntry, client: HpCdmClient
    ) -> None:
        """Initialise the supply coordinator."""
        super().__init__(hass, entry, client, "supply", UPDATE_INTERVAL_SUPPLY)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the public supplies list."""
        try:
            payload = await self.client.async_get(ENDPOINT_SUPPLIES)
        except HpCdmError as err:
            raise UpdateFailed(str(err)) from err
        return payload or {}


class HpCdmUsageCoordinator(HpCdmCoordinator):
    """Poll lifetime counters every fifteen minutes.

    A single request feeds roughly twenty sensors, and the counters move
    slowly, so there is nothing to gain from polling it faster.
    """

    def __init__(
        self, hass: HomeAssistant, entry: HpCdmConfigEntry, client: HpCdmClient
    ) -> None:
        """Initialise the usage coordinator."""
        super().__init__(hass, entry, client, "usage", UPDATE_INTERVAL_USAGE)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the lifetime counters."""
        try:
            payload = await self.client.async_get(ENDPOINT_USAGE)
        except HpCdmError as err:
            raise UpdateFailed(str(err)) from err
        return payload or {}
