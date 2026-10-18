"""Explicitly reset a database to a fresh, EMPTY schema (drop + recreate).

This is a deliberate, standalone script. The application never resets the
database on startup (see app/__init__.py and Requirement 11.4) so that saved
results survive restarts. Resetting is something you run yourself, on purpose.

SAFETY — this script is built to only ever wipe the TEST/demo database:
    * It defaults to the TEST configuration (``app.config.TestConfig``), which
      reads its connection string from TEST_DATABASE_URL — a SEPARATE database
      from the dev DB. It never implicitly targets the dev database.
    * It does nothing destructive without an explicit confirmation. You must
      pass ``--yes`` on the command line (or set the env var RESET_CONFIRM=1).
      Without that confirmation it is a DRY RUN: it prints the target database
      and exits having dropped nothing.
    * It prints the resolved target database URI BEFORE dropping anything, so
      the operator can see exactly which database will be reset.

What it does when confirmed:
    * Drops every table (db.drop_all()) then recreates the empty schema
      (db.create_all()). DDL is auto-committed, so no explicit commit is
      needed. No demo data is inserted here.

What it does NOT do:
    * It does not seed demo data (that is scripts/seed_demo.py).
    * It does not default to or implicitly touch the dev database.

Run it from the project root:

    python scripts/reset_demo.py            # dry run: shows target, drops nothing
    python scripts/reset_demo.py --yes      # resets the TEST database (empty schema)

Advanced (still always requires --yes to actually drop):

    python scripts/reset_demo.py --config app.config.TestConfig --yes   # default target
    python scripts/reset_demo.py --config app.config.DevConfig --yes    # dev DB (explicit opt-in)
"""

import argparse
import os
import sys

# Make the project root importable so "app" resolves even if this script is
# launched directly (e.g. `python scripts/reset_demo.py`). The repo root is the
# parent folder of this scripts/ directory.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import create_app  # noqa: E402  (import after sys.path setup)
from app.extensions import db  # noqa: E402


def parse_args(argv=None):
    """Parse command-line options.

    --config defaults to the TEST database so the common, safe case needs no
    flag. --yes is the explicit confirmation required before anything is
    dropped; without it the script is a harmless dry run.
    """
    parser = argparse.ArgumentParser(
        description="Reset a database to a fresh, EMPTY schema (TEST database by default)."
    )
    parser.add_argument(
        "--config",
        default="app.config.TestConfig",
        help=(
            "Dotted path to the config class selecting the target database. "
            "Defaults to app.config.TestConfig (the TEST database). "
            "Use app.config.DevConfig to target the dev database (still "
            "requires --yes)."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Explicit confirmation to actually drop and recreate the schema. "
            "Without this flag the script only reports the target and exits "
            "(dry run). The env var RESET_CONFIRM=1 has the same effect."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None):
    """Reset the configured database to an empty schema, only when confirmed."""
    args = parse_args(argv)

    # Confirmation may come from the --yes flag OR the RESET_CONFIRM=1 env var.
    confirmed = args.yes or os.environ.get("RESET_CONFIRM") == "1"

    # Build the app with the chosen config. By default this is TestConfig, so
    # the connection string comes from TEST_DATABASE_URL and the dev DB is not
    # touched. No database connection is opened just by creating the app.
    app = create_app(args.config)

    # Resolve the exact target so we can show the operator precisely which
    # database is in scope BEFORE doing anything destructive.
    target_uri = app.config.get("SQLALCHEMY_DATABASE_URI")

    print(f"Config:          {args.config}")
    print(f"Target database: {target_uri}")

    if not confirmed:
        # DRY RUN: no app context is pushed and db.drop_all() is never called,
        # so nothing connects to or changes any database.
        print("")
        print("DRY RUN — nothing was dropped.")
        print("This would DROP ALL TABLES and recreate an empty schema in the")
        print("database shown above.")
        print("Re-run with --yes (or RESET_CONFIRM=1) to actually reset it:")
        print("    python scripts/reset_demo.py --yes")
        return

    # Confirmed: perform the reset inside an app context so the shared db
    # instance knows which database to connect to.
    with app.app_context():
        # Importing the models registers all tables on db.metadata so that
        # drop_all() and create_all() know the full schema.
        import app.models  # noqa: F401  (imported for table registration)

        print("")
        print("Resetting (confirmed)...")

        # Drop every known table, then recreate the empty schema. DDL is
        # auto-committed by the database, so no explicit commit is needed.
        db.drop_all()
        print("  dropped all tables")

        db.create_all()
        print("  recreated empty schema")

        # Report the resulting (empty) tables so the result is visible.
        table_names = sorted(db.metadata.tables.keys())
        print(f"Tables now present ({len(table_names)}), all empty:")
        for name in table_names:
            print(f"  - {name}")

        print("")
        print(f"Done. {target_uri} has been reset to an empty schema.")
        print("No demo data was inserted (run scripts/seed_demo.py for that).")


if __name__ == "__main__":
    main()
