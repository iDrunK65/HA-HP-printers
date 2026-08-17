"""Read-only HTTP client for the HP CDM local API.

This client implements **GET only**, on purpose. The CDM API on recent HP
firmwares accepts unauthenticated writes on endpoints such as
``/cdm/reset/v1/systemReset``, ``/cdm/ioConfig/v2/networkReset``,
``/cdm/power/v1/reboot`` or ``/cdm/storageDevices/v1/secureErase``. Not
implementing any write verb is the cheapest way to guarantee this integration
can never brick a printer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from yarl import URL

from .const import MIN_REQUEST_INTERVAL, REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)

# Status codes that mean "this device does not offer that endpoint" rather
# than "the device is broken". Old firmwares answer 400 on unknown paths,
# newer ones 404; authenticated-only resources answer 401/403.
_OPTIONAL_STATUSES = (400, 401, 403, 404, 405, 410, 501)


class HpCdmError(Exception):
    """Base error for the HP CDM client."""


class HpCdmConnectionError(HpCdmError):
    """Raised when the printer cannot be reached or answers unexpectedly."""


class HpCdmNotSupportedError(HpCdmError):
    """Raised when the device answers but does not expose the CDM API."""


class HpCdmClient:
    """Minimal, deliberately slow, GET-only CDM client."""

    def __init__(self, session: aiohttp.ClientSession, host: str) -> None:
        """Initialise the client for a printer reachable at ``host``."""
        self._session = session
        self._host = host
        # HTTP is answered with a 301 to HTTPS, so go straight to HTTPS. The
        # certificate is self-signed, hence the caller must pass a session
        # created with verify_ssl=False.
        self._base = URL(f"https://{host}")
        # The printer drops requests when they arrive too close together, so
        # every call is serialised and spaced out.
        self._lock = asyncio.Lock()
        self._last_request: float = 0.0

    @property
    def host(self) -> str:
        """Return the configured host."""
        return self._host

    @property
    def base_url(self) -> str:
        """Return the base URL used for every request."""
        return str(self._base)

    async def async_get(
        self, path: str, *, required: bool = True
    ) -> dict[str, Any] | None:
        """Fetch a CDM resource and return its decoded JSON payload.

        Returns ``None`` when the endpoint is absent or forbidden and
        ``required`` is False, so that an optional endpoint missing on another
        model never breaks a whole refresh cycle.
        """
        async with self._lock:
            await self._async_throttle()
            url = self._base.with_path(path)
            try:
                response = await self._session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                    ssl=False,
                )
                async with response:
                    if response.status in _OPTIONAL_STATUSES:
                        if required:
                            raise HpCdmConnectionError(
                                f"{path} returned HTTP {response.status}"
                            )
                        _LOGGER.debug(
                            "Optional endpoint %s unavailable (HTTP %s)",
                            path,
                            response.status,
                        )
                        return None
                    if response.status != 200:
                        raise HpCdmConnectionError(
                            f"{path} returned HTTP {response.status}"
                        )
                    # The firmware is not always strict about its content
                    # type, so do not let aiohttp reject the body over it.
                    payload = await response.json(content_type=None)
            except HpCdmError:
                raise
            except (TimeoutError, aiohttp.ClientError) as err:
                raise HpCdmConnectionError(f"Error fetching {path}: {err}") from err
            except ValueError as err:
                raise HpCdmConnectionError(f"Invalid JSON from {path}: {err}") from err
            finally:
                self._last_request = asyncio.get_running_loop().time()

        if not isinstance(payload, dict):
            raise HpCdmConnectionError(f"Unexpected payload type from {path}")
        return payload

    async def async_probe(self) -> dict[str, Any]:
        """Validate that the host really is a CDM-capable HP printer.

        ``/cdm/servicesDiscovery`` indexes every service the firmware exposes;
        an answer without a ``services`` key means the device speaks HTTP but
        not CDM.
        """
        payload = await self.async_get("/cdm/servicesDiscovery")
        if payload is None or "services" not in payload:
            raise HpCdmNotSupportedError("Device does not expose the CDM API")
        return payload

    async def _async_throttle(self) -> None:
        """Sleep so two requests are never sent back to back."""
        if not self._last_request:
            return
        elapsed = asyncio.get_running_loop().time() - self._last_request
        if elapsed < MIN_REQUEST_INTERVAL:
            await asyncio.sleep(MIN_REQUEST_INTERVAL - elapsed)
