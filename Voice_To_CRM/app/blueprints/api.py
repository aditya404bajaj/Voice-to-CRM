"""JSON endpoints used by the recording page."""
import re
import uuid
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request

from app.i18n import gettext as _, translate_errors
from app.repositories import logs as logs_repo
from app.security import login_required
from app.services import ai
from app.validation import clean_log

bp = Blueprint("api", __name__, url_prefix="/api")

ALLOWED_AUDIO = {"webm", "ogg", "oga", "mp3", "mp4", "m4a", "wav", "mpeg", "mpga", "flac"}
ALLOWED_VIDEO = {"mp4", "webm"}  # formats Whisper can read the sound track from
MIME_TO_EXT = {
    "audio/webm": "webm", "video/webm": "webm", "audio/ogg": "ogg", "audio/mpeg": "mp3",
    "audio/mp4": "mp4", "audio/x-m4a": "m4a", "audio/wav": "wav", "audio/x-wav": "wav",
    "audio/flac": "flac", "video/mp4": "mp4",
}
# Recordings are stored as <uuid>.<ext>; videos get a "v_" prefix so no database change is needed.
AUDIO_NAME_RE = re.compile(r"^(?:v_)?[0-9a-f]{32}\.[a-z0-9]{2,4}$")


def upload_root():
    return Path(current_app.config["UPLOAD_DIR"])


def user_audio_dir(user_id):
    return upload_root() / str(user_id)


def audio_path(user_id, filename):
    """Path of a stored recording, or None if the name is not one we created."""
    if not filename or not AUDIO_NAME_RE.match(filename):
        return None
    return user_audio_dir(user_id) / filename


def is_video_name(filename):
    return bool(filename) and filename.startswith("v_")


def _fail(message, status=400, **extra):
    return jsonify(success=False, message=message, **extra), status


@bp.get("/status")
@login_required
def status():
    return jsonify(success=True, ai_configured=ai.ai_configured())


class UploadError(Exception):
    """The uploaded recording was rejected. The message is safe to show (already translated)."""


def store_upload(file, is_video, owner_id):
    """Validate an uploaded recording and save it under the owner's folder.

    Returns (filename, path). Raises UploadError with a readable message.
    """
    if not file or not file.filename:
        raise UploadError(_("No recording was received. Record again or choose a file."))

    allowed = ALLOWED_VIDEO if is_video else ALLOWED_AUDIO
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in allowed:
        ext = MIME_TO_EXT.get((file.mimetype or "").split(";")[0].lower(), "")
    if ext not in allowed:
        if is_video:
            raise UploadError(_("This video format is not supported. Use mp4 or webm."))
        raise UploadError(_("This audio format is not supported. Use webm, ogg, mp3, m4a, wav or flac."))

    folder = user_audio_dir(owner_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{'v_' if is_video else ''}{uuid.uuid4().hex}.{ext}"
    path = folder / name
    file.save(path)
    if path.stat().st_size < 200:
        path.unlink(missing_ok=True)
        raise UploadError(_("The recording is empty. Check your microphone or camera and try again."))
    return name, path


def delete_recording(owner_id, filename):
    path = audio_path(owner_id, filename)
    if path is not None:
        path.unlink(missing_ok=True)


@bp.post("/transcribe")
@login_required
def transcribe():
    video = request.files.get("video")
    is_video = bool(video and video.filename)
    try:
        name, path = store_upload(video if is_video else request.files.get("audio"), is_video, g.user["id"])
    except UploadError as exc:
        return _fail(str(exc))

    if not ai.ai_configured():
        return jsonify(
            success=True, filename=name, kind="video" if is_video else "audio", transcript="", ai_configured=False,
            message=_("Recording saved. Speech-to-text needs OPENAI_API_KEY, so type the transcript below (or use the browser's live dictation)."),
        )

    try:
        text = ai.transcribe_audio(path)
    except ai.AIError as exc:
        return _fail(str(exc), 502, filename=name)

    if not text:
        return jsonify(success=True, filename=name, transcript="", ai_configured=True,
                       message=_("No speech was detected in the recording."))
    return jsonify(success=True, filename=name, kind="video" if is_video else "audio",
                   transcript=text, ai_configured=True)


@bp.post("/extract")
@login_required
def extract():
    payload = request.get_json(silent=True) or {}
    transcript = str(payload.get("transcript", "")).strip()
    if not transcript:
        return _fail(_("The transcript is empty. Record something or type the notes first."))
    if len(transcript) > 30000:
        return _fail(_("The transcript is too long (limit 30,000 characters)."))
    fields, method, warning = ai.extract_fields(transcript)
    return jsonify(success=True, fields=fields, method=method, warning=warning)


@bp.post("/logs")
@login_required
def create_log():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _fail(_("Send the log as JSON."))
    values, errors = clean_log(payload)
    if errors:
        return _fail(_("Please fix the highlighted fields."), 422, errors=translate_errors(errors))

    log_id = logs_repo.create_log(g.user, values, _owned_recordings(payload))
    return jsonify(success=True, id=log_id, redirect=f"/logs/{log_id}"), 201


def _owned_recordings(payload):
    """Recordings named in the payload that really exist in this user's folder.

    Accepts `media` (a list of filenames; any number) and the older single
    `audio_file`. Unknown names, duplicates and path tricks are ignored.
    """
    names = payload.get("media")
    names = list(names) if isinstance(names, list) else []
    if payload.get("audio_file"):
        names.insert(0, payload["audio_file"])
    found, seen = [], set()
    for name in names:
        if not isinstance(name, str) or name in seen:
            continue
        seen.add(name)
        path = audio_path(g.user["id"], name)
        if path is not None and path.is_file():
            found.append((name, "video" if is_video_name(name) else "audio"))
    return found


@bp.post("/media/discard")
@login_required
def discard_media():
    """Delete a recording that was uploaded on the record page but not saved to a log yet."""
    payload = request.get_json(silent=True) or {}
    name = payload.get("filename")
    if isinstance(name, str) and name not in logs_repo.referenced_audio_files():
        delete_recording(g.user["id"], name)
    return jsonify(success=True)
