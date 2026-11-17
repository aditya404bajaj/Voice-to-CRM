"""Queries used by email reminders."""
from datetime import timedelta

from app.db import get_db


def users_with_due(today, days_ahead):
    horizon = (today + timedelta(days=days_ahead)).isoformat()
    return get_db().all(
        """SELECT DISTINCT u.id, u.name, u.email
           FROM users u JOIN field_logs l ON l.user_id = u.id
           WHERE l.follow_up_date IS NOT NULL AND l.status <> 'Completed'
             AND l.follow_up_date <= %s
           ORDER BY u.id""",
        (horizon,),
    )


def due_followups(user_id, today, days_ahead):
    horizon = (today + timedelta(days=days_ahead)).isoformat()
    return get_db().all(
        """SELECT id, customer_name, location, contact_person, follow_up, follow_up_date
           FROM field_logs
           WHERE user_id = %s AND follow_up_date IS NOT NULL
             AND status <> 'Completed' AND follow_up_date <= %s
           ORDER BY follow_up_date ASC, id ASC""",
        (user_id, horizon),
    )


def already_sent(user_id, day):
    return get_db().one(
        "SELECT 1 AS x FROM reminder_log WHERE user_id = %s AND sent_on = %s",
        (user_id, day.isoformat()),
    ) is not None


def mark_sent(user_id, day):
    db = get_db()
    db.execute("DELETE FROM reminder_log WHERE user_id = %s AND sent_on = %s",
               (user_id, day.isoformat()))
    db.execute("INSERT INTO reminder_log (user_id, sent_on) VALUES (%s, %s)",
               (user_id, day.isoformat()))
    db.commit()
