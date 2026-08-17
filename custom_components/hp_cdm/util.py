"""Helpers for navigating and normalising HP CDM payloads."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .const import ALERT_STATUS_ERROR, ALERT_STATUS_OK, ALERT_STATUS_WARNING

# Severity values are not documented in /cdm/alert/v1/capabilities (the
# validators omit the field entirely) even though real alerts do carry one, so
# treat this mapping as open-ended and fall back on the category name.
_SEVERITY_RANK: dict[str, int] = {
    "informational": 0,
    "information": 0,
    "info": 0,
    "status": 0,
    "notice": 0,
    "warning": 1,
    "warn": 1,
    "error": 2,
    "critical": 3,
    "fatal": 3,
}

# Keyword fallback used when an alert carries no severity at all. There are
# 420 possible categories; mapping them by hand is not maintainable, so only
# classify them.
_ERROR_KEYWORDS = (
    "jam",
    "door",
    "cover",
    "empty",
    "missing",
    "fail",
    "error",
    "outof",
    "incompatible",
    "unsupported",
    "shutdown",
)
_WARNING_KEYWORDS = ("low", "warning", "replace", "maintenance", "soon", "used")

# Only a handful of categories get a friendly label; everything else is
# surfaced raw so a category we have never seen is still readable.
_CATEGORY_LABELS: dict[str, str] = {
    "doorOpen": "Door open",
    "coverOpen": "Cover open",
    "allTraysEmpty": "All trays empty",
    "trayEmpty": "Tray empty",
    "cartridgeLow": "Cartridge low",
    "cartridgeVeryLow": "Cartridge very low",
    "cartridgeMissing": "Cartridge missing",
    "cartridgeOut": "Cartridge depleted",
    "outputBinFull": "Output bin full",
}

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# /cdm/system/v1/identity has never been observed on real hardware, so its
# schema is unknown. Instead of assuming one, probe for any of the key names HP
# uses elsewhere in the CDM tree. The bare "version" key is deliberately absent
# from these lists: every CDM payload carries one and it holds the *schema*
# version, not the firmware version.
IDENTITY_MODEL_KEYS: tuple[str, ...] = (
    "makeAndModel",
    "makeAndModelBase",
    "modelName",
    "productName",
    "deviceName",
)
IDENTITY_SERIAL_KEYS: tuple[str, ...] = (
    "serialNumber",
    "productSerialNumber",
    "deviceSerialNumber",
)
IDENTITY_FIRMWARE_KEYS: tuple[str, ...] = (
    "firmwareRevision",
    "firmwareVersion",
    "fwVersion",
    "currentFirmwareVersion",
)


def nested_get(data: Any, *keys: str, default: Any = None) -> Any:
    """Return ``data[key1][key2]...`` or ``default`` if any hop is missing."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        if key not in current:
            return default
        current = current[key]
    return current if current is not None else default


def parse_hp_bool(value: Any) -> bool | None:
    """Convert HP's stringly-typed booleans into real booleans.

    ``suppliesPublic`` reports ``"true"`` / ``"false"`` as JSON *strings*, not
    JSON booleans, so a naive truthiness test would read ``"false"`` as True.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return None


def humanize(value: Any) -> str | None:
    """Turn a camelCase CDM identifier into a readable phrase."""
    if not isinstance(value, str) or not value:
        return None
    spaced = _CAMEL_BOUNDARY.sub(" ", value)
    return spaced[0].upper() + spaced[1:].lower() if len(spaced) > 1 else spaced


def find_value(data: Any, keys: tuple[str, ...]) -> Any:
    """Depth-first search for the first of ``keys`` present in ``data``.

    ``/cdm/system/v1/identity`` has never been observed, so its schema is
    unknown; this walks whatever it returns looking for plausible key names
    instead of assuming a shape.
    """
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                return value
        for value in data.values():
            found = find_value(value, keys)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_value(item, keys)
            if found is not None:
                return found
    return None


@dataclass(frozen=True, kw_only=True)
class ParsedAlert:
    """A single printer alert, normalised for display."""

    id: str
    category: str
    severity: str
    rank: int
    priority: int
    date_time: str | None
    detail: str | None
    description: str


def _severity_rank(severity: str | None, category: str) -> int:
    """Rank an alert, falling back on keywords when severity is missing."""
    if severity:
        rank = _SEVERITY_RANK.get(severity.strip().lower())
        if rank is not None:
            return rank
    lowered = category.lower()
    if any(keyword in lowered for keyword in _ERROR_KEYWORDS):
        return 2
    if any(keyword in lowered for keyword in _WARNING_KEYWORDS):
        return 1
    return 0


def _alert_detail(alert: dict[str, Any]) -> str | None:
    """Extract the concrete subject of an alert from its ``data`` block.

    This is what turns a generic "doorOpen" into "front door": the sensor
    identity lives in the entry whose propertyPointer ends with ``/id``.
    """
    for item in alert.get("data") or []:
        if not isinstance(item, dict):
            continue
        pointer = item.get("propertyPointer")
        if isinstance(pointer, str) and pointer.endswith("/id"):
            value = nested_get(item, "value", "seValue")
            if isinstance(value, str) and value:
                return value
    return None


def _alert_label(category: str) -> str:
    """Return a friendly label for a category, or the raw category."""
    if label := _CATEGORY_LABELS.get(category):
        return label
    if category.lower().startswith("jam"):
        return "Paper jam"
    return humanize(category) or category


def parse_alerts(payload: dict[str, Any] | None) -> list[ParsedAlert]:
    """Normalise ``/cdm/alert/v1/alerts`` into a sorted list of alerts.

    Sorted most severe first, then by ``priority`` ascending (lower is more
    urgent) so the first item is always the one worth showing.
    """
    alerts: list[ParsedAlert] = []
    for raw in nested_get(payload, "alerts", default=[]) or []:
        if not isinstance(raw, dict):
            continue
        category = str(raw.get("category") or "unknown")
        severity = raw.get("severity")
        rank = _severity_rank(severity if isinstance(severity, str) else None, category)
        detail = _alert_detail(raw)
        label = _alert_label(category)
        description = f"{label} ({humanize(detail)})" if detail else label
        alerts.append(
            ParsedAlert(
                id=str(raw.get("id", "")),
                category=category,
                severity=str(severity) if isinstance(severity, str) else "unknown",
                rank=rank,
                priority=int(raw.get("priority", 99))
                if isinstance(raw.get("priority"), int)
                else 99,
                date_time=raw.get("dateTime")
                if isinstance(raw.get("dateTime"), str)
                else None,
                detail=detail,
                description=description,
            )
        )
    alerts.sort(key=lambda alert: (-alert.rank, alert.priority))
    return alerts


def alert_status(alerts: list[ParsedAlert]) -> str:
    """Summarise a list of alerts as ok / warning / error."""
    if not alerts:
        return ALERT_STATUS_OK
    worst = max(alert.rank for alert in alerts)
    if worst >= 2:
        return ALERT_STATUS_ERROR
    if worst == 1:
        return ALERT_STATUS_WARNING
    return ALERT_STATUS_OK


def alert_as_dict(alert: ParsedAlert) -> dict[str, Any]:
    """Render an alert as attribute-friendly data."""
    return {
        "id": alert.id,
        "category": alert.category,
        "severity": alert.severity,
        "priority": alert.priority,
        "detail": alert.detail,
        "description": alert.description,
        "date_time": alert.date_time,
    }
