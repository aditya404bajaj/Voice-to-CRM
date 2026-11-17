"""Validation and cleaning for field-log data."""
import re
from datetime import date, datetime

STATUSES = ["Pending", "Follow-up Required", "Completed"]

LIMITS = {
    "customer_name": 150,
    "location": 200,
    "contact_person": 150,
    "observations": 5000,
    "issues": 5000,
    "follow_up": 5000,
    "transcript": 30000,
}

REQUIRED = {
    "customer_name": "Customer name",
    "location": "Location",
    "visit_date": "Visit date",
    "status": "Status",
}

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_date(value):
    """Return a `date` for a YYYY-MM-DD string, otherwise None."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not _ISO.match(value.strip()):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def normalize_status(value):
    """Match a status case-insensitively; returns the canonical value or None."""
    if not isinstance(value, str):
        return None
    wanted = value.strip().lower().replace("_", "-")
    for status in STATUSES:
        if status.lower() == wanted:
            return status
    return None


def clean_log(data):
    """Validate user input for a field log.

    Returns (clean_values, errors). `errors` maps field name -> message and is
    empty when everything is valid.
    """
    data = data or {}
    clean, errors = {}, {}

    for field, limit in LIMITS.items():
        raw = data.get(field)
        text = "" if raw is None else str(raw).strip()
        if len(text) > limit:
            errors[field] = f"Keep this under {limit} characters."
        clean[field] = text

    for field, label in REQUIRED.items():
        if field in ("visit_date", "status"):
            continue
        if not clean.get(field) and field not in errors:
            errors[field] = f"{label} is required."

    visit = parse_date(data.get("visit_date"))
    if visit is None:
        errors["visit_date"] = "Enter the visit date as YYYY-MM-DD."
    clean["visit_date"] = visit.isoformat() if visit else ""

    follow_raw = data.get("follow_up_date")
    follow_raw = "" if follow_raw is None else str(follow_raw).strip()
    if follow_raw:
        follow = parse_date(follow_raw)
        if follow is None:
            errors["follow_up_date"] = "Enter the follow-up date as YYYY-MM-DD."
        clean["follow_up_date"] = follow.isoformat() if follow else ""
    else:
        clean["follow_up_date"] = ""

    status = normalize_status(data.get("status"))
    if status is None:
        errors["status"] = "Choose Pending, Follow-up Required or Completed."
    clean["status"] = status or ""

    return clean, errors
