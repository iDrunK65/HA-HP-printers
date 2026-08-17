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

# Key names to probe for device identity. Confirmed against a Color LaserJet
# Pro MFP 3302 (firmware 6.28.3.30), plus the variants HP uses elsewhere in the
# CDM tree so other models still resolve. The bare "version" key is
# deliberately absent from these lists: every CDM payload carries one and it
# holds the *schema* version, not the firmware version.
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
# The SKU identifies the exact variant (for example "3302fdn"), which the
# model name alone does not convey; the product number is the orderable
# reference and makes a reasonable second choice.
IDENTITY_MODEL_ID_KEYS: tuple[str, ...] = ("skuIdentifier", "productNumber")

# Not every CDM field is a scalar. identity reports the model as
#   "makeAndModel": {"base": ..., "family": ..., "name": ...}
# so a match on the right key can still hand back an object. Unwrap those
# through the sub-keys HP uses, "base" first because "family" would widen a
# single model into a whole product range.
_UNWRAP_KEYS: tuple[str, ...] = ("base", "name", "value", "seValue")


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


def as_scalar(value: Any) -> str | int | float | None:
    """Reduce a CDM value to a scalar, unwrapping object wrappers."""
    # bool is a subclass of int, and a flag is never an identity value.
    if isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)) and str(value).strip():
        return value
    if isinstance(value, dict):
        for key in _UNWRAP_KEYS:
            if (found := as_scalar(value.get(key))) is not None:
                return found
    return None


def find_value(data: Any, keys: tuple[str, ...]) -> Any:
    """Depth-first search for the first of ``keys`` present in ``data``.

    Models differ in where they put things, so this walks whatever the printer
    returns looking for plausible key names instead of assuming a shape.
    """
    if isinstance(data, dict):
        for key in keys:
            if (value := as_scalar(data.get(key))) is not None:
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


_CDM_PATH = re.compile(r"^/cdm/[A-Za-z0-9/_.-]+$")

# A path must look like device identity...
_IDENTITY_HINTS = ("identity", "configuration", "productconfig", "deviceinfo")
# ...and belong to a device-scoped service, which is what keeps
# /cdm/power/v1/configuration and /cdm/firewall/v2/configuration out.
_IDENTITY_SCOPES = ("system", "product", "device")
# Defence in depth. Nothing here is ever reachable anyway, since the client
# only implements GET, but a probe must not even *address* a resource whose
# name suggests it mutates the printer.
_NEVER_PROBE = (
    "reset",
    "reboot",
    "erase",
    "format",
    "password",
    "private",
    "devtest",
    "firmwareupdate",
    "shutdown",
)


def collect_cdm_paths(data: Any) -> list[str]:
    """Collect every /cdm/... path advertised anywhere inside a payload.

    servicesDiscovery indexes the whole firmware, but its exact shape is not
    documented, so walk it blindly and keep anything that looks like a link
    rather than assuming a structure.
    """
    found: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)
        elif isinstance(node, str) and _CDM_PATH.match(node) and node not in found:
            found.append(node)

    _walk(data)
    return found


def identity_candidates(paths: list[str], *, limit: int = 3) -> list[str]:
    """Rank the advertised paths most likely to describe the device.

    Returns at most ``limit`` of them: this hardware is fragile, and probing
    is only ever worth a couple of extra requests at setup.
    """

    def _rank(path: str) -> int:
        lowered = path.lower()
        if "identity" in lowered:
            return 0
        if "productconfig" in lowered or "deviceinfo" in lowered:
            return 1
        return 2

    selected = [
        path
        for path in paths
        if any(hint in path.lower() for hint in _IDENTITY_HINTS)
        and any(scope in path.lower() for scope in _IDENTITY_SCOPES)
        and not any(danger in path.lower() for danger in _NEVER_PROBE)
    ]
    return sorted(selected, key=_rank)[:limit]


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
