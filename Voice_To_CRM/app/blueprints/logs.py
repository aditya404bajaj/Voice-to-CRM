"""List, view, edit, delete and export visit logs."""
import csv
import io
from datetime import date

from flask import (Blueprint, Response, abort, current_app, flash, g, jsonify,
                   redirect, render_template, request, send_file, url_for)

from app.blueprints.api import (UploadError, audio_path, delete_recording, is_video_name,
                                store_upload)
from app.i18n import gettext as _, translate_errors
from app.repositories import logs as logs_repo, users as users_repo
from app.security import login_required
from app.validation import STATUSES, clean_log, normalize_status, parse_date

bp = Blueprint("logs", __name__)

VIDEO_MIME = {"webm": "video/webm", "mp4": "video/mp4"}
AUDIO_MIME = {
    "webm": "audio/webm", "ogg": "audio/ogg", "oga": "audio/ogg", "mp3": "audio/mpeg",
    "mpeg": "audio/mpeg", "mpga": "audio/mpeg", "mp4": "audio/mp4", "m4a": "audio/mp4",
    "wav": "audio/wav", "flac": "audio/flac",
}


def _filters():
    status = normalize_status(request.args.get("status", "")) or ""
    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()
    return {
        "q": request.args.get("q", "").strip()[:100],
        "status": status,
        "date_from": date_from if parse_date(date_from) else "",
        "date_to": date_to if parse_date(date_to) else "",
        "customer": request.args.get("customer", "").strip()[:150],
        "user_id": int(raw_user) if (raw_user := request.args.get("user", "").strip()).isdigit() else None,
    }


def _page_number():
    try:
        return max(1, int(request.args.get("page", 1)))
    except ValueError:
        return 1


def _get_or_404(log_id):
    log = logs_repo.get_log(g.user, log_id)
    if log is None:
        abort(404)
    return log


@bp.get("/logs")
@login_required
def index():
    filters = _filters()
    per_page = current_app.config["PER_PAGE"]
    page = _page_number()
    rows, total = logs_repo.list_logs(g.user, page=page, per_page=per_page, **filters)
    pages = max(1, -(-total // per_page))
    if page > pages:
        return redirect(url_for("logs.index", **{**request.args, "page": pages}))
    query = {k: v for k, v in request.args.items() if k != "page" and v}
    filter_user = None
    if filters["user_id"] and g.user.get("role") == "admin":
        filter_user = users_repo.get_by_id(filters["user_id"])
    return render_template(
        "logs.html", filter_user=filter_user, logs=rows, total=total, page=page, pages=pages, per_page=per_page,
        filters=filters, statuses=STATUSES, query=query,
        today=date.today().isoformat(),
    )


@bp.get("/logs/<int:log_id>")
@login_required
def detail(log_id):
    return render_template("log_detail.html", log=_get_or_404(log_id),
                           today=date.today().isoformat())


@bp.route("/logs/<int:log_id>/edit", methods=["GET", "POST"])
@login_required
def edit(log_id):
    log = _get_or_404(log_id)
    if request.method == "POST":
        values, errors = clean_log(request.form)
        if errors:
            return render_template("log_edit.html", log=log, values=values,
                                   errors=translate_errors(errors), statuses=STATUSES), 422
        logs_repo.update_log(g.user, log_id, values)
        flash(_("Changes saved."), "success")
        return redirect(url_for("logs.detail", log_id=log_id))
    return render_template("log_edit.html", log=log, values=log, errors={}, statuses=STATUSES)


@bp.post("/logs/<int:log_id>/delete")
@login_required
def delete(log_id):
    log = logs_repo.delete_log(g.user, log_id)
    if log is None:
        abort(404)
    for item in log.get("media", []):
        delete_recording(log["user_id"], item["filename"])
    flash(_("Visit log deleted."), "success")
    return redirect(url_for("logs.index"))


REPORT_LIMIT = 300


@bp.get("/logs/<int:log_id>/report")
@login_required
def report_one(log_id):
    log = _get_or_404(log_id)
    return render_template(
        "report.html", logs=[log], single=True, truncated=False,
        subtitle=f"{log['customer_name']} · {log['location']}",
        back_url=url_for("logs.detail", log_id=log_id),
        generated=date.today().isoformat(), today=date.today().isoformat(),
    )


@bp.get("/logs/report")
@login_required
def report_list():
    filters = _filters()
    rows = logs_repo.export_rows(g.user, limit=REPORT_LIMIT + 1, **filters)
    truncated = len(rows) > REPORT_LIMIT
    rows = rows[:REPORT_LIMIT]
    parts = []
    if filters["customer"]:
        parts.append(filters["customer"])
    if filters["q"]:
        parts.append(f'"{filters["q"]}"')
    if filters["status"]:
        parts.append(_(filters["status"]))
    if filters["date_from"] or filters["date_to"]:
        parts.append(f"{filters['date_from'] or '…'} → {filters['date_to'] or '…'}")
    back = (url_for("main.customer_detail", name=filters["customer"]) if filters["customer"]
            else url_for("logs.index", **{k: v for k, v in request.args.items() if v and k != "page"}))
    return render_template(
        "report.html", logs=rows, single=False, truncated=truncated, limit=REPORT_LIMIT,
        subtitle=" · ".join(parts) or _("All visit logs"), back_url=back,
        generated=date.today().isoformat(), today=date.today().isoformat(),
    )


def _wants_json():
    return request.headers.get("X-Requested-With") == "fetch"


@bp.post("/logs/<int:log_id>/media")
@login_required
def attach_media(log_id):
    """Add one or more recordings (audio or video) to a log. Existing ones are kept."""
    log = _get_or_404(log_id)
    files = [f for f in request.files.getlist("media") if f and f.filename]
    added, error = 0, None
    if not files:
        error = _("No recording was received. Record again or choose a file.")
    for file in files:
        is_video = (file.mimetype or "").startswith("video/")
        try:
            name, _path = store_upload(file, is_video, log["user_id"])
        except UploadError as exc:
            error = error or str(exc)
            continue
        logs_repo.add_media(g.user, log_id, name, "video" if is_video else "audio")
        added += 1

    if _wants_json():
        status = 200 if added else 400
        return jsonify(success=bool(added), added=added, message=error or ""), status
    if added:
        flash(_("Recording added.") if added == 1 else _("{n} recordings added.", n=added), "success")
    if error:
        flash(error, "error")
    return redirect(url_for("logs.detail", log_id=log_id))


@bp.post("/logs/<int:log_id>/media/<int:media_id>/delete")
@login_required
def remove_media(log_id, media_id):
    log = _get_or_404(log_id)
    row = logs_repo.delete_media(g.user, log_id, media_id)
    if row is None:
        abort(404)
    delete_recording(log["user_id"], row["filename"])
    flash(_("Recording removed."), "success")
    return redirect(url_for("logs.detail", log_id=log_id))


def _send_recording(log, row):
    path = audio_path(log["user_id"], row["filename"])
    if path is None or not path.is_file():
        abort(404)
    ext = path.suffix.lstrip(".").lower()
    if is_video_name(path.name):
        mimetype = VIDEO_MIME.get(ext, "video/mp4")
    else:
        mimetype = AUDIO_MIME.get(ext, "application/octet-stream")
    return send_file(path, mimetype=mimetype, conditional=True)


@bp.get("/logs/<int:log_id>/media/<int:media_id>")
@login_required
def media_file(log_id, media_id):
    log = _get_or_404(log_id)
    row = next((m for m in log["media"] if m["id"] == media_id), None)
    if row is None:
        abort(404)
    return _send_recording(log, row)


@bp.get("/logs/<int:log_id>/audio")
@login_required
def audio(log_id):
    """Older link: plays the first recording of the log."""
    log = _get_or_404(log_id)
    if not log["media"]:
        abort(404)
    return _send_recording(log, log["media"][0])


def _csv_safe(value):
    """Stop spreadsheet apps from running cell values that start with = + - @."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@bp.get("/logs/export.csv")
@login_required
def export_csv():
    rows = logs_repo.export_rows(g.user, **_filters())
    out = io.StringIO()
    out.write("\ufeff")  # BOM so Excel opens UTF-8 (Hindi etc.) correctly
    writer = csv.writer(out)
    writer.writerow([
        "ID", "Customer", "Location", "Contact person", "Visit date", "Observations",
        "Issues", "Follow-up", "Follow-up date", "Status", "Logged by", "Transcript",
    ])
    for r in rows:
        writer.writerow([_csv_safe(v) for v in (
            r["id"], r["customer_name"], r["location"], r["contact_person"], r["visit_date"],
            r["observations"], r["issues"], r["follow_up"], r["follow_up_date"], r["status"],
            r["owner_name"], r["transcript"],
        )])
    filename = f"visit_logs_{date.today().isoformat()}.csv"
    return Response(out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
