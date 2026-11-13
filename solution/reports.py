import re
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from database import get_connection
from encryption import decrypt_text

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "assignment-a-data" / "schemas"
IST = ZoneInfo("Asia/Kolkata")

REPORT_COLUMNS = {
    "batch_summary.csv": [
        "workflow_id", "batch_id", "total_leads",
        "yet_to_reach_out", "in_progress_telephony_failed",
        "in_progress_dialed", "completed"
    ],
    "daily_overview.csv": [
        "workflow_id", "date", "total_calls",
        "connected_calls", "connectivity_rate", "success_leads"
    ],
    "lead_report.csv": [
        "workflow_id", "external_ref", "batch_id", "phone_masked",
        "bucket", "total_calls", "connected_calls",
        "last_call_at", "current_disposition", "ever_success"
    ],
    "rejections.csv": [
        "workflow_id", "batch_id", "row_number", "reason"
    ],
}


def parse_timestamp(value):
    if not value:
        return None

    try:
        value = str(value).strip().replace("Z", "+00:00")
        result = datetime.fromisoformat(value)

        if result.tzinfo is None:
            result = result.replace(tzinfo=IST)

        return result.astimezone(IST)

    except (ValueError, TypeError):
        return None


def load_dispositions():
    path = SCHEMA_DIR / "dispositions.csv"
    catalogue = {}

    with open(path, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        for row in reader:
            catalogue[(row["workflow_id"], row["code"])] = {
                "label": row["label"],
                "is_terminal": row["is_terminal"].strip().lower() == "true",
                "is_success": row["is_success"].strip().lower() == "true",
            }

    return catalogue


def load_reference_fields():
    references = {}

    for workflow in ["motor_renewal", "policy_feedback"]:
        path = SCHEMA_DIR / f"{workflow}.json"

        with open(path, "r", encoding="utf-8") as file:
            schema = json.load(file)

        for field in schema["fields"]:
            if field.get("reference") is True:
                references[workflow] = field["name"]
                break

    return references


def read_payload(row):
    encrypted = row["payload_encrypted"]

    if not encrypted:
        return {}

    return json.loads(decrypt_text(encrypted))


def effective_disposition(events):
    overrides = [
        event for event in events
        if event["event_type"] == "disposition.override"
        and event["disposition_code"]
    ]

    if overrides:
        candidates = overrides
    else:
        candidates = [
            event for event in events
            if event["event_type"] == "disposition.set"
            and event["disposition_code"]
        ]

    if not candidates:
        return None

    candidates.sort(
        key=lambda event: event["_timestamp"] or datetime.min.replace(tzinfo=IST)
    )

    return candidates[-1]["disposition_code"]


def write_csv(path, columns, rows):
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=columns,
            extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)


def get_quality_stats(cursor):
    defaults = {
        "lead_rows_total": 0,
        "lead_rows_rejected": 0,
        "duplicate_lead_rows_merged": 0,
        "field_warnings": 0,
        "events_total": 0,
        "malformed_events": 0,
        "duplicate_events_dropped": 0,
        "unknown_disposition_events": 0,
    }

    # Statistics will be populated by the ingestion pipeline.
    table = cursor.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = 'pipeline_stats'
        """
    ).fetchone()

    if table:
        for row in cursor.execute(
            "SELECT stat_key, stat_value FROM pipeline_stats"
        ).fetchall():
            if row["stat_key"] in defaults:
                defaults[row["stat_key"]] = row["stat_value"]

    return defaults


def generate_reports(output_dir="reports"):

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    catalogue = load_dispositions()
    reference_fields = load_reference_fields()

    conn = get_connection()
    cursor = conn.cursor()

    try:
        # ------------------------------------------------
        # 1. Load and decrypt leads
        # ------------------------------------------------

        lead_rows = cursor.execute(
            "SELECT * FROM leads ORDER BY workflow_id, batch_id"
        ).fetchall()

        leads = {}

        for row in lead_rows:
            lead_data = json.loads(
                decrypt_text(row["lead_data_encrypted"])
            )

            phone = decrypt_text(row["phone_encrypted"])

            lead_id = row["lead_id"]

            leads[lead_id] = {
                "lead_id": lead_id,
                "workflow_id": row["workflow_id"],
                "batch_id": row["batch_id"],
                "phone": phone,
                "data": lead_data,
                "calls": [],
            }

        # ------------------------------------------------
        # 2. Load linked events
        # ------------------------------------------------

        event_rows = cursor.execute(
            """
            SELECT *
            FROM events
            WHERE lead_id IS NOT NULL
            ORDER BY event_timestamp
            """
        ).fetchall()

        call_groups = defaultdict(list)

        for row in event_rows:

            if row["lead_id"] not in leads:
                continue

            payload = read_payload(row)

            event_type = row["event_type"]
            workflow_id = row["workflow_id"] or leads[row["lead_id"]]["workflow_id"]
            call_id = row["call_id"]

            # Only call-related events belong to call groups.
            if not call_id:
                continue

            timestamp = parse_timestamp(
                row["event_timestamp"]
                or payload.get("occurred_at")
            )

            event = {
                "event_type": event_type,
                "disposition_code": row["disposition_code"],
                "_timestamp": timestamp,
            }

            key = (workflow_id, call_id, row["lead_id"])
            call_groups[key].append(event)

        # ------------------------------------------------
        # 3. Build calls from call.initiated events
        # ------------------------------------------------

        daily = defaultdict(
            lambda: {
                "total_calls": 0,
                "connected_calls": 0,
                "success_leads": set(),
            }
        )

        for (workflow_id, call_id, lead_id), events in call_groups.items():

            initiated = [
                event for event in events
                if event["event_type"] == "call.initiated"
            ]

            # Calls without call.initiated must not be counted.
            if not initiated:
                continue

            initiated.sort(
                key=lambda event: event["_timestamp"]
                or datetime.min.replace(tzinfo=IST)
            )

            initiated_event = initiated[0]
            initiated_time = initiated_event["_timestamp"]

            connected = any(
                event["event_type"] == "call.connected"
                for event in events
            )

            telephony_failed = any(
                event["event_type"] == "call.telephony_failed"
                for event in events
            )

            disposition_code = effective_disposition(events)

            disposition_info = catalogue.get(
                (workflow_id, disposition_code)
            )

            call = {
                "workflow_id": workflow_id,
                "call_id": call_id,
                "lead_id": lead_id,
                "initiated_time": initiated_time,
                "connected": connected,
                "telephony_failed": telephony_failed,
                "disposition": disposition_code,
                "is_terminal": (
                    disposition_info["is_terminal"]
                    if disposition_info else False
                ),
                "is_success": (
                    disposition_info["is_success"]
                    if disposition_info else False
                ),
            }

            leads[lead_id]["calls"].append(call)

            # Daily overview uses the initiated date in IST.
            if initiated_time:
                date = initiated_time.date().isoformat()
                daily_key = (workflow_id, date)

                daily[daily_key]["total_calls"] += 1

                if connected:
                    daily[daily_key]["connected_calls"] += 1

                if call["is_success"]:
                    daily[daily_key]["success_leads"].add(lead_id)

        # ------------------------------------------------
        # 4. Create lead report and batch summary
        # ------------------------------------------------

        lead_report = []
        batch_data = defaultdict(
            lambda: {
                "total_leads": 0,
                "yet_to_reach_out": 0,
                "in_progress_telephony_failed": 0,
                "in_progress_dialed": 0,
                "completed": 0,
            }
        )

        for lead in leads.values():

            calls = lead["calls"]

            calls.sort(
                key=lambda call: call["initiated_time"]
                or datetime.min.replace(tzinfo=IST)
            )

            # Determine lead bucket using required priority.
            if not calls:
                bucket = "yet_to_reach_out"

            elif any(call["is_terminal"] for call in calls):
                bucket = "completed"

            elif all(call["telephony_failed"] for call in calls):
                bucket = "in_progress_telephony_failed"

            else:
                bucket = "in_progress_dialed"

            workflow_id = lead["workflow_id"]
            batch_id = lead["batch_id"]

            batch_key = (workflow_id, batch_id)

            batch_data[batch_key]["total_leads"] += 1
            batch_data[batch_key][bucket] += 1

            connected_count = sum(
                1 for call in calls if call["connected"]
            )

            initiated_times = [
                call["initiated_time"]
                for call in calls
                if call["initiated_time"]
            ]

            last_call_at = (
                max(initiated_times).isoformat(timespec="seconds")
                if initiated_times else ""
            )

            # Most recent call that has an effective disposition.
            calls_with_disposition = [
                call for call in calls if call["disposition"]
            ]

            current_disposition = (
                calls_with_disposition[-1]["disposition"]
                if calls_with_disposition else ""
            )

            ever_success = any(
                call["is_success"] for call in calls
            )

            phone = lead["phone"] or ""
            phone_digits = "".join(
                character for character in phone
                if character.isdigit()
            )

            phone_masked = (
                "+91******" + phone_digits[-4:]
                if len(phone_digits) >= 4 else ""
            )

            external_ref = lead["data"].get(
                reference_fields.get(workflow_id, ""), ""
            )

            lead_report.append({
                "workflow_id": workflow_id,
                "external_ref": external_ref,
                "batch_id": batch_id,
                "phone_masked": phone_masked,
                "bucket": bucket,
                "total_calls": len(calls),
                "connected_calls": connected_count,
                "last_call_at": last_call_at,
                "current_disposition": current_disposition,
                "ever_success": str(ever_success).lower(),
            })

        batch_summary = []

        for (workflow_id, batch_id), values in sorted(batch_data.items()):
            batch_summary.append({
                "workflow_id": workflow_id,
                "batch_id": batch_id,
                **values,
            })

        lead_report.sort(
            key=lambda row: (
                row["workflow_id"],
                str(row["external_ref"])
            )
        )

        # ------------------------------------------------
        # 5. Create daily overview
        # ------------------------------------------------

        daily_overview = []

        for (workflow_id, date), values in sorted(daily.items()):

            total_calls = values["total_calls"]
            connected_calls = values["connected_calls"]

            rate = (
                connected_calls / total_calls
                if total_calls else 0
            )

            daily_overview.append({
                "workflow_id": workflow_id,
                "date": date,
                "total_calls": total_calls,
                "connected_calls": connected_calls,
                "connectivity_rate": f"{rate:.4f}",
                "success_leads": len(values["success_leads"]),
            })

        # ------------------------------------------------
        # 6. Create rejection report
        # ------------------------------------------------

        rejection_rows = cursor.execute(
            "SELECT source_file, row_number, reason FROM rejected_rows"
        ).fetchall()

        rejections = []

        for row in rejection_rows:

            parts = re.split(r"[\\/]+", row["source_file"])

            workflow_id = (
                parts[-2] if len(parts) >= 2 else ""
            )

            batch_id = Path(parts[-1]).stem if parts else ""

            rejections.append({
                "workflow_id": workflow_id,
                "batch_id": batch_id,
                "row_number": row["row_number"],
                "reason": row["reason"],
            })

        rejections.sort(
            key=lambda row: (
                row["workflow_id"],
                row["batch_id"],
                row["row_number"]
            )
        )

        # ------------------------------------------------
        # 7. Create data quality report
        # ------------------------------------------------

        quality = get_quality_stats(cursor)

        reason_rows = cursor.execute(
            """
            SELECT reason, COUNT(*) AS total
            FROM rejected_rows
            GROUP BY reason
            ORDER BY reason
            """
        ).fetchall()

        rejections_by_reason = {
            row["reason"]: row["total"]
            for row in reason_rows
        }

        orphan_count = cursor.execute(
            "SELECT COUNT(*) FROM orphan_events"
        ).fetchone()[0]

        data_quality = {
            "lead_rows_total": quality["lead_rows_total"],
            "lead_rows_rejected": len(rejections),
            "rejections_by_reason": rejections_by_reason,
            "duplicate_lead_rows_merged": quality[
                "duplicate_lead_rows_merged"
            ],
            "field_warnings": quality["field_warnings"],
            "events_total": quality["events_total"],
            "malformed_events": quality["malformed_events"],
            "duplicate_events_dropped": quality[
                "duplicate_events_dropped"
            ],
            "orphan_events": orphan_count,
            "unknown_disposition_events": quality[
                "unknown_disposition_events"
            ],
        }

        # ------------------------------------------------
        # 8. Write exactly five reports
        # ------------------------------------------------

        write_csv(
            output_dir / "batch_summary.csv",
            REPORT_COLUMNS["batch_summary.csv"],
            batch_summary,
        )

        write_csv(
            output_dir / "daily_overview.csv",
            REPORT_COLUMNS["daily_overview.csv"],
            daily_overview,
        )

        write_csv(
            output_dir / "lead_report.csv",
            REPORT_COLUMNS["lead_report.csv"],
            lead_report,
        )

        write_csv(
            output_dir / "rejections.csv",
            REPORT_COLUMNS["rejections.csv"],
            rejections,
        )

        with open(
            output_dir / "data_quality.json",
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(data_quality, file, indent=2)

        print("\nReports generated successfully!")
        print("Output folder:", output_dir.resolve())
        print("Files generated:")
        for filename in [
            "batch_summary.csv",
            "daily_overview.csv",
            "lead_report.csv",
            "rejections.csv",
            "data_quality.json",
        ]:
            print("-", filename)

    finally:
        conn.close()
