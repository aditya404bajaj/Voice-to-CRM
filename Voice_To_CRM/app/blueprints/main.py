"""Dashboard, recording page, customers, language and reminders."""
from datetime import date

from flask import (Blueprint, abort, flash, g, redirect, render_template, request,
                   session, url_for)

from app import i18n
from app.i18n import gettext as _
from app.repositories import logs as logs_repo, users as users_repo
from app.security import admin_required, login_required, safe_next_url
from app.services import ai, mailer, reminders
from app.validation import STATUSES

bp = Blueprint("main", __name__)


@bp.get("/")
def home():
    return redirect(url_for("main.dashboard" if g.user else "auth.login"))


@bp.get("/lang/<code>")
def set_language(code):
    if code in i18n.LANGS:
        session["lang"] = code
    return redirect(safe_next_url(request.args.get("next"), url_for("main.home")))


@bp.get("/dashboard")
@login_required
def dashboard():
    today = date.today()
    return render_template(
        "dashboard.html", stats=logs_repo.dashboard_stats(g.user, today), today=today.isoformat(),
        email_ready=mailer.smtp_configured(),
    )


@bp.get("/record")
@login_required
def record():
    values = {
        "visit_date": date.today().isoformat(), "status": "Pending",
        "customer_name": request.args.get("customer", "").strip()[:150],
        "location": request.args.get("location", "").strip()[:200],
        "contact_person": request.args.get("contact", "").strip()[:150],
    }
    return render_template(
        "record.html", statuses=STATUSES, ai_configured=ai.ai_configured(),
        today=values["visit_date"], values=values, js_strings=i18n.js_strings(),
    )


@bp.get("/account")
@login_required
def account():
    """The signed-in person's own details and totals."""
    me = users_repo.stats_for(g.user["id"])
    recent, _total = logs_repo.list_logs(g.user, per_page=5, user_id=g.user["id"])
    return render_template("account.html", u=me, stats=me, recent=recent, mine=True,
                           all_url=url_for("logs.index", user=g.user["id"]))


@bp.get("/users")
@admin_required
def users():
    q = request.args.get("q", "").strip()[:100]
    rows = users_repo.list_with_stats(q)
    return render_template("users.html", people=rows, q=q)


@bp.get("/users/<int:user_id>")
@admin_required
def user_detail(user_id):
    person = users_repo.stats_for(user_id)
    if person is None:
        abort(404)
    recent, _total = logs_repo.list_logs(g.user, per_page=10, user_id=user_id)
    return render_template("user_detail.html", u=person, stats=person, recent=recent, mine=False,
                           all_url=url_for("logs.index", user=user_id))


@bp.get("/customers")
@login_required
def customers():
    q = request.args.get("q", "").strip()[:100]
    return render_template("customers.html", customers=logs_repo.customers_summary(g.user, q), q=q)


@bp.get("/customer")
@login_required
def customer_detail():
    name = request.args.get("name", "").strip()[:150]
    data = logs_repo.customer_detail(g.user, name) if name else None
    if data is None:
        abort(404)
    return render_template("customer_detail.html", c=data, today=date.today().isoformat())


@bp.post("/reminders/send")
@login_required
def send_my_reminders():
    """Email the signed-in user their own due follow-ups right now."""
    if not mailer.smtp_configured():
        flash(_("Email is not set up. Add the SMTP_* values to your .env file."), "error")
    else:
        try:
            count = reminders.send_digest(g.user)
            if count:
                flash(_("Reminder sent to {email} with {n} follow-up(s).", email=g.user["email"], n=count),
                      "success")
            else:
                flash(_("Nothing is due, so no email was sent."), "info")
        except mailer.MailError as exc:
            flash(str(exc), "error")
    return redirect(url_for("main.dashboard"))
