"""Speech-to-text and field extraction.

* With OPENAI_API_KEY set: audio goes to Whisper, and the transcript is turned
  into structured fields by a chat model.
* Without a key (or if the AI call fails): `basic_extract` fills what it can
  using simple pattern matching, so the app still works.

OpenAI or Groq (free plan) is called over plain HTTPS with `requests`, so no extra SDK is needed.
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import requests
from flask import current_app

from app.i18n import gettext as _
from app.validation import STATUSES, normalize_status, parse_date

FIELD_KEYS = [
    "customer_name", "location", "contact_person", "visit_date",
    "observations", "issues", "follow_up", "follow_up_date", "status",
]


class AIError(Exception):
    """An AI call failed. The message is safe to show to the user."""


def ai_configured():
    return bool(current_app.config.get("OPENAI_API_KEY"))


# ---------------------------------------------------------------------------
# OpenAI helpers
# ---------------------------------------------------------------------------

def _headers():
    return {"Authorization": f"Bearer {current_app.config['OPENAI_API_KEY']}"}


def _provider():
    return current_app.config.get("AI_PROVIDER_NAME") or "OpenAI"


def _explain_http_error(resp):
    try:
        detail = resp.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    name = _provider()
    if resp.status_code == 401:
        return f"{name} rejected the API key. Check the key in your .env file."
    if resp.status_code == 429:
        return (f"{name} says the rate limit or quota is used up. Wait a minute and try again, "
                "or type your notes. The recording is saved.")
    if resp.status_code == 413:
        return "The recording is too large for transcription (limit 25 MB)."
    return f"{name} returned an error ({resp.status_code}). {detail}".strip()


def _post(path, **kwargs):
    cfg = current_app.config
    try:
        resp = requests.post(
            f"{cfg['OPENAI_BASE_URL']}{path}",
            headers=_headers(),
            timeout=cfg["OPENAI_TIMEOUT"],
            **kwargs,
        )
    except requests.RequestException as exc:
        raise AIError(f"Could not reach {_provider()}. Check your internet connection.") from exc
    if resp.status_code >= 400:
        raise AIError(_explain_http_error(resp))
    try:
        return resp.json()
    except ValueError as exc:
        raise AIError(f"{_provider()} sent a reply that could not be read.") from exc


def transcribe_audio(path):
    """Send an audio file to Whisper and return the transcript text."""
    path = Path(path)
    cfg = current_app.config
    data = {"model": cfg["OPENAI_TRANSCRIBE_MODEL"], "response_format": "json"}
    if cfg.get("TRANSCRIBE_LANGUAGE"):
        data["language"] = cfg["TRANSCRIBE_LANGUAGE"]
    with open(path, "rb") as fh:
        result = _post("/audio/transcriptions", data=data, files={"file": (path.name, fh)})
    return (result.get("text") or "").strip()


EXTRACTION_RULES = """\
You extract field-visit notes for a CRM. The transcript may be English, Hindi or
Hinglish; write every value in English (keep names and places as spoken).

Return ONLY a JSON object with exactly these keys:
customer_name, location, contact_person, visit_date, observations, issues,
follow_up, follow_up_date, status

Rules:
1. Never invent information. If something is not said, use an empty string.
2. visit_date and follow_up_date use YYYY-MM-DD. Work out words like "today",
   "yesterday", "tomorrow" or "next Monday" from today's date, given below.
   If no visit date is mentioned, use an empty string.
3. observations = what was seen or discussed. issues = problems or complaints.
   follow_up = the action to take next, in one or two short sentences.
4. status must be exactly one of: Completed, Follow-up Required, Pending.
   Use "Follow-up Required" when a next action is needed.
"""


def _strip_code_fence(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def ai_extract(transcript, today):
    cfg = current_app.config
    system = EXTRACTION_RULES + f"\nToday is {today.strftime('%A')}, {today.isoformat()}."
    result = _post(
        "/chat/completions",
        json={
            "model": cfg["OPENAI_CHAT_MODEL"],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Transcript:\n{transcript}"},
            ],
        },
    )
    try:
        content = result["choices"][0]["message"]["content"]
        return json.loads(_strip_code_fence(content))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIError("The AI reply was not valid JSON. Try again.") from exc


# ---------------------------------------------------------------------------
# Cleaning model output
# ---------------------------------------------------------------------------

def sanitize_fields(raw):
    """Keep only known keys, as clean strings, with valid dates and status."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key in FIELD_KEYS:
        value = raw.get(key, "")
        out[key] = "" if value is None else str(value).strip()
    for key in ("visit_date", "follow_up_date"):
        parsed = parse_date(out[key])
        out[key] = parsed.isoformat() if parsed else ""
    out["status"] = normalize_status(out["status"]) or ""
    return out


# ---------------------------------------------------------------------------
# Basic (no-AI) extraction
# ---------------------------------------------------------------------------

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_TITLE = r"(?:Mr|Mrs|Ms|Miss|Dr|Shri|Smt|Sir|Madam)\.?"
_NAME = r"[A-Z][\w&'\-]*(?:\s+[A-Z][\w&'\-]*){0,3}"
_STOP_WORDS = {
    "I", "We", "The", "He", "She", "They", "It", "This", "That", "Monday", "Tuesday",
    "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Today", "Tomorrow",
    "Yesterday", "Next", "Also", "And", "But", "Customer", "Client", "Visit",
}

_ISSUE_WORDS = re.compile(
    r"\b(issue|issues|problem|problems|complaint|complaints|not working|broken|damaged|"
    r"delay|delayed|defect|faulty|leak|leaking|unhappy|pending payment|overdue)\b", re.I)
_FOLLOW_WORDS = re.compile(
    r"\b(follow[- ]?up|call (?:him|her|them|back)|call back|revisit|next visit|visit again|"
    r"come back|send (?:a )?(?:quote|quotation|proposal|sample)|schedule|remind)\b", re.I)


def _clean_name(value):
    words = [w for w in value.split() if w.rstrip(".") not in _STOP_WORDS]
    return " ".join(words).strip(" .,")


def _first_match(patterns, text):
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            name = _clean_name(m.group(1))
            if name:
                return name
    return ""


def _find_relative_date(text, today):
    """Find a date in a snippet: ISO date, today/tomorrow/yesterday, weekday, 'in N days'."""
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if iso and parse_date(iso.group(1)):
        return parse_date(iso.group(1))
    low = text.lower()
    m = re.search(r"\bin\s+(\d{1,2})\s+days?\b", low)
    if m:
        return today + timedelta(days=int(m.group(1)))
    if "day after tomorrow" in low:
        return today + timedelta(days=2)
    if "tomorrow" in low:
        return today + timedelta(days=1)
    if "yesterday" in low:
        return today - timedelta(days=1)
    if "next week" in low:
        return today + timedelta(days=7)
    if re.search(r"\btoday\b", low):
        return today
    for index, name in enumerate(WEEKDAYS):
        if re.search(rf"\b{name}\b", low):
            ahead = (index - today.weekday()) % 7 or 7
            return today + timedelta(days=ahead)
    return None


def basic_extract(transcript, today=None):
    """Best-effort extraction using patterns. Less accurate than the AI path."""
    today = today or date.today()
    text = " ".join((transcript or "").split())
    fields = {key: "" for key in FIELD_KEYS}
    if not text:
        return fields

    customer = _first_match([
        rf"(?:customer|client|company|shop|store|firm)(?:\s+name)?\s+(?:is|was|named)\s+({_NAME})",
        rf"(?:visited|visit to|visiting|met with|meeting with|called on|went to)\s+(?:{_TITLE}\s+)?({_NAME})",
    ], text)
    fields["customer_name"] = customer

    fields["contact_person"] = _first_match([
        rf"contact(?:\s+person)?\s+(?:is|was)\s+(?:{_TITLE}\s+)?({_NAME})",
        rf"(?:met|spoke (?:to|with)|talked (?:to|with)|discussed with)\s+{_TITLE}\s+({_NAME})",
        rf"(?:met|spoke (?:to|with)|talked (?:to|with))\s+({_NAME})",
    ], text)
    if fields["contact_person"] and fields["contact_person"] == customer:
        fields["contact_person"] = ""

    location = _first_match([
        rf"(?:location|area|city|town)\s+(?:is|was)\s+({_NAME})",
        rf"\b(?:in|near)\s+({_NAME})",
        rf"\bat\s+({_NAME})",
    ], text)
    if location and (location == customer or location in customer):
        location = ""
    fields["location"] = location

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    issues = [s for s in sentences if _ISSUE_WORDS.search(s)]
    follow = [s for s in sentences if _FOLLOW_WORDS.search(s)]
    rest = [s for s in sentences if s not in issues and s not in follow]
    fields["issues"] = " ".join(issues)
    fields["follow_up"] = " ".join(follow)
    fields["observations"] = " ".join(rest) if rest else ""

    if follow:
        found = _find_relative_date(fields["follow_up"], today)
        if found and found >= today:
            fields["follow_up_date"] = found.isoformat()

    visit = None
    for sentence in sentences:
        if sentence in follow:
            continue
        visit = _find_relative_date(sentence, today)
        if visit and visit <= today:
            break
        visit = None
    fields["visit_date"] = visit.isoformat() if visit else ""

    if follow:
        fields["status"] = "Follow-up Required"
    elif re.search(r"\b(completed|complete|done|resolved|finished|closed|installed|delivered)\b", text, re.I):
        fields["status"] = "Completed"
    else:
        fields["status"] = "Pending"
    return sanitize_fields(fields)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_fields(transcript, today=None):
    """Return (fields, method, warning). method is 'ai' or 'basic'."""
    today = today or date.today()
    if ai_configured():
        try:
            return sanitize_fields(ai_extract(transcript, today)), "ai", None
        except AIError as exc:
            return basic_extract(transcript, today), "basic", f"{exc} {_('Used basic extraction instead.')}"
    return (
        basic_extract(transcript, today),
        "basic",
        _("No OpenAI key is set, so basic extraction was used. Please check every field."),
    )
