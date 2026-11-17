"""Daily follow-up reminder emails."""
from datetime import date
from html import escape

from flask import current_app

from app.repositories import reminders as repo
from app.services import mailer


def _label(item, today):
    days = (date.fromisoformat(item["follow_up_date"]) - today).days
    if days < 0:
        return f"OVERDUE by {-days} day{'s' if days != -1 else ''}"
    if days == 0:
        return "Due today"
    if days == 1:
        return "Due tomorrow"
    return f"Due in {days} days"


def build_digest(user, items, today):
    """Return (subject, plain_text, html) for one user's follow-ups."""
    overdue = sum(1 for i in items if i["follow_up_date"] < today.isoformat())
    subject = f"Follow-up reminder: {len(items)} to do" + (f" ({overdue} overdue)" if overdue else "")
    lines = [f"Hello {user['name'].split()[0]},", "",
             "These customer follow-ups need your attention:", ""]
    rows = []
    for i in items:
        label = _label(i, today)
        action = i["follow_up"] or "Follow up"
        where = i["location"] + (f", contact: {i['contact_person']}" if i["contact_person"] else "")
        lines += [f"- {i['customer_name']} ({where})", f"  {label} - {i['follow_up_date']}", f"  {action}", ""]
        color = "#b42318" if label.startswith("OVERDUE") else "#44505f"
        rows.append(
            f"<li style='margin-bottom:12px'><strong>{escape(i['customer_name'])}</strong> "
            f"<span style='color:#5a6963'>({escape(where)})</span><br>"
            f"<span style='color:{color};font-weight:600'>{escape(label)} &middot; {escape(i['follow_up_date'])}</span><br>"
            f"{escape(action)}</li>")
    lines.append("Sent by Voice-to-CRM.")
    html = (f"<p>Hello {escape(user['name'].split()[0])},</p>"
            "<p>These customer follow-ups need your attention:</p>"
            f"<ul style='padding-left:18px'>{''.join(rows)}</ul>"
            "<p style='color:#5a6963'>Sent by Voice-to-CRM.</p>")
    return subject, "\n".join(lines), html


def send_digest(user, today=None, days_ahead=None):
    """Email one user their due follow-ups. Returns how many items were sent."""
    today = today or date.today()
    days = current_app.config["REMINDER_DAYS_AHEAD"] if days_ahead is None else days_ahead
    items = repo.due_followups(user["id"], today, days)
    if not items:
        return 0
    subject, text, html = build_digest(user, items, today)
    mailer.send_email(user["email"], subject, text, html)
    return len(items)


def run_all(today=None, days_ahead=None, force=False):
    """Send a digest to every user who has something due. Once per user per day."""
    today = today or date.today()
    days = current_app.config["REMINDER_DAYS_AHEAD"] if days_ahead is None else days_ahead
    result = {"sent": 0, "skipped": 0, "failed": 0, "errors": []}
    for user in repo.users_with_due(today, days):
        if not force and repo.already_sent(user["id"], today):
            result["skipped"] += 1
            continue
        try:
            send_digest(user, today, days)
        except mailer.MailError as exc:
            result["failed"] += 1
            result["errors"].append(f"{user['email']}: {exc}")
            continue
        repo.mark_sent(user["id"], today)
        result["sent"] += 1
    return result
