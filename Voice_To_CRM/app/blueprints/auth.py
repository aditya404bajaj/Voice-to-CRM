"""Sign in, register, sign out."""
import hmac
import re
import time

from flask import (Blueprint, current_app, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from app.i18n import gettext as _
from app.repositories import users as users_repo
from app.security import safe_next_url

bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Very small in-memory brake against password guessing (per process).
_FAILED = {}
MAX_ATTEMPTS = 5
WINDOW_SECONDS = 600


def _throttle_key(email):
    return f"{request.remote_addr}|{email}"


def _is_locked(key):
    now = time.time()
    attempts = [t for t in _FAILED.get(key, []) if now - t < WINDOW_SECONDS]
    _FAILED[key] = attempts
    return len(attempts) >= MAX_ATTEMPTS


def _reset_session(**keep):
    """Clear the session but keep the chosen language."""
    lang = session.get("lang")
    session.clear()
    if lang:
        session["lang"] = lang
    for key, value in keep.items():
        session[key] = value


def _start_session(user):
    _reset_session(user_id=user["id"])
    session.permanent = True


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))

    next_url = request.args.get("next") or request.form.get("next") or ""
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        key = _throttle_key(email)
        if _is_locked(key):
            error = _("Too many failed attempts. Wait 10 minutes and try again.")
        else:
            user = users_repo.get_by_email(email) if email else None
            if user and check_password_hash(user["password_hash"], password):
                _FAILED.pop(key, None)
                _start_session(user)
                return redirect(safe_next_url(next_url, url_for("main.dashboard")))
            _FAILED.setdefault(key, []).append(time.time())
            error = _("Email or password is incorrect.")
    return render_template("auth/login.html", error=error, next_url=next_url)


def _admin_code_matches(code):
    secret = current_app.config.get("ADMIN_SIGNUP_CODE")
    return bool(secret) and hmac.compare_digest(code.encode("utf-8"), secret.encode("utf-8"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("main.dashboard"))
    allowed = current_app.config["ALLOW_REGISTRATION"] or users_repo.count() == 0
    if not allowed:
        return render_template("auth/register.html", closed=True, form={}, error=None, first=False, admin_signup=False), 403

    first = users_repo.count() == 0            # the very first account is always the administrator
    admin_signup = bool(current_app.config.get("ADMIN_SIGNUP_CODE"))
    form, error = {"role": "user"}, None
    if request.method == "POST":
        form = {
            "name": request.form.get("name", "").strip(),
            "email": request.form.get("email", "").strip().lower(),
            "role": "admin" if request.form.get("role") == "admin" else "user",
        }
        admin_code = request.form.get("admin_code", "")
        code_key = f"{request.remote_addr}|admin-code"
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if not form["name"] or not form["email"] or not password:
            error = _("Fill in your name, email and password.")
        elif len(form["name"]) > 100:
            error = _("Name must be 100 characters or fewer.")
        elif not EMAIL_RE.match(form["email"]) or len(form["email"]) > 150:
            error = _("Enter a valid email address.")
        elif len(password) < 8:
            error = _("Use at least 8 characters for the password.")
        elif password != confirm:
            error = _("The two passwords do not match.")
        elif users_repo.get_by_email(form["email"]):
            error = _("An account with this email already exists. Sign in instead.")
        elif not first and form["role"] == "admin" and not admin_signup:
            error = _("Administrator sign-up is turned off. Choose User, or ask an administrator.")
        elif not first and form["role"] == "admin" and _is_locked(code_key):
            error = _("Too many wrong admin codes. Wait 10 minutes and try again.")
        elif not first and form["role"] == "admin" and not _admin_code_matches(admin_code):
            _FAILED.setdefault(code_key, []).append(time.time())
            error = _("The admin code is wrong.")
        else:
            role = "admin" if first or form["role"] == "admin" else "user"
            users_repo.create(form["name"], form["email"], generate_password_hash(password), role)
            _FAILED.pop(code_key, None)
            flash(_("Account created. Sign in to continue."), "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/register.html", form=form, error=error, closed=False,
                           first=first, admin_signup=admin_signup)


@bp.route("/logout", methods=["POST"])
def logout():
    _reset_session()
    flash(_("You have been signed out."), "info")
    return redirect(url_for("auth.login"))
