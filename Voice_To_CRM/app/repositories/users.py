"""Queries for the users table."""
from app.db import get_db


def get_by_id(user_id):
    return get_db().one(
        "SELECT id, name, email, role, created_at FROM users WHERE id = %s", (user_id,)
    )


def get_by_email(email):
    return get_db().one("SELECT * FROM users WHERE email = %s", (email.lower(),))


def count():
    return int(get_db().scalar("SELECT COUNT(*) FROM users"))


def create(name, email, password_hash, role="user"):
    db = get_db()
    result = db.execute(
        "INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, %s)",
        (name, email.lower(), password_hash, role),
    )
    db.commit()
    return result.lastrowid


def set_password_and_role(user_id, password_hash, role):
    db = get_db()
    db.execute(
        "UPDATE users SET password_hash = %s, role = %s WHERE id = %s",
        (password_hash, role, user_id),
    )
    db.commit()


_STATS_SQL = """
    SELECT u.id, u.name, u.email, u.role, u.created_at,
      (SELECT COUNT(*) FROM field_logs l WHERE l.user_id = u.id) AS visits,
      (SELECT COUNT(DISTINCT LOWER(l.customer_name)) FROM field_logs l WHERE l.user_id = u.id) AS customers,
      (SELECT COUNT(*) FROM field_logs l WHERE l.user_id = u.id AND l.status = 'Follow-up Required') AS open_followups,
      (SELECT COUNT(*) FROM field_logs l WHERE l.user_id = u.id AND l.status = 'Completed') AS completed,
      (SELECT MAX(l.visit_date) FROM field_logs l WHERE l.user_id = u.id) AS last_visit,
      (SELECT COUNT(*) FROM log_media m JOIN field_logs l ON l.id = m.log_id
        WHERE l.user_id = u.id) AS recordings
    FROM users u
"""


def stats_for(user_id):
    """One user with their visit totals, or None."""
    return get_db().one(_STATS_SQL + " WHERE u.id = %s", (user_id,))


def list_with_stats(q=""):
    """Every user with visit totals (administrators first)."""
    q = (q or "").strip()
    where, params = "", []
    if q:
        like = "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        where = " WHERE (u.name LIKE %s ESCAPE '!' OR u.email LIKE %s ESCAPE '!')"
        params = [like, like]
    return get_db().all(
        _STATS_SQL + where + " ORDER BY CASE WHEN u.role = 'admin' THEN 0 ELSE 1 END, u.name, u.id",
        params)
