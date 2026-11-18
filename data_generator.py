"""Admin tool: generate simulated EventFlow data as a CSV, without Firebase.

Uses the same simulator as the dashboard's admin panel, so both produce identical kinds of data.

Examples:
    python data_generator.py --records 800 --profile Realistic --force
    python data_generator.py --events "AI Workshop, Music Fest" --seed 7 --output demo.csv
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

from eventflow.simulation import DEFAULT_EVENTS, PROFILES, generate_records
from eventflow.storage import CSV_PATH, save_csv


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Generate simulated EventFlow registration and feedback data (CSV).")
    p.add_argument("--records", type=int, default=600, help="number of registrations to simulate (default 600)")
    p.add_argument("--events", default=", ".join(DEFAULT_EVENTS),
                   help="comma-separated event names (default: the dashboard's default events)")
    p.add_argument("--profile", choices=PROFILES, default="Realistic",
                   help="overall sentiment profile (default Realistic)")
    p.add_argument("--seed", type=int, help="random seed for reproducible output")
    p.add_argument("--output", type=Path, default=CSV_PATH,
                   help=f"CSV to write (default: {CSV_PATH.name}, the file the dashboard reads)")
    p.add_argument("--force", action="store_true", help="overwrite the output file if it already exists")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    events = [e.strip() for e in args.events.split(",") if e.strip()]
    if not events:
        sys.exit("error: --events must name at least one event")
    if args.records < 1:
        sys.exit("error: --records must be at least 1")
    if args.output.exists() and not args.force:
        sys.exit(f"error: {args.output} already exists; pass --force to overwrite it")

    records = generate_records(args.records, events, args.profile, seed=args.seed)
    save_csv(records, args.output)

    df = pd.DataFrame(records)
    print(f"Wrote {len(df)} rows ({args.records} registrations + injected duplicates) to {args.output}")
    summary = df.groupby("Event_Name").agg(Registrations=("Attended", "size"), Attendance=("Attended", "mean"),
                                           Avg_Rating=("Rating", "mean"), Date=("Event_Date", "first"))
    print(summary.round(2).to_string())


if __name__ == "__main__":
    main()
