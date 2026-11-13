
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "ledger.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def create_tables():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            lead_id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL,
            phone_hash TEXT NOT NULL,
            phone_encrypted TEXT NOT NULL,
            batch_id TEXT,
            lead_data_encrypted TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(workflow_id, phone_hash)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            lead_id INTEGER,
            event_type TEXT,
            event_timestamp TEXT,
            disposition_code TEXT,
            payload_encrypted TEXT,
            workflow_id TEXT,
            call_id TEXT,
            FOREIGN KEY (lead_id) REFERENCES leads(lead_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rejected_rows (
            rejection_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT,
            row_number INTEGER,
            reason TEXT,
            row_data_encrypted TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orphan_events (
            event_id TEXT PRIMARY KEY,
            payload_encrypted TEXT,
            source_file TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            batch_id TEXT,
            workflow_id TEXT,
            source_file TEXT,
            processed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (workflow_id, batch_id, source_file)
        )
    """)

    # Persistent pipeline statistics.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_stats (
            stat_key TEXT PRIMARY KEY,
            stat_value INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Track files already processed to support idempotency.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_files (
            source_file TEXT PRIMARY KEY,
            file_type TEXT NOT NULL,
            processed_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Add event columns if upgrading an older database.
    event_columns = {
        row["name"]
        for row in cursor.execute("PRAGMA table_info(events)").fetchall()
    }

    if "workflow_id" not in event_columns:
        cursor.execute("ALTER TABLE events ADD COLUMN workflow_id TEXT")

    if "call_id" not in event_columns:
        cursor.execute("ALTER TABLE events ADD COLUMN call_id TEXT")

    conn.commit()
    conn.close()


def update_stat(cursor, key, amount=1):
    cursor.execute("""
        INSERT INTO pipeline_stats (stat_key, stat_value)
        VALUES (?, ?)
        ON CONFLICT(stat_key)
        DO UPDATE SET stat_value = stat_value + excluded.stat_value
    """, (key, amount))


if __name__ == "__main__":
    create_tables()
    print("Database and tables created successfully!")
