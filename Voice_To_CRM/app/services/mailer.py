"""Send email over SMTP (Gmail, Outlook, your own server...)."""
import smtplib
import ssl
from email.message import EmailMessage

from flask import current_app


class MailError(Exception):
    """Sending failed. The message is safe to show to the user."""


def smtp_configured():
    cfg = current_app.config
    return bool(cfg.get("SMTP_HOST") and cfg.get("SMTP_FROM"))


def send_email(to, subject, text, html=None):
    cfg = current_app.config
    if not smtp_configured():
        raise MailError("Email is not set up. Add the SMTP_* values to your .env file.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["SMTP_FROM"]
    msg["To"] = to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")

    security = cfg.get("SMTP_SECURITY", "starttls")
    context = ssl.create_default_context()
    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=20, context=context)
        else:
            server = smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=20)
        with server:
            if security == "starttls":
                server.starttls(context=context)
            if cfg.get("SMTP_USER"):
                server.login(cfg["SMTP_USER"], cfg.get("SMTP_PASSWORD") or "")
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError("The email server rejected the username or password. "
                        "For Gmail use an App Password.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"Could not send the email ({exc}). Check the SMTP_* values in .env.") from exc
