
import csv
import json
from pathlib import Path

from database import get_connection, update_stat
from encryption import encrypt_text, decrypt_text, hash_phone
from normalization import normalize_phone

REPO_ROOT = Path(__file__).resolve().parents[1]
DISPOSITIONS_FILE = (
    REPO_ROOT / "assignment-a-data" / "schemas" / "dispositions.csv"
)


def prepare_database(conn):
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(events)").fetchall()
    }

    if "workflow_id" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN workflow_id TEXT")

    if "call_id" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN call_id TEXT")


def load_dispositions():
    allowed = {}

    with open(
        DISPOSITIONS_FILE,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        reader = csv.DictReader(file)

        for row in reader:
            workflow = row["workflow_id"].strip()
            code = row["code"].strip().lower()
            allowed.setdefault(workflow, set()).add(code)

    return allowed


def find_lead(cursor, workflow_id, phone):
    if not workflow_id or not phone:
        return None

    canonical_phone = normalize_phone(phone)

    if not canonical_phone:
        return None

    digest = hash_phone(canonical_phone)

    row = cursor.execute(
        """
        SELECT lead_id
        FROM leads
        WHERE workflow_id = ? AND phone_hash = ?
        """,
        (workflow_id, digest),
    ).fetchone()

    return row["lead_id"] if row else None


def build_call_map(cursor, current_events):
    call_map = {}

    rows = cursor.execute(
        """
        SELECT workflow_id, call_id, lead_id
        FROM events
        WHERE call_id IS NOT NULL
        AND workflow_id IS NOT NULL
        AND lead_id IS NOT NULL
        """
    ).fetchall()

    for row in rows:
        call_map[(row["workflow_id"], row["call_id"])] = row["lead_id"]

    for event in current_events:

        workflow = event.get("workflow_id")
        call_id = event.get("call_id")
        phone = event.get("phone")

        if not workflow or not call_id or not phone:
            continue

        lead_id = find_lead(cursor, workflow, phone)

        if lead_id is not None:
            call_map[(workflow, call_id)] = lead_id

    return call_map


def resolve_lead(cursor, event, call_map):
    workflow = event.get("workflow_id")
    phone = event.get("phone")

    lead_id = find_lead(cursor, workflow, phone)

    if lead_id is not None:
        return lead_id

    call_id = event.get("call_id")

    if workflow and call_id:
        return call_map.get((workflow, call_id))

    return None


def get_disposition_code(event):
    event_type = event.get("event_type", "")

    if event_type not in (
        "disposition.set",
        "disposition.override"
    ):
        return None

    payload = event.get("payload", {})

    if not isinstance(payload, dict):
        return None

    code = payload.get("disposition_code")

    if code is None:
        return None

    return str(code).strip().lower()


def save_event(cursor, event, lead_id, allowed):
    event_type = event.get("event_type", "")
    workflow = event.get("workflow_id")
    call_id = event.get("call_id")
    timestamp = event.get("occurred_at")

    disposition_code = get_disposition_code(event)

    if disposition_code not in allowed.get(workflow, set()):
        disposition_code = None

    cursor.execute(
        """
        INSERT INTO events
        (
            event_id,
            lead_id,
            event_type,
            event_timestamp,
            disposition_code,
            payload_encrypted,
            workflow_id,
            call_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["event_id"],
            lead_id,
            event_type,
            timestamp,
            disposition_code,
            encrypt_text(json.dumps(event)),
            workflow,
            call_id,
        ),
    )


def ingest_events_drop(drop_path):

    drop_path = Path(drop_path)
    event_folder = drop_path / "events"

    if not event_folder.exists():
        print("No events folder found:", event_folder)
        return

    conn = get_connection()
    cursor = conn.cursor()

    try:
        prepare_database(conn)
        allowed = load_dispositions()

        current_events = []
        processed_paths = []

        total_lines = 0
        malformed = 0
        missing_event_id = 0

        # First pass: read event files that have not been processed.
        for filename in sorted(event_folder.glob("*.jsonl")):

            source_file = str(filename.resolve())

            already_processed = cursor.execute(
                """
                SELECT 1
                FROM processed_files
                WHERE source_file = ?
                """,
                (source_file,),
            ).fetchone()

            if already_processed:
                print("Already processed, skipping:", filename.name)
                continue

            processed_paths.append(source_file)

            with open(filename, "r", encoding="utf-8") as file:

                for line in file:

                    if not line.strip():
                        continue

                    total_lines += 1

                    try:
                        event = json.loads(line)

                    except json.JSONDecodeError:
                        malformed += 1
                        continue

                    if not isinstance(event, dict):
                        malformed += 1
                        continue

                    if not event.get("event_id"):
                        malformed += 1
                        missing_event_id += 1
                        continue

                    current_events.append(
                        (event, source_file)
                    )

        # Build call mappings before saving events.
        event_dicts = [
            item[0] for item in current_events
        ]

        call_map = build_call_map(cursor, event_dicts)

        inserted = 0
        duplicates = 0
        new_orphans = 0
        unknown_dispositions = 0
        seen_ids = set()

        # Second pass: match and save events.
        for event, source_file in current_events:

            event_id = str(event["event_id"]).strip()

            if event_id in seen_ids:
                duplicates += 1
                continue

            seen_ids.add(event_id)
            event["event_id"] = event_id

            exists = cursor.execute(
                "SELECT 1 FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()

            if exists:
                duplicates += 1
                continue

            exists = cursor.execute(
                "SELECT 1 FROM orphan_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()

            if exists:
                duplicates += 1
                continue

            lead_id = resolve_lead(cursor, event, call_map)

            # Orphan events are quarantined before disposition validation.
            if lead_id is None:

                cursor.execute(
                    """
                    INSERT INTO orphan_events
                    (event_id, payload_encrypted, source_file)
                    VALUES (?, ?, ?)
                    """,
                    (
                        event_id,
                        encrypt_text(json.dumps(event)),
                        source_file,
                    ),
                )

                new_orphans += 1
                continue

            code = get_disposition_code(event)

            if code and code not in allowed.get(
                event.get("workflow_id"), set()
            ):
                unknown_dispositions += 1

            save_event(cursor, event, lead_id, allowed)
            inserted += 1

        # Third pass: retry previously quarantined events.
        old_orphans = cursor.execute(
            """
            SELECT event_id, payload_encrypted, source_file
            FROM orphan_events
            """
        ).fetchall()

        relinked = 0

        for orphan in old_orphans:

            event = json.loads(
                decrypt_text(orphan["payload_encrypted"])
            )

            lead_id = resolve_lead(cursor, event, call_map)

            if lead_id is None:
                continue

            code = get_disposition_code(event)

            if code and code not in allowed.get(
                event.get("workflow_id"), set()
            ):
                unknown_dispositions += 1

            save_event(cursor, event, lead_id, allowed)

            cursor.execute(
                """
                DELETE FROM orphan_events
                WHERE event_id = ?
                """,
                (orphan["event_id"],),
            )

            relinked += 1

        # Save statistics.
        if total_lines:
            update_stat(cursor, "events_total", total_lines)

        if malformed:
            update_stat(cursor, "malformed_events", malformed)

        if duplicates:
            update_stat(
                cursor,
                "duplicate_events_dropped",
                duplicates
            )

        if unknown_dispositions:
            update_stat(
                cursor,
                "unknown_disposition_events",
                unknown_dispositions
            )

        # Mark event files as processed.
        for source_file in processed_paths:

            cursor.execute(
                """
                INSERT INTO processed_files
                (source_file, file_type)
                VALUES (?, 'events')
                """,
                (source_file,),
            )

        conn.commit()

        print("\nEVENT INGESTION SUMMARY")
        print("Non-empty lines read:", total_lines)
        print("Valid events read:", len(current_events))
        print("Events inserted:", inserted)
        print("Duplicate events skipped:", duplicates)
        print("Malformed events:", malformed)
        print("Missing event IDs:", missing_event_id)
        print("New orphan events:", new_orphans)
        print("Previously orphaned events linked:", relinked)
        print("Unknown disposition codes:", unknown_dispositions)

        final_orphans = cursor.execute(
            "SELECT COUNT(*) FROM orphan_events"
        ).fetchone()[0]

        print("Total remaining orphan events:", final_orphans)

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()
