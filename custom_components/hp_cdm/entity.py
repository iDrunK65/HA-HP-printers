"""Base entity for the HP CDM printer integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import HpCdmConfigEntry, HpCdmCoordinator


class HpCdmEntity(CoordinatorEntity[HpCdmCoordinator]):
    """Common device wiring for every HP CDM entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HpCdmCoordinator,
        entry: HpCdmConfigEntry,
        key: str,
    ) -> None:
        """Initialise the entity and attach it to the printer device."""
        super().__init__(coordinator)
        self._entry = entry
        base_id = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{base_id}_{key}"

        device = entry.runtime_data.device
        self._attr_device_info = DeviceInfo(
            # No `connections`: since Home Assistant 2026.8 a device belongs to
            # a single config entry, and declaring the MAC would risk merging
            # with the device created by the built-in IPP integration.
            identifiers={(DOMAIN, base_id)},
            manufacturer=MANUFACTURER,
            model=device.model,
            model_id=device.model_id,
            name=device.name,
            serial_number=device.serial_number,
            sw_version=device.firmware,
            configuration_url=f"https://{entry.data[CONF_HOST]}",
        )
