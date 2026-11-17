"""English / Hindi screens.

English text is the key: `_("Save visit log")` returns the Hindi version when
the user picked Hindi, otherwise the English text itself. Strings that are not
in the Hindi table fall back to English, so nothing ever breaks.
"""
import re
from datetime import datetime
from urllib.parse import urlencode

from flask import request, session

from app.translations_hi import HI

LANGS = {"en": "English", "hi": "हिन्दी"}
MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTHS_HI = ["जन॰", "फ़र॰", "मार्च", "अप्रै॰", "मई", "जून", "जुल॰", "अग॰", "सित॰", "अक्टू॰", "नव॰", "दिस॰"]

# Messages used by static/js/record.js (sent to the page as JSON).
JS_KEYS = [
    "This browser cannot record. Use Chrome, Edge, Firefox or Safari, on https or localhost. "
    "You can still upload a file or type notes.",
    "Recording. Speak now, then tap the button to stop.",
    "Camera or microphone access is blocked. Allow it in your browser's site settings, then try again.",
    "Could not start the recording:",
    "Transcribing your recording…",
    "Saving your recording…",
    "Transcription failed.",
    "Transcript ready. Filling the fields…",
    "No speech was detected. Try again, or type your notes.",
    "Could not reach the server. Check your connection and try again.",
    "Add a transcript first: record, upload audio, or type your notes.",
    "Reading your notes…",
    "Could not read the notes.",
    "Filled {n} field(s) using {how}. Fields marked with a green edge came from your notes; please check them.",
    "AI",
    "basic matching",
    "Check the visit details, then save.",
    "Saving…",
    "Save visit log",
    "Could not save the visit log.",
    "Could not reach the server. Your entries are still here; try again.",
]


def current_lang():
    lang = session.get("lang")
    return lang if lang in LANGS else "en"


def gettext(text, **kwargs):
    if current_lang() == "hi":
        text = HI.get(text, text)
    return text.format(**kwargs) if kwargs else text


def ngettext(one, many, n, **kwargs):
    return gettext(one if n == 1 else many, n=n, **kwargs)


def _translate_message(msg):
    match = re.fullmatch(r"Keep this under (\d+) characters\.", msg)
    if match:
        return gettext("Keep this under {limit} characters.", limit=match.group(1))
    return gettext(msg)


def translate_errors(errors):
    return {field: _translate_message(msg) for field, msg in (errors or {}).items()}


def js_strings():
    return {key: gettext(key) for key in JS_KEYS}


def _parse(value):
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _month(d):
    return (MONTHS_HI if current_lang() == "hi" else MONTHS_EN)[d.month - 1]


def nice_date(value):
    d = _parse(value)
    return f"{d.day:02d} {_month(d)} {d.year}" if d else (value or "")


def short_date(value):
    d = _parse(value)
    return f"{d.day:02d} {_month(d)}" if d else (value or "")


def init_app(app):
    app.jinja_env.globals["_"] = gettext
    app.jinja_env.globals["_n"] = ngettext
    app.jinja_env.filters["nice_date"] = nice_date
    app.jinja_env.filters["short_date"] = short_date

    @app.context_processor
    def inject_language():
        def lang_url(code):
            nxt = request.full_path.rstrip("?")
            return "/lang/" + code + "?" + urlencode({"next": nxt})
        return {"lang": current_lang(), "langs": LANGS, "lang_url": lang_url}
