"""Create the empty database schema from the SQLAlchemy models.

This is an EXPLICIT, standalone script. The application never creates,
resets, or seeds the database on startup (see app/__init__.py and
Requirement 11.4) so that saved results survive restarts. Schema creation is
a deliberate step you run yourself.

What it does:
    * Builds the app with the DevConfig app factory.
    * Pushes an application context so the single SQLAlchemy instance knows
      which app (and therefore which DATABASE_URL) to use.
    * Imports the models so every table is registered on the shared metadata.
    * Calls db.create_all() to create any MISSING tables. create_all() is
      idempotent: it never drops existing tables and never touches data, so
      running it twice is safe.

What it does NOT do:
    * It does not drop tables (that is scripts/reset_demo.py).
    * It does not insert any demo data (that is scripts/seed_demo.py).

Run it from the project root:

    python scripts/init_db.py
"""

import os
import sys

# Make the project root importable so "app" resolves even if this script is
# launched directly (e.g. `python scripts/init_db.py`). The repo root is the
# parent folder of this scripts/ directory.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import create_app  # noqa: E402  (import after sys.path setup)
from app.extensions import db  # noqa: E402


def main():
    """Create the empty schema in the database named by DATABASE_URL."""
    # Use the development configuration by default. The connection string
    # comes entirely from DATABASE_URL via DevConfig.
    app = create_app("app.config.DevConfig")

    # An app context binds the shared db instance to this app, so create_all()
    # knows which database to connect to.
    with app.app_context():
        # Importing the models registers all seven tables on db.metadata.
        # create_app already attempts this import, but we import again here so
        # the intent is explicit and this script is correct on its own.
        import app.models  # noqa: F401  (imported for table registration)

        # Create only the tables that do not already exist. No data is added
        # and no existing table is dropped.
        db.create_all()

        # Report exactly which tables were created or ensured so the student
        # running this can see the result at a glance.
        table_names = sorted(db.metadata.tables.keys())
        print("Schema is ready in the database identified by DATABASE_URL.")
        print(f"Tables created or ensured ({len(table_names)}):")
        for name in table_names:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
