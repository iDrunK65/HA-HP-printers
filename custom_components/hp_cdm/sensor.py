"""Sensor platform for the HP CDM printer integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import (
    DATA_ALERTS,
    DATA_POWER,
    DATA_STATISTICS,
    DATA_STATUS,
    SUPPLY_COLOR_CODES,
    UNIT_ALERTS,
    UNIT_IMAGES,
    UNIT_JOBS,
    UNIT_PAGES,
    UNIT_SHEETS,
)
from .coordinator import HpCdmConfigEntry, HpCdmCoordinator
from .entity import HpCdmEntity
from .util import alert_as_dict, alert_status, nested_get, parse_alerts, parse_hp_bool


@dataclass(frozen=True, kw_only=True)
class HpCdmSensorEntityDescription(SensorEntityDescription):
    """Describe an HP CDM sensor."""

    value_fn: Callable[[dict[str, Any]], StateType]
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _ratio(numerator: Any, denominator: Any) -> float | None:
    """Return numerator/denominator as a percentage, or None if undefined."""
    if not isinstance(numerator, (int, float)) or not isinstance(
        denominator, (int, float)
    ):
        return None
    if not denominator:
        return None
    return round(numerator / denominator * 100, 1)


def _usage(
    key: str,
    *path: str,
    unit: str,
    enabled: bool = True,
) -> HpCdmSensorEntityDescription:
    """Build a lifetime-counter sensor description."""
    return HpCdmSensorEntityDescription(
        key=key,
        translation_key=key,
        native_unit_of_measurement=unit,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=enabled,
        value_fn=lambda data, path=path: nested_get(data, *path),
    )


# --- Lifetime counters (usage coordinator) ---------------------------------
# The a4Equivalent* blocks are deliberately ignored: they restate the same
# figures in a {significand, exponent} notation (2320000 x 10^-4 = 232).
# Everything fax-related, blank sides and the network folder counter are
# disabled by default: on most machines they stay pinned at zero forever. A
# static flag is used rather than runtime detection, so the entity set stays
# predictable across restarts.
USAGE_SENSORS: tuple[HpCdmSensorEntityDescription, ...] = (
    _usage("impressions_total", "printUsage", "impressions", "total", unit=UNIT_PAGES),
    _usage(
        "impressions_mono", "printUsage", "impressions", "monochrome", unit=UNIT_PAGES
    ),
    _usage("impressions_color", "printUsage", "impressions", "color", unit=UNIT_PAGES),
    _usage(
        "impressions_blank",
        "printUsage",
        "impressions",
        "blankSides",
        unit=UNIT_PAGES,
        enabled=False,
    ),
    _usage(
        "print_impressions",
        "printUsage",
        "printOtherImpressions",
        "total",
        unit=UNIT_PAGES,
    ),
    _usage(
        "copy_impressions", "printUsage", "copyImpressions", "total", unit=UNIT_PAGES
    ),
    _usage(
        "fax_impressions",
        "printUsage",
        "faxInImpressions",
        "total",
        unit=UNIT_PAGES,
        enabled=False,
    ),
    _usage("sheets_total", "printUsage", "sheets", "total", unit=UNIT_SHEETS),
    _usage("sheets_simplex", "printUsage", "sheets", "simplex", unit=UNIT_SHEETS),
    _usage("sheets_duplex", "printUsage", "sheets", "duplex", unit=UNIT_SHEETS),
    _usage("scan_total", "scanUsage", "totalImages", unit=UNIT_IMAGES),
    _usage("scan_send", "scanUsage", "sendImages", unit=UNIT_IMAGES),
    _usage("scan_copy", "scanUsage", "copyImages", unit=UNIT_IMAGES),
    _usage("scan_adf", "scanUsage", "adfImages", unit=UNIT_IMAGES),
    _usage("scan_fax", "scanUsage", "faxImages", unit=UNIT_IMAGES, enabled=False),
    _usage("jobs_print", "jobUsage", "printJobCount", unit=UNIT_JOBS),
    _usage("jobs_copy", "jobUsage", "copyJobCount", unit=UNIT_JOBS),
    _usage("jobs_email", "jobUsage", "emailJobCount", unit=UNIT_JOBS),
    _usage(
        "jobs_network_folder",
        "jobUsage",
        "networkFolderJobCount",
        unit=UNIT_JOBS,
        enabled=False,
    ),
    _usage(
        "jobs_fax_sent", "jobUsage", "sendFaxJobCount", unit=UNIT_JOBS, enabled=False
    ),
    _usage(
        "jobs_fax_received",
        "jobUsage",
        "receiveFaxJobCount",
        unit=UNIT_JOBS,
        enabled=False,
    ),
    HpCdmSensorEntityDescription(
        key="color_ratio",
        translation_key="color_ratio",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: _ratio(
            nested_get(data, "printUsage", "impressions", "color"),
            nested_get(data, "printUsage", "impressions", "total"),
        ),
    ),
    HpCdmSensorEntityDescription(
        key="duplex_ratio",
        translation_key="duplex_ratio",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: _ratio(
            nested_get(data, "printUsage", "sheets", "duplex"),
            nested_get(data, "printUsage", "sheets", "total"),
        ),
    ),
)


# --- State and diagnostics (status coordinator) ----------------------------
STATUS_SENSORS: tuple[HpCdmSensorEntityDescription, ...] = (
    HpCdmSensorEntityDescription(
        key="printer_status",
        translation_key="printer_status",
        # No device_class ENUM here: the firmware documents no closed list of
        # states, and "inPowerSave" is a perfectly healthy one.
        value_fn=lambda data: nested_get(data, DATA_STATUS, "status"),
    ),
    HpCdmSensorEntityDescription(
        key="alert_status",
        translation_key="alert_status",
        device_class=SensorDeviceClass.ENUM,
        options=["ok", "warning", "error"],
        value_fn=lambda data: alert_status(parse_alerts(data.get(DATA_ALERTS))),
        attributes_fn=lambda data: {
            "alerts": [
                alert_as_dict(alert) for alert in parse_alerts(data.get(DATA_ALERTS))
            ]
        },
    ),
    HpCdmSensorEntityDescription(
        key="alert_count",
        translation_key="alert_count",
        native_unit_of_measurement=UNIT_ALERTS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: len(parse_alerts(data.get(DATA_ALERTS))),
    ),
    HpCdmSensorEntityDescription(
        key="power_cycle_count",
        translation_key="power_cycle_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: nested_get(data, DATA_STATISTICS, "powerCycleCount"),
    ),
    HpCdmSensorEntityDescription(
        key="available_memory",
        translation_key="available_memory",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.KILOBYTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: nested_get(data, DATA_STATISTICS, "availableMemory"),
        attributes_fn=lambda data: {
            "total_memory": nested_get(data, DATA_STATISTICS, "totalMemory")
        },
    ),
    HpCdmSensorEntityDescription(
        key="sleep_timeout",
        translation_key="sleep_timeout",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: nested_get(data, DATA_POWER, "sleepTimeout"),
        # The rest of the power configuration is read-only context; this
        # integration never writes it back.
        attributes_fn=lambda data: {
            "inactivity_timeout": nested_get(data, DATA_POWER, "inactivityTimeout"),
            "shutdown_timeout_minutes": nested_get(
                data, DATA_POWER, "shutdownTimeoutInMinutes"
            ),
            "shutdown_prevention": nested_get(data, DATA_POWER, "shutdownPrevention"),
            "auto_shutdown_enabled": parse_hp_bool(
                nested_get(data, DATA_POWER, "autoShutdownEnabled")
            ),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpCdmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the HP CDM sensors."""
    data = entry.runtime_data

    entities: list[SensorEntity] = [
        HpCdmSensor(data.usage, entry, description) for description in USAGE_SENSORS
    ]
    entities += [
        HpCdmSensor(data.status, entry, description) for description in STATUS_SENSORS
    ]

    # Cartridges are enumerated once, from the first successful refresh. A
    # cartridge added in a slot that was empty at setup shows up after a
    # reload of the entry.
    for supply in nested_get(data.supply.data, "suppliesList", default=[]) or []:
        if not isinstance(supply, dict):
            continue
        entities.append(
            HpCdmSupplySensor(
                data.supply,
                entry,
                slot=supply.get("slot"),
                color_code=str(supply.get("supplyColorCode") or ""),
            )
        )

    async_add_entities(entities)


class HpCdmSensor(HpCdmEntity, SensorEntity):
    """A sensor backed by a single value from a CDM payload."""

    entity_description: HpCdmSensorEntityDescription

    def __init__(
        self,
        coordinator: HpCdmCoordinator,
        entry: HpCdmConfigEntry,
        description: HpCdmSensorEntityDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return True only when the underlying value is actually present.

        Endpoints missing on another model return None rather than failing the
        refresh, so the individual sensors go unavailable instead.
        """
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes, if this sensor exposes any."""
        if self.entity_description.attributes_fn is None or self.coordinator.data is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)


class HpCdmSupplySensor(HpCdmEntity, SensorEntity):
    """Remaining life of a single cartridge."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: HpCdmCoordinator,
        entry: HpCdmConfigEntry,
        *,
        slot: Any,
        color_code: str,
    ) -> None:
        """Initialise the cartridge sensor."""
        code = color_code.strip().upper()
        key = f"supply_{code or 'x'}_{slot}"
        super().__init__(coordinator, entry, key)
        self._slot = slot
        self._color_code = code

        if code in SUPPLY_COLOR_CODES:
            self._attr_translation_key = f"supply_{code.lower()}"
        else:
            # Unknown colour codes still get a usable name from their slot.
            self._attr_translation_key = "supply_generic"
            self._attr_translation_placeholders = {"slot": str(slot)}

    @callback
    def _supply(self) -> dict[str, Any] | None:
        """Return this cartridge's entry in the supplies list."""
        for supply in nested_get(self.coordinator.data, "suppliesList", default=[]) or []:
            if not isinstance(supply, dict):
                continue
            code = str(supply.get("supplyColorCode") or "").strip().upper()
            if supply.get("slot") == self._slot and code == self._color_code:
                return supply
        return None

    @property
    def available(self) -> bool:
        """Return True while the cartridge is still reported by the printer."""
        return super().available and self._supply() is not None

    @property
    def native_value(self) -> StateType:
        """Return the remaining life percentage."""
        supply = self._supply()
        if supply is None:
            return None
        value = supply.get("percentLifeDisplay")
        return value if isinstance(value, (int, float)) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the rich cartridge metadata CDM exposes."""
        supply = self._supply()
        if supply is None:
            return None

        # "greaterThan" means "more than N pages left", not "N pages left".
        # Surfacing the flag lets automations avoid treating a floor as exact.
        pages_symbol = supply.get("approximatePagesRemainingDisplaySymbol")
        percent_symbol = supply.get("percentLifeDisplaySymbol")

        return {
            "slot": supply.get("slot"),
            "color_code": supply.get("supplyColorCode"),
            "supply_type": supply.get("supplyType"),
            "supply_state": supply.get("supplyState"),
            "level_state": supply.get("levelState"),
            "state_reasons": supply.get("stateReasons"),
            "order_part_number": supply.get("orderPartNumber"),
            "product_number": supply.get("productNumber"),
            "capacity": supply.get("capacity"),
            "capacity_unit": supply.get("capacityUnit"),
            "serial_number": supply.get("serialNumber"),
            "manufacture_date": supply.get("manufactureDate"),
            "warranty_status": supply.get("warrantyStatus"),
            # These arrive as the strings "true"/"false", not JSON booleans.
            "is_genuine_hp": parse_hp_bool(supply.get("isGenuineHP")),
            "is_refilled": parse_hp_bool(supply.get("isRefilled")),
            "is_used": parse_hp_bool(supply.get("isUsed")),
            "pages_remaining": supply.get("approximatePagesRemainingDisplay"),
            "pages_remaining_is_lower_bound": pages_symbol == "greaterThan",
            "percent_life_is_lower_bound": percent_symbol == "greaterThan",
        }
