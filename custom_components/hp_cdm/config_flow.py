"""Config flow for the HP CDM printer integration.

Only a host is ever asked for. The integration is deliberately 100% anonymous:
the CDM API does offer an OAuth2 token endpoint, but the resources it unlocks
(notably the job history, which exposes printed file names and Windows user
names) are a privacy problem on a shared printer, so no credentials are
collected or stored.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import HpCdmClient, HpCdmConnectionError, HpCdmNotSupportedError
from .const import CONF_MODEL, CONF_UUID, DEFAULT_DEVICE_NAME, DOMAIN, ENDPOINT_IDENTITY
from .util import IDENTITY_MODEL_KEYS, find_value

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


class HpCdmConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HP CDM printers."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._host: str | None = None
        self._model: str | None = None
        self._uuid: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a manually initiated flow."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            # A printer already added through discovery is keyed on its mDNS
            # UUID, so only a host match can catch it here.
            self._async_abort_entries_match({CONF_HOST: host})
            try:
                model = await self._async_validate(host)
            except HpCdmNotSupportedError:
                errors["base"] = "not_supported"
            except HpCdmConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - surfaced to the user as "unknown"
                _LOGGER.exception("Unexpected error validating %s", host)
                errors["base"] = "unknown"
            else:
                # Without mDNS there is no UUID to key on, so fall back to the
                # host, which is what the user typed anyway.
                await self.async_set_unique_id(host, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=model or host,
                    data={CONF_HOST: host, CONF_MODEL: model},
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle a printer discovered over mDNS."""
        # The built-in IPP integration listens on the very same service type,
        # so filter on the manufacturer here rather than in the manifest and
        # walk away quietly from anything that is not an HP.
        properties = {
            key.lower(): value for key, value in discovery_info.properties.items()
        }
        manufacturer = str(
            properties.get("mfg") or properties.get("usb_mfg") or ""
        ).lower()
        if "hp" not in manufacturer and "hewlett" not in manufacturer:
            return self.async_abort(reason="not_hp")

        host = discovery_info.host
        # The IPP TXT record UUID is stable across reboots and DHCP leases.
        # Careful: the deviceUuid found in the OAuth2 issuer is a *different*
        # identifier; this one is the right one to key entries on.
        uuid = str(properties.get("uuid") or "").removeprefix("urn:uuid:")
        model = str(properties.get("ty") or "") or None

        await self.async_set_unique_id(uuid or host)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        # Catches a printer already added by hand before it was discovered.
        self._async_abort_entries_match({CONF_HOST: host})

        try:
            model = await self._async_validate(host) or model
        except HpCdmNotSupportedError:
            return self.async_abort(reason="not_supported")
        except HpCdmConnectionError:
            return self.async_abort(reason="cannot_connect")

        self._host = host
        self._model = model
        self._uuid = uuid or None
        self.context["title_placeholders"] = {"name": model or DEFAULT_DEVICE_NAME}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm adding a discovered printer."""
        assert self._host is not None

        if user_input is not None:
            return self.async_create_entry(
                title=self._model or self._host,
                data={
                    CONF_HOST: self._host,
                    CONF_MODEL: self._model,
                    CONF_UUID: self._uuid,
                },
            )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={
                "name": self._model or DEFAULT_DEVICE_NAME,
                "host": self._host,
            },
        )

    async def _async_validate(self, host: str) -> str | None:
        """Check the host speaks CDM and return its model name if it says so.

        Raises :class:`HpCdmNotSupportedError` when the device answers but has
        no ``services`` key, which is how a firmware without the CDM API looks.
        """
        session = async_get_clientsession(self.hass, verify_ssl=False)
        client = HpCdmClient(session, host)
        await client.async_probe()

        # Purely cosmetic, and never observed on the reference hardware, so a
        # failure here must not fail the flow.
        try:
            identity = await client.async_get(ENDPOINT_IDENTITY, required=False)
        except HpCdmConnectionError:
            return None
        model = find_value(identity, IDENTITY_MODEL_KEYS) if identity else None
        return str(model) if model else None
