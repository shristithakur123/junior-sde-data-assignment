
import argparse
from pathlib import Path

from database import create_tables
from ingestion import ingest_drop
from events import ingest_events_drop
from reports import generate_reports


def main():
    parser = argparse.ArgumentParser(
        description="Interaction Ledger and Reporting Pipeline"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True
    )

    # Ingest command
    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Ingest a data drop"
    )
    ingest_parser.add_argument(
        "drop_path",
        help="Path to the data drop folder"
    )

    # Report command
    report_parser = subparsers.add_parser(
        "report",
        help="Generate reports"
    )
    report_parser.add_argument(
        "--out",
        default="reports/",
        help="Output directory for reports"
    )

    args = parser.parse_args()

    create_tables()

    if args.command == "ingest":

        drop_path = Path(args.drop_path)

        if not drop_path.exists():
            parser.error(f"Drop folder does not exist: {drop_path}")

        print("\nStarting lead ingestion...")
        ingest_drop(drop_path)

        print("\nStarting event ingestion...")
        ingest_events_drop(drop_path)

        print("\nIngestion completed.")

    elif args.command == "report":

        print("\nGenerating reports...")
        generate_reports(args.out)


if __name__ == "__main__":
    main()
