# Submission

## Candidate

- **Name:** SHRISTI
- **Assignment chosen:** A (Data)
- **Time spent:** 6HRS]

## How to run

Run the following commands from the repository root. Place the supplied `data/` folder inside the repository root before running.

**Windows PowerShell:**

```powershell
# Create virtual environment
python -m venv .venv

# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Install dependencies
python -m pip install -r requirements.txt

# Ingest Drop 1
python .\solution\main.py ingest .\data\drop_1

# Ingest Drop 2
python .\solution\main.py ingest .\data\drop_2

# Generate reports
python .\solution\main.py report --out .\reports
```

The application uses SQLite and creates its database automatically.

## Design

The solution is a Python-based CLI pipeline with SQLite as the persistent storage layer.

The implementation is divided into separate modules:

- `main.py`: CLI entry point for ingestion and report generation.
- `normalization.py`: Phone, date, decimal, and enum normalization.
- `encryption.py`: Encryption of personal information and keyed phone hashing.
- `database.py`: SQLite connection and table creation.
- `ingestion.py`: Lead CSV ingestion, validation, rejection handling, and duplicate merging.
- `events.py`: JSONL event ingestion, deduplication, lead matching, and orphan handling.
- `reports.py`: Generation of the five required reports.

**Database design**

The database contains the following main tables:

- `leads`: Stores lead records, encrypted phone numbers, phone hashes, and batch information.
- `events`: Stores linked events and encrypted event payloads.
- `rejected_rows`: Stores rejected input rows in encrypted form with rejection reasons.
- `orphan_events`: Temporarily stores events that cannot be matched to a lead.
- `batches`: Tracks source batch information.
- `processed_files`: Tracks files already ingested to support idempotency.
- `pipeline_stats`: Tracks ingestion and data-quality statistics.

A unique constraint on `(workflow_id, phone_hash)` prevents duplicate leads within a workflow. Event IDs are unique in the event and orphan tables.

Phone numbers are encrypted for retrieval and hashed using HMAC for matching without storing plaintext phone numbers.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| SQLite for storage | Simple persistent relational database suitable for this assignment | PostgreSQL |
| HMAC-SHA256 for phone matching | Allows matching without exposing phone numbers | Plaintext phone comparison |
| Fernet encryption for PII | Provides authenticated encryption for retrievable personal data | Storing plaintext |
| Canonical Indian phone format (+91) | Ensures consistent phone matching | Keeping original input format |
| First valid lead establishes batch ID | Preserves the original source batch during duplicate merging | Updating batch ID on every duplicate |
| Unknown disposition codes are ignored and counted | Prevents unrecognized values from affecting lead status | Treating unknown codes as valid |
| Orphan events are quarantined and retried | Allows events to be linked when their leads arrive in a later drop | Permanently discarding unmatched events |
| Report dates use IST | Keeps reporting dates consistent with the required timezone | UTC reporting |

## Failure modes considered

- Invalid phone numbers and missing required fields are rejected with reasons.
- Optional invalid fields are stored as null and counted as warnings.
- Duplicate leads are merged using workflow and canonical phone identity.
- Duplicate event IDs are skipped.
- Malformed JSON events and events without event IDs are counted as malformed.
- Events without matching leads are quarantined and retried during subsequent ingestion.
- Repeated ingestion of processed files is skipped.
- PII is encrypted, and phone numbers are masked in reports.

**Known limitations / not fully verified:**

- Automated unit-test coverage has not yet been completed.
- Crash recovery and concurrent ingestion have not been independently stress-tested.
- Remaining orphan events require further investigation to determine whether their leads are absent or their matching information is insufficient.
- Performance has not been benchmarked on substantially larger datasets.

## Testing

The following checks were performed:

- Python syntax checks using `py_compile` across the main modules.
- Ingestion of both Drop 1 and Drop 2.
- Verification of lead insertion, duplicate merging, and rejected-row counts.
- Verification of event insertion, duplicate handling, malformed events, and orphan relinking.
- Generation of all five required reports.
- Inspection of `data_quality.json` and `batch_summary.csv`.
- Inspection of lead report columns and phone masking.
- Repeated Drop 1 ingestion to verify that previously processed files are skipped.
- Repeated ingestion again to confirm that no additional records or orphan links were created.

Automated unit tests for all normalization and reporting helper functions remain to be completed.

## Results

Both data drops were processed successfully.

**Combined ingestion results:**

- Total lead rows: 2,057
- Rejected lead rows: 108
- Duplicate lead rows merged: 57
- Field warnings: 122
- Total event lines: 8,745
- Malformed events: 8
- Duplicate events dropped: 237
- Unknown disposition events: 18
- Remaining orphan events after retry: 462

**Reports generated:**

1. `batch_summary.csv`
2. `daily_overview.csv`
3. `lead_report.csv`
4. `rejections.csv`
5. `data_quality.json`

Repeated ingestion was verified to skip already-processed files without adding duplicate records.

Execution timings were not formally benchmarked.

## If I had another day

1. Add comprehensive automated unit tests for normalization, disposition selection, and bucket calculation.
2. Add integration tests for both ingestion drops and report generation.
3. Improve crash recovery and transaction handling.
4. Investigate the remaining orphan events.
5. Add structured logging and better error reporting.
6. Benchmark performance with larger datasets.