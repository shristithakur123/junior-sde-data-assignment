
import csv
import json
import re
from pathlib import Path

from database import get_connection, update_stat
from encryption import encrypt_text, decrypt_text, hash_phone
from normalization import (
    normalize_phone,
    normalize_date,
    normalize_decimal,
    normalize_enum,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "assignment-a-data" / "schemas"


def normalize_header(value):
    value = value.strip().lower()
    return re.sub(r"[_\s]+", " ", value)


def load_schema(workflow_id):
    path = SCHEMA_DIR / f"{workflow_id}.json"

    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def build_field_mapping(schema):
    mapping = {}

    for field in schema["fields"]:
        mapping[normalize_header(field["name"])] = field["name"]

        for alias in field.get("aliases", []):
            mapping[normalize_header(alias)] = field["name"]

    return mapping


def process_value(value, field):
    value = (value or "").strip()

    if not value:
        return None, None

    field_type = field["type"]

    if field_type == "phone":
        result = normalize_phone(value)
        return result, None if result else "invalid_phone"

    if field_type == "date":
        result = normalize_date(value)
        return result, None if result else "invalid_date"

    if field_type == "decimal":
        result = normalize_decimal(value)
        return result, None if result is not None else "invalid_decimal"

    if field_type == "enum":
        result = normalize_enum(value, field.get("values", []))
        return result, None if result else "invalid_enum"

    return value, None


def is_blank_row(row):
    return not any(str(value or "").strip() for value in row.values())


def apply_defaults(lead, fields):
    for field in fields:
        name = field["name"]

        if lead.get(name) is None and "default" in field:
            lead[name] = field["default"]

    return lead


def ingest_lead_file(csv_path, workflow_id):

    csv_path = Path(csv_path).resolve()
    source_file = str(csv_path)

    schema = load_schema(workflow_id)
    fields = schema["fields"]
    mapping = build_field_mapping(schema)

    batch_id = csv_path.stem

    inserted = 0
    merged = 0
    rejected = 0
    warnings = 0
    row_number = 0

    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Skip files that have already been processed.
        already_processed = cursor.execute(
            """
            SELECT 1 FROM processed_files
            WHERE source_file = ?
            """,
            (source_file,),
        ).fetchone()

        if already_processed:
            print(f"Already processed, skipping: {csv_path.name}")

            return {
                "file": csv_path.name,
                "workflow": workflow_id,
                "inserted": 0,
                "merged": 0,
                "rejected": 0,
                "warnings": 0,
                "skipped": True,
            }

        with open(
            csv_path,
            "r",
            encoding="utf-8-sig",
            newline=""
        ) as file:

            reader = csv.DictReader(file)

            for original_row in reader:

                if is_blank_row(original_row):
                    continue

                row_number += 1
                clean_row = {}

                # Map CSV headers to schema field names.
                for header, value in original_row.items():

                    if header is None:
                        continue

                    standard_name = mapping.get(
                        normalize_header(header)
                    )

                    if standard_name:
                        clean_row[standard_name] = value

                lead = {}
                rejection_reason = None
                row_warnings = 0

                # Validate fields in schema order.
                for field in fields:

                    name = field["name"]
                    raw_value = clean_row.get(name)

                    if not str(raw_value or "").strip():

                        if field.get("required"):
                            rejection_reason = (
                                f"missing_required:{name}"
                            )
                            break

                        lead[name] = None
                        continue

                    value, error = process_value(raw_value, field)

                    if error:

                        if field.get("required"):
                            rejection_reason = error
                            break

                        lead[name] = None
                        row_warnings += 1

                    else:
                        lead[name] = value

                # Save rejected rows.
                if rejection_reason:

                    cursor.execute(
                        """
                        INSERT INTO rejected_rows
                        (source_file, row_number, reason, row_data_encrypted)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            source_file,
                            row_number,
                            rejection_reason,
                            encrypt_text(json.dumps(original_row)),
                        ),
                    )

                    rejected += 1
                    continue

                # Count warnings only for accepted rows.
                warnings += row_warnings

                phone = lead["phone"]
                phone_digest = hash_phone(phone)

                # Check for an existing lead.
                existing = cursor.execute(
                    """
                    SELECT lead_id, lead_data_encrypted
                    FROM leads
                    WHERE workflow_id = ? AND phone_hash = ?
                    """,
                    (workflow_id, phone_digest),
                ).fetchone()

                if existing:

                    old_lead = json.loads(
                        decrypt_text(existing["lead_data_encrypted"])
                    )

                    # New non-null values overwrite old values.
                    for key, value in lead.items():
                        if value is not None:
                            old_lead[key] = value

                    apply_defaults(old_lead, fields)

                    cursor.execute(
                        """
                        UPDATE leads
                        SET lead_data_encrypted = ?
                        WHERE lead_id = ?
                        """,
                        (
                            encrypt_text(json.dumps(old_lead)),
                            existing["lead_id"],
                        ),
                    )

                    merged += 1

                else:

                    apply_defaults(lead, fields)

                    cursor.execute(
                        """
                        INSERT INTO leads
                        (
                            workflow_id,
                            phone_hash,
                            phone_encrypted,
                            batch_id,
                            lead_data_encrypted
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            workflow_id,
                            phone_digest,
                            encrypt_text(phone),
                            batch_id,
                            encrypt_text(json.dumps(lead)),
                        ),
                    )

                    inserted += 1

        # Store statistics in the same transaction.
        update_stat(cursor, "lead_rows_total", row_number)

        if rejected:
            update_stat(cursor, "lead_rows_rejected", rejected)

        if merged:
            update_stat(cursor, "duplicate_lead_rows_merged", merged)

        if warnings:
            update_stat(cursor, "field_warnings", warnings)

        # Record the processed file.
        cursor.execute(
            """
            INSERT INTO processed_files (source_file, file_type)
            VALUES (?, 'leads')
            """,
            (source_file,),
        )

        cursor.execute(
            """
            INSERT OR IGNORE INTO batches
            (batch_id, workflow_id, source_file)
            VALUES (?, ?, ?)
            """,
            (batch_id, workflow_id, source_file),
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    result = {
        "file": csv_path.name,
        "workflow": workflow_id,
        "inserted": inserted,
        "merged": merged,
        "rejected": rejected,
        "warnings": warnings,
        "skipped": False,
    }

    print(result)
    return result


def ingest_drop(drop_path):

    drop_path = Path(drop_path)
    results = []

    for workflow_id in ["motor_renewal", "policy_feedback"]:

        folder = drop_path / "leads" / workflow_id

        if not folder.exists():
            continue

        for csv_path in sorted(folder.glob("*.csv")):

            result = ingest_lead_file(csv_path, workflow_id)
            results.append(result)

    return results
