-- Voice-to-CRM schema for MySQL 8 / MariaDB (used when DB_BACKEND=mysql)
-- The app runs this automatically on start. You can also import it by hand:
--   mysql -u root -p < database/schema_mysql.sql
-- (change the database name below if you use a different MYSQL_DATABASE)

CREATE DATABASE IF NOT EXISTS voice_to_crm CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE voice_to_crm;

CREATE TABLE IF NOT EXISTS users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    name          VARCHAR(100) NOT NULL,
    email         VARCHAR(150) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(20)  NOT NULL DEFAULT 'user',
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS field_logs (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    user_id          INT NOT NULL,
    customer_name    VARCHAR(150) NOT NULL,
    location         VARCHAR(200) NOT NULL,
    contact_person   VARCHAR(150) NULL,
    visit_date       DATE NOT NULL,
    observations     TEXT NULL,
    issues           TEXT NULL,
    follow_up        TEXT NULL,
    follow_up_date   DATE NULL,
    status           VARCHAR(30) NOT NULL DEFAULT 'Pending',
    transcript       MEDIUMTEXT NULL,
    audio_file       VARCHAR(100) NULL,
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_logs_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_logs_user     (user_id),
    INDEX idx_logs_visit    (visit_date),
    INDEX idx_logs_status   (status),
    INDEX idx_logs_followup (follow_up_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS reminder_log (
    user_id  INT  NOT NULL,
    sent_on  DATE NOT NULL,
    PRIMARY KEY (user_id, sent_on),
    CONSTRAINT fk_rem_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Any number of recordings (audio or video) per visit log.
CREATE TABLE IF NOT EXISTS log_media (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    log_id     INT NOT NULL,
    filename   VARCHAR(100) NOT NULL,
    kind       VARCHAR(10)  NOT NULL DEFAULT 'audio',
    created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_media_log FOREIGN KEY (log_id) REFERENCES field_logs(id) ON DELETE CASCADE,
    INDEX idx_media_log (log_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
