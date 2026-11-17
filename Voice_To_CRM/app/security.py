"""Login handling, CSRF protection and security headers."""
import hmac
import secrets
from functools import wraps
from urllib.parse import urlparse

from flask import abort, current_app, g, jsonify, redirect, request, session, url_for

from app.repositories import users as users_repo

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

def csrf_token():
    """Return the CSRF token for this session (creating one if needed)."""
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


def _check_csrf():
    if request.method in SAFE_METHODS:
        return
    sent = request.headers.get("X-CSRFToken") or request.form.get("csrf_token") or ""
    expected = session.get("_csrf", "")
    if not expected or not hmac.compare_digest(sent, expected):
        if request.path.startswith("/api/"):
            abort(400, description="Your session expired. Reload the page and try again.")
        abort(400, description="Your session expired. Go back, reload the page and try again.")


# ---------------------------------------------------------------------------
# Current user / login_required
# ---------------------------------------------------------------------------

def _load_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id:
        g.user = users_repo.get_by_id(user_id)
        if g.user is None:  # account was deleted
            session.pop("user_id", None)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            if request.path.startswith("/api/"):
                return jsonify(success=False, message="Please sign in first."), 401
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    """Signed-in administrators only (everyone else gets a 403 page)."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user.get("role") != "admin":
            abort(403, description="Only administrators can open this page.")
        return view(*args, **kwargs)
    return wrapped


def safe_next_url(target, default):
    """Only allow redirects to paths inside this site."""
    if not target:
        return default
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/") or target.startswith("//"):
        return default
    return target


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def init_app(app):
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def before_request():
        if request.endpoint == "static":
            return
        _load_user()
        _check_csrf()

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "microphone=(self)")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        if request.endpoint != "static" and request.method == "GET":
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp
