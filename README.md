# Local Buy and Sell (STS Milestone / Phase 1)

A local training app that lets a single preloaded trainee practice buying and
selling fictional stocks with simulated money. It uses a three-tier
architecture: browser → Flask application → MySQL database.

Technology: Python 3, Flask (`create_app` app factory), SQLAlchemy ORM,
MySQL via PyMySQL, Jinja templates, Hypothesis + pytest for tests. Money is
handled as `Decimal` mapped to `DECIMAL(12,2)`, never `float`.

> Status: Task 1 (project scaffolding and configuration) is complete. Models,
> data-access helpers, the trading service, routes, templates, and the
> database scripts arrive in later tasks. Items marked "coming in a later
> task" below are not yet runnable.

## Prerequisites

- Python 3 (developed against Python 3.14).
- A local MySQL server you can connect to.

## 1. Create the databases

Create two MySQL databases — a development database and a SEPARATE test
database — for example:

```sql
CREATE DATABASE sts;
CREATE DATABASE sts_test;
```

The test database is used only by the automated tests; the development
database is never touched by tests.

## 2. Configure environment variables

Copy the example file and fill in your real values:

```bash
cp .env.example .env
```

Then edit `.env` and set:

- `DATABASE_URL` — development DB, e.g. `mysql+pymysql://user:password@localhost:3306/sts`
- `TEST_DATABASE_URL` — SEPARATE test DB, e.g. `mysql+pymysql://user:password@localhost:3306/sts_test`
- `SECRET_KEY` — any long random string

The connection string is fully environment-driven, so moving to a hosted
database later (e.g. AWS RDS) means changing only `DATABASE_URL` — no code
change.

## 3. Create and activate a virtual environment

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
```

## 4. Install dependencies

```bash
python -m pip install -r requirements.txt
```

## 5. Initialize and seed the database (coming in a later task)

The database schema and demo data are created by explicit scripts (never on
app startup). These scripts are added in a later task and will be run like:

```bash
python scripts/init_db.py    # create the empty schema
python scripts/seed_demo.py  # load the demo user, account, and stocks
```

## 6. Run the application

The app is built by the `create_app` factory in `app/__init__.py`. Once the
routes are added in a later task, run it with Flask:

```bash
# Windows (PowerShell)
$env:FLASK_APP = "app:create_app"
flask run

# macOS / Linux
FLASK_APP="app:create_app" flask run
```

## 7. Run the tests

Tests run against the SEPARATE test database (`TEST_DATABASE_URL`) and never
touch the development database. The test suite is added in later tasks.

```bash
pytest
```

## Project layout

```
app/
  __init__.py       # create_app() app factory, binds db, registers blueprints
  config.py         # DevConfig / TestConfig read from environment variables
  extensions.py     # the single SQLAlchemy instance (db)
  services/         # trading service (later task)
  routes/           # GET and POST routes (later task)
requirements.txt    # pinned dependency versions
.env.example        # variable names + placeholders, no real secrets
```
