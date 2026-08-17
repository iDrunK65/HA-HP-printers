"""Binary sensor platform for the HP CDM printer integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DATA_ALERTS
from .coordinator import HpCdmConfigEntry, HpCdmCoordinator
from .entity import HpCdmEntity
from .util import alert_as_dict, parse_alerts


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpCdmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the HP CDM binary sensors."""
    coordinator = entry.runtime_data.status
    async_add_entities(
        [
            HpCdmProblemBinarySensor(coordinator, entry),
            HpCdmConnectivityBinarySensor(coordinator, entry),
        ]
    )


class HpCdmProblemBinarySensor(HpCdmEntity, BinarySensorEntity):
    """On whenever the printer reports an alert of severity error or worse."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "problem"

    def __init__(
        self, coordinator: HpCdmCoordinator, entry: HpCdmConfigEntry
    ) -> None:
        """Initialise the problem sensor."""
        super().__init__(coordinator, entry, "problem")

    @property
    def is_on(self) -> bool | None:
        """Return True when at least one blocking alert is active."""
        if self.coordinator.data is None:
            return None
        alerts = parse_alerts(self.coordinator.data.get(DATA_ALERTS))
        return any(alert.rank >= 2 for alert in alerts)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the alerts behind the current state, worst first."""
        if self.coordinator.data is None:
            return None
        alerts = parse_alerts(self.coordinator.data.get(DATA_ALERTS))
        return {
            "reason": alerts[0].description if alerts else None,
            "alerts": [alert_as_dict(alert) for alert in alerts],
        }


class HpCdmConnectivityBinarySensor(HpCdmEntity, BinarySensorEntity):
    """Whether the last poll of the printer succeeded."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "connectivity"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: HpCdmCoordinator, entry: HpCdmConfigEntry
    ) -> None:
        """Initialise the connectivity sensor."""
        super().__init__(coordinator, entry, "connectivity")

    @property
    def available(self) -> bool:
        """Always available: this entity is what reports the outage.

        A printer asleep in inPowerSave still answers CDM, so a False here
        really does mean unreachable.
        """
        return True

    @property
    def is_on(self) -> bool:
        """Return True while the printer answers."""
        return self.coordinator.last_update_success
