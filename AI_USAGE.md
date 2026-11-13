# AI Usage Disclosure

## Tools used

- ChatGPT (OpenAI)

## What I used them for

I used ChatGPT throughout the assignment to understand the problem statement and break the implementation into smaller steps.

Specifically, I used it for:
- Understanding the lead ingestion, event ingestion, and reporting requirements.
- Generating initial code and implementation guidance for Python modules such as `normalization.py`, `encryption.py`, `database.py`, `ingestion.py`, `events.py`, `reports.py`, and `main.py`.
- Understanding phone number normalization, date validation, decimal conversion, and duplicate handling.
- Implementing guidance for SQLite storage, PII encryption, keyed phone hashing, event matching, and orphan event handling.
- Debugging Python errors and understanding how to run the CLI commands.
- Reviewing generated reports and checking data-quality statistics.

## What I wrote or decided myself

- I reviewed the assignment requirements and followed the required ingestion and reporting workflow.
- I organized the implementation into separate Python modules.
- I executed the commands and tested the application locally.
- I checked the ingestion results for both data drops.
- I reviewed the generated reports, including batch summaries, lead records, and data-quality statistics.
- I checked that phone numbers were masked in the lead report.
- I verified that repeated ingestion skipped previously processed files.
- I made implementation and debugging decisions based on the actual output and assignment requirements.

AI assistance was used substantially in writing the initial implementation, so I do not claim that all the code was independently written by me.

## Where the AI was wrong or unhelpful

One generated version of `reports.py` used `re.split()` without importing the `re` module. When I ran report generation, Python raised a `NameError`.

I examined the traceback, identified the missing import, added `import re`, and reran report generation successfully.

I also encountered a missing `tzdata` dependency when using `ZoneInfo("Asia/Kolkata")`. I installed the required package and verified that the timezone could be loaded.

## How I verified AI-generated code

- Ran Python syntax checks using `py_compile`.
- Executed the CLI ingestion command for Drop 1 and Drop 2.
- Checked the number of inserted, merged, rejected, duplicate, malformed, and orphan records.
- Generated all five required reports.
- Inspected the contents of `data_quality.json`, `batch_summary.csv`, and `lead_report.csv`.
- Verified that phone numbers were masked in the lead report.
- Re-ran Drop 1 to check that previously processed files were skipped.
- Regenerated the reports and checked that the updated orphan count was reflected.

I used actual command outputs and generated reports to verify the implementation rather than relying only on AI-generated explanations.