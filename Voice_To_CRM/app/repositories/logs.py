"""Queries for field logs (visit logs).

Every function takes the logged-in `user` dict. Admins see everybody's logs;
everyone else only sees their own.
"""
from datetime import date, timedelta

from app.db import get_db

LOG_FIELDS = [
    "customer_name", "location", "contact_person", "visit_date",
    "observations", "issues", "follow_up", "follow_up_date",
    "status", "transcript",
]

_OWNER_JOIN = "FROM field_logs l JOIN users u ON u.id = l.user_id"


def _is_admin(user):
    return user.get("role") == "admin"


def _escape_like(text):
    """Escape LIKE wildcards. '!' is the escape char (works on MySQL and SQLite)."""
    return text.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _scope(user):
    if _is_admin(user):
        return ["1=1"], []
    return ["l.user_id = %s"], [user["id"]]


def _nullable(value):
    return value if value not in ("", None) else None


# ---------------------------------------------------------------------------
# Listing / searching
# ---------------------------------------------------------------------------

def _where(user, q="", status="", date_from="", date_to="", customer="", user_id=None):
    clauses, params = _scope(user)
    q = (q or "").strip()
    if q:
        like = f"%{_escape_like(q)}%"
        clauses.append(
            "(l.customer_name LIKE %s ESCAPE '!' OR l.location LIKE %s ESCAPE '!' "
            "OR l.contact_person LIKE %s ESCAPE '!')"
        )
        params += [like, like, like]
    customer = (customer or "").strip()
    if customer:
        clauses.append("LOWER(l.customer_name) = LOWER(%s)")
        params.append(customer)
    if user_id:  # admins can narrow to one person; everyone else is already limited to their own
        clauses.append("l.user_id = %s")
        params.append(int(user_id))
    if status:
        clauses.append("l.status = %s")
        params.append(status)
    if date_from:
        clauses.append("l.visit_date >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("l.visit_date <= %s")
        params.append(date_to)
    return " AND ".join(clauses), params


def list_logs(user, q="", status="", date_from="", date_to="", customer="", page=1, per_page=15,
              user_id=None):
    where, params = _where(user, q, status, date_from, date_to, customer, user_id)
    db = get_db()
    total = int(db.scalar(f"SELECT COUNT(*) {_OWNER_JOIN} WHERE {where}", params))
    page = max(1, page)
    rows = db.all(
        f"""SELECT l.*, u.name AS owner_name {_OWNER_JOIN}
            WHERE {where}
            ORDER BY l.visit_date DESC, l.id DESC
            LIMIT %s OFFSET %s""",
        params + [per_page, (page - 1) * per_page],
    )
    return rows, total


def export_rows(user, q="", status="", date_from="", date_to="", customer="", limit=None, user_id=None):
    where, params = _where(user, q, status, date_from, date_to, customer, user_id)
    limit_sql = f" LIMIT {int(limit)}" if limit else ""
    return get_db().all(
        f"""SELECT l.*, u.name AS owner_name {_OWNER_JOIN}
            WHERE {where}
            ORDER BY l.visit_date DESC, l.id DESC{limit_sql}""",
        params,
    )


# ---------------------------------------------------------------------------
# Single log CRUD
# ---------------------------------------------------------------------------

def get_log(user, log_id):
    clauses, params = _scope(user)
    row = get_db().one(
        f"SELECT l.*, u.name AS owner_name {_OWNER_JOIN} "
        f"WHERE l.id = %s AND {' AND '.join(clauses)}",
        [log_id] + params,
    )
    if row:
        row["media"] = list_media(row["id"])
    return row


def create_log(user, values, media=()):
    """Create a log. `media` is a list of (filename, kind) already saved on disk."""
    db = get_db()
    result = db.execute(
        """INSERT INTO field_logs
           (user_id, customer_name, location, contact_person, visit_date,
            observations, issues, follow_up, follow_up_date, status,
            transcript)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            user["id"],
            values["customer_name"], values["location"],
            _nullable(values["contact_person"]), values["visit_date"],
            _nullable(values["observations"]), _nullable(values["issues"]),
            _nullable(values["follow_up"]), _nullable(values["follow_up_date"]),
            values["status"], _nullable(values["transcript"]),
        ),
    )
    for filename, kind in media:
        db.execute("INSERT INTO log_media (log_id, filename, kind) VALUES (%s, %s, %s)",
                   (result.lastrowid, filename, kind))
    db.commit()
    return result.lastrowid


def update_log(user, log_id, values):
    """Update a log the user may access. Returns True if a row was changed."""
    if not get_log(user, log_id):
        return False
    db = get_db()
    db.execute(
        """UPDATE field_logs SET
               customer_name = %s, location = %s, contact_person = %s,
               visit_date = %s, observations = %s, issues = %s,
               follow_up = %s, follow_up_date = %s, status = %s,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = %s""",
        (
            values["customer_name"], values["location"],
            _nullable(values["contact_person"]), values["visit_date"],
            _nullable(values["observations"]), _nullable(values["issues"]),
            _nullable(values["follow_up"]), _nullable(values["follow_up_date"]),
            values["status"], log_id,
        ),
    )
    db.commit()
    return True


def delete_log(user, log_id):
    """Delete a log. Returns the log row that was deleted (or None)."""
    log = get_log(user, log_id)
    if not log:
        return None
    db = get_db()
    db.execute("DELETE FROM field_logs WHERE id = %s", (log_id,))
    db.commit()
    return log


# ---------------------------------------------------------------------------
# Recordings (any number per log)
# ---------------------------------------------------------------------------

def list_media(log_id):
    return get_db().all(
        "SELECT id, log_id, filename, kind, created_at FROM log_media "
        "WHERE log_id = %s ORDER BY id ASC", (log_id,))


def add_media(user, log_id, filename, kind):
    """Attach a saved recording to a log the user may access. Returns the new id or None."""
    if not get_log(user, log_id):
        return None
    db = get_db()
    result = db.execute("INSERT INTO log_media (log_id, filename, kind) VALUES (%s, %s, %s)",
                        (log_id, filename, kind))
    db.execute("UPDATE field_logs SET updated_at = CURRENT_TIMESTAMP WHERE id = %s", (log_id,))
    db.commit()
    return result.lastrowid


def get_media(user, log_id, media_id):
    if not get_log(user, log_id):
        return None
    return get_db().one(
        "SELECT id, log_id, filename, kind, created_at FROM log_media "
        "WHERE id = %s AND log_id = %s", (media_id, log_id))


def delete_media(user, log_id, media_id):
    """Remove one recording from a log. Returns the removed row (or None)."""
    row = get_media(user, log_id, media_id)
    if not row:
        return None
    db = get_db()
    db.execute("DELETE FROM log_media WHERE id = %s", (media_id,))
    db.execute("UPDATE field_logs SET updated_at = CURRENT_TIMESTAMP WHERE id = %s", (log_id,))
    db.commit()
    return row


def referenced_audio_files():
    """Names of every recording that belongs to a saved log."""
    return {r["filename"] for r in get_db().all("SELECT filename FROM log_media")}


# ---------------------------------------------------------------------------
# Dashboard + customers
# ---------------------------------------------------------------------------

def dashboard_stats(user, today=None):
    today = today or date.today()
    db = get_db()
    clauses, params = _scope(user)
    where = " AND ".join(clauses)

    total = int(db.scalar(f"SELECT COUNT(*) {_OWNER_JOIN} WHERE {where}", params))
    customers = int(db.scalar(
        f"SELECT COUNT(DISTINCT LOWER(l.customer_name)) {_OWNER_JOIN} WHERE {where}", params))
    by_status = {r["status"]: int(r["total"]) for r in db.all(
        f"SELECT l.status AS status, COUNT(*) AS total {_OWNER_JOIN} "
        f"WHERE {where} GROUP BY l.status", params)}

    horizon = (today + timedelta(days=7)).isoformat()
    followups = db.all(
        f"""SELECT l.id, l.customer_name, l.location, l.follow_up, l.follow_up_date
            {_OWNER_JOIN}
            WHERE {where} AND l.follow_up_date IS NOT NULL
              AND l.status <> 'Completed' AND l.follow_up_date <= %s
            ORDER BY l.follow_up_date ASC, l.id ASC LIMIT 8""",
        params + [horizon],
    )
    for item in followups:
        days = (date.fromisoformat(item["follow_up_date"]) - today).days
        item["due_days"] = days
        item["overdue"] = days < 0
        item["soon"] = 0 <= days <= 2

    first_day = today - timedelta(days=13)
    counts = {r["visit_date"]: int(r["total"]) for r in db.all(
        f"""SELECT l.visit_date AS visit_date, COUNT(*) AS total {_OWNER_JOIN}
            WHERE {where} AND l.visit_date >= %s AND l.visit_date <= %s
            GROUP BY l.visit_date""",
        params + [first_day.isoformat(), today.isoformat()])}
    per_day = []
    for offset in range(14):
        day = first_day + timedelta(days=offset)
        per_day.append({"date": day.isoformat(), "label": day.strftime("%d %b"),
                        "count": counts.get(day.isoformat(), 0)})

    recent = db.all(
        f"""SELECT l.id, l.customer_name, l.location, l.visit_date, l.status,
                   u.name AS owner_name
            {_OWNER_JOIN} WHERE {where}
            ORDER BY l.visit_date DESC, l.id DESC LIMIT 6""", params)

    return {
        "total": total,
        "customers": customers,
        "need_followup": by_status.get("Follow-up Required", 0),
        "completed": by_status.get("Completed", 0),
        "by_status": by_status,
        "followups": followups,
        "per_day": per_day,
        "max_per_day": max([d["count"] for d in per_day] + [1]),
        "recent": recent,
    }


def customers_summary(user, q=""):
    clauses, params = _scope(user)
    q = (q or "").strip()
    if q:
        clauses.append("l.customer_name LIKE %s ESCAPE '!'")
        params.append(f"%{_escape_like(q)}%")
    return get_db().all(
        f"""SELECT MIN(l.customer_name) AS customer_name,
                   COUNT(*) AS visits,
                   MAX(l.visit_date) AS last_visit,
                   MIN(l.location) AS location,
                   SUM(CASE WHEN l.status = 'Follow-up Required' THEN 1 ELSE 0 END) AS open_followups
            {_OWNER_JOIN}
            WHERE {' AND '.join(clauses)}
            GROUP BY LOWER(l.customer_name)
            ORDER BY MAX(l.visit_date) DESC, MIN(l.customer_name)""",
        params,
    )


def customer_detail(user, name):
    """Everything about one customer (case-insensitive name). None if unknown."""
    rows = export_rows(user, customer=name, limit=500)
    if not rows:
        return None
    today = date.today().isoformat()
    contacts, locations = [], []
    for r in rows:  # newest first, so lists keep "most recent first"
        for value, bucket in ((r["contact_person"], contacts), (r["location"], locations)):
            if value and value.lower() not in [v.lower() for v in bucket]:
                bucket.append(value)
    open_items = [r for r in rows if r["status"] != "Completed" and r["follow_up_date"]]
    return {
        "name": rows[0]["customer_name"],
        "rows": rows,
        "visits": len(rows),
        "first_visit": rows[-1]["visit_date"],
        "last_visit": rows[0]["visit_date"],
        "completed": sum(1 for r in rows if r["status"] == "Completed"),
        "needs_followup": sum(1 for r in rows if r["status"] == "Follow-up Required"),
        "overdue": sum(1 for r in open_items if r["follow_up_date"] < today),
        "contacts": contacts,
        "locations": locations,
    }
