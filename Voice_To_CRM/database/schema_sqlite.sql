-- Voice-to-CRM schema for SQLite (used when DB_BACKEND=sqlite)

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS field_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    customer_name    TEXT NOT NULL,
    location         TEXT NOT NULL,
    contact_person   TEXT,
    visit_date       TEXT NOT NULL,
    observations     TEXT,
    issues           TEXT,
    follow_up        TEXT,
    follow_up_date   TEXT,
    status           TEXT NOT NULL DEFAULT 'Pending',
    transcript       TEXT,
    audio_file       TEXT,
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_logs_user      ON field_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_logs_visit     ON field_logs(visit_date);
CREATE INDEX IF NOT EXISTS idx_logs_status    ON field_logs(status);
CREATE INDEX IF NOT EXISTS idx_logs_followup  ON field_logs(follow_up_date);

CREATE TABLE IF NOT EXISTS reminder_log (
    user_id  INTEGER NOT NULL,
    sent_on  TEXT NOT NULL,
    PRIMARY KEY (user_id, sent_on),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Any number of recordings (audio or video) per visit log.
CREATE TABLE IF NOT EXISTS log_media (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id     INTEGER NOT NULL,
    filename   TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'audio',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (log_id) REFERENCES field_logs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_media_log ON log_media(log_id);
