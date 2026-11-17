"""Voice-to-CRM application factory."""
import logging
import time
from pathlib import Path

import click
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash

from config import Config


def create_app(overrides=None):
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)

    from app import db, i18n, security
    db.init_app(app)
    security.init_app(app)
    i18n.init_app(app)

    from app.blueprints import api, auth, logs, main
    for module in (auth, main, logs, api):
        app.register_blueprint(module.bp)

    _register_template_helpers(app)
    _register_error_handlers(app)
    _register_cli(app)

    # Create tables on start so the app works right after install.
    if not app.config.get("TESTING"):
        try:
            db.init_schema(app.config)
        except db.DatabaseError as exc:
            raise SystemExit(f"\nDatabase setup failed:\n  {exc}\n")
    return app


# ---------------------------------------------------------------------------

def _register_template_helpers(app):
    @app.template_filter("status_class")
    def status_class(status):
        return {
            "Completed": "done",
            "Follow-up Required": "followup",
            "Pending": "pending",
        }.get(status, "pending")

    @app.context_processor
    def inject_globals():
        return {"app_name": "Voice-to-CRM"}


def _register_error_handlers(app):
    def wants_json():
        return request.path.startswith("/api/")

    @app.errorhandler(HTTPException)
    def handle_http(error):
        from app.i18n import gettext as _
        message = _(error.description)
        if error.code == 413:
            limit = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
            message = _("That file is too large. The limit is {limit} MB.", limit=limit)
        if wants_json():
            return jsonify(success=False, message=message), error.code
        return render_template("errors/error.html", code=error.code, title=error.name,
                               message=message), error.code

    @app.errorhandler(Exception)
    def handle_unexpected(error):
        app.logger.exception("Unhandled error: %s", error)
        from app.i18n import gettext as _
        message = _("Something went wrong on the server. Please try again.")
        if wants_json():
            return jsonify(success=False, message=message), 500
        return render_template("errors/error.html", code=500, title="Server error",
                               message=message), 500


def _register_cli(app):
    @app.cli.command("create-admin")
    @click.option("--name", prompt=True)
    @click.option("--email", prompt=True)
    @click.password_option()
    def create_admin(name, email, password):
        """Create an admin user, or promote and reset an existing one."""
        from app.repositories import users
        email = email.strip().lower()
        existing = users.get_by_email(email)
        hashed = generate_password_hash(password)
        if existing:
            users.set_password_and_role(existing["id"], hashed, "admin")
            click.echo(f"Updated {email}: now an admin with the new password.")
        else:
            users.create(name.strip(), email, hashed, "admin")
            click.echo(f"Admin {email} created.")

    @app.cli.command("cleanup-uploads")
    @click.option("--hours", default=24, show_default=True,
                  help="Only delete unattached recordings older than this.")
    def cleanup_uploads(hours):
        """Delete recordings that were never saved into a log."""
        from app.repositories import logs
        keep = logs.referenced_audio_files()
        cutoff = time.time() - hours * 3600
        removed = 0
        for path in Path(app.config["UPLOAD_DIR"]).glob("*/*"):
            if path.is_file() and path.name not in keep and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        click.echo(f"Removed {removed} unused recording(s).")

    @app.cli.command("send-reminders")
    @click.option("--force", is_flag=True, help="Send even if a reminder already went out today.")
    @click.option("--days", type=int, default=None,
                  help="Also include follow-ups due within this many days (default: REMINDER_DAYS_AHEAD).")
    def send_reminders(force, days):
        """Email each user their due and overdue follow-ups (run once a day)."""
        from app.services import mailer, reminders
        if not mailer.smtp_configured():
            raise click.ClickException("Email is not set up. Add the SMTP_* values to your .env file.")
        result = reminders.run_all(days_ahead=days, force=force)
        click.echo(f"Sent {result['sent']}, skipped {result['skipped']} (already sent today), "
                   f"failed {result['failed']}.")
        for line in result["errors"]:
            click.echo(f"  ! {line}")
