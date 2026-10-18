# Verification Report — Local Buy and Sell (STS Milestone / Phase 1)

**Task:** 11.1 — Run the full verification pass and produce the report
**Spec:** `.kiro/specs/local-buy-and-sell/`
**Date of run:** verification pass executed on Windows (PowerShell), Python 3.14.7
**Requirements covered:** 8.14, 8.15, 11.3, 11.4, 12.1–12.3, 13.1–13.5, 14.1–14.5, 16.1–16.6, 17.1–17.5

---

## 1. Summary

The full implementation was verified end-to-end. The automated suite passes
in full, the two exact milestone scenarios produce the required database
values, the hand-written SQL in `app/db.py` uses bound parameters only, and
the web flow (review → confirm → confirmation, plus cancel) behaves correctly
with GET never changing data and a refresh never re-executing a trade.

| Area | Result |
|------|--------|
| Automated test suite | **59 passed, 0 failed, 0 skipped** |
| Milestone BUY (5 DEMO1) | **Pass** — cash `9750.00`, position `5`, inventory `995`, COMPLETED, 1 Transaction + 1 EventLog |
| Milestone SELL (2 DEMO1) | **Pass** — cash `9850.00`, position `3`, inventory `997`, COMPLETED, 1 Transaction + 1 EventLog |
| Bound-parameter audit of `app/db.py` | **Pass** — only one raw statement, fully parameterized |
| Web pages (test client) | **Pass** — GET read-only, PRG confirm, refresh does not re-execute, cancel changes nothing |
| Persistence across restart | **Pass** — covered by `test_concurrency.py::test_saved_results_survive_simulated_restart` |

### Database backend used (and its limitations)

**MySQL was not reachable in this environment.** A probe of `127.0.0.1:3306`
timed out (no local MySQL server running), so the MySQL-backed path documented
in the README could not be exercised here.

To still run the automated suite and the scenario/web verification, a
**throwaway SQLite database** was used (allowed by the task). This has known
limitations relative to the production MySQL target:

- **`SELECT ... FOR UPDATE` is a no-op on SQLite.** SQLite parses the clause
  but does not take real row locks; it serializes writes with a database-level
  lock instead. The concurrency guarantee in production rests on MySQL row
  locks plus the atomic conditional claim (`UPDATE ... WHERE status='PENDING'`).
  On SQLite the *idempotency* guard (the conditional claim) still works and is
  what the concurrency tests assert against (they use a deterministic
  sequential double-confirm, not a true thread race), so the tests remain
  meaningful; but true row-lock contention is **not** exercised on SQLite.
- **Decimal storage differs.** MySQL `DECIMAL(12,2)` stores exact base-10
  values; SQLite has no native decimal type. SQLAlchemy's `Numeric(12,2)`
  type adapter still returns Python `Decimal` values, and all money assertions
  (e.g. `9750.00`, `250.00`) compared exactly, so no rounding drift was
  observed — but exact on-disk decimal semantics should be re-confirmed once
  MySQL is available.
- These limitations affect *confidence about MySQL-specific behavior*, not the
  correctness of the application logic, which is backend-agnostic.

> Recommendation: when a MySQL server is available, re-run the suite against a
> real `sts_test` database (set `TEST_DATABASE_URL` to the MySQL URL) to
> confirm `FOR UPDATE` locking and exact `DECIMAL` storage under contention.

---

## 2. Setup and run commands (from `README.md`)

The commands below are the documented setup/run/test steps. The MySQL
`CREATE DATABASE` and `.env` values are the intended production-local path;
the SQLite note shows exactly what was substituted here because MySQL was not
reachable.

### 2.1 Create the databases (MySQL — intended path)

```sql
CREATE DATABASE sts;
CREATE DATABASE sts_test;
```

### 2.2 Configure environment

```bash
cp .env.example .env
```

Then set in `.env`:

- `DATABASE_URL` — e.g. `mysql+pymysql://user:password@localhost:3306/sts`
- `TEST_DATABASE_URL` — e.g. `mysql+pymysql://user:password@localhost:3306/sts_test`
- `SECRET_KEY` — a long random string

**Substitution used in this run (MySQL unavailable):** `DATABASE_URL` and
`TEST_DATABASE_URL` were pointed at throwaway SQLite files, e.g.
`sqlite:///C:/.../_verify_dev.db` and a per-run file under the system temp
directory for the test DB. These throwaway files were removed afterward.

### 2.3 Create a virtual environment and install dependencies

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

(All pinned dependencies from `requirements.txt` — Flask 3.1.3,
Flask-SQLAlchemy 3.1.1, SQLAlchemy 2.1.4, PyMySQL 1.2.3, python-dotenv 1.2.4,
pytest 9.1.1, hypothesis 6.170.0 — were already importable in the environment.)

### 2.4 Initialize and seed the database

```bash
python scripts/init_db.py    # create the empty schema (7 tables)
python scripts/seed_demo.py  # load the demo user, account, and stocks
```

Observed output:

- `init_db.py` → *"Schema is ready … Tables created or ensured (7)"*: `accounts,
  event_logs, orders, positions, stocks, transactions, users`.
- `seed_demo.py` → Account `id=1`, `cash_balance=$10000.00`,
  `starting_balance=$10000.00`; stocks `DEMO1 $50.00/1000`, `DEMO2 $25.00/500`,
  `DEMO3 $120.00/300`; starter Position of `10` shares of `DEMO2`; `0` DEMO1
  shares (no Position row), exactly as Requirement 1 specifies.

### 2.5 Run the application

```powershell
# Windows (PowerShell)
$env:FLASK_APP = "app:create_app"
flask run
```

```bash
# macOS / Linux
FLASK_APP="app:create_app" flask run
```

### 2.6 Run the tests

```bash
pytest
```

> Note for SQLite only: because the suite was designed for MySQL and the
> shared Flask-SQLAlchemy session resets the schema between every test
> (`drop_all` + `create_all`), running the whole suite in a single process on
> a **file-backed SQLite** database hits a SQLite DDL-visibility quirk (see
> Section 4). Running each test file in its own process (fresh engine + fresh
> DB file) sidesteps it. On MySQL, a plain `pytest` is the correct command and
> no per-file isolation is needed.

---

## 3. Automated test results

Command (per-file isolation on SQLite, fresh DB file per file):

```powershell
# For each test file, with a fresh throwaway SQLite DB:
$env:TEST_DATABASE_URL = "sqlite:///<fresh temp file>.db"
python -m pytest <file> -q
```

**Totals: 59 passed, 0 failed, 0 skipped.** Only benign
`DeprecationWarning: datetime.datetime.utcnow()` warnings were emitted (see
Section 6).

| Test file | Passed | Failed | Skipped | Notes |
|-----------|-------:|-------:|--------:|-------|
| `tests/test_trading_errors.py` | 17 | 0 | 0 | Typed errors + exact messages (no DB). |
| `tests/test_validation.py` | 13 | 0 | 0 | `prepare_order` checks: invalid qty set, cash, inventory, owned shares, missing stock/account. |
| `tests/test_properties_review.py` | 6 | 0 | 0 | Properties 1, 2, 4, 5, 6, 7 (Hypothesis). |
| `tests/test_trading_buy.py` | 1 | 0 | 0 | **Milestone BUY** scenario (Req 13). |
| `tests/test_trading_sell.py` | 1 | 0 | 0 | **Milestone SELL** scenario (Req 14). |
| `tests/test_execute_edge_cases.py` | 6 | 0 | 0 | Full-holding sale, repeat confirm, price change, mid-trade rollback, cancelled/failed → no txn. |
| `tests/test_properties_execute.py` | 7 | 0 | 0 | Properties 8, 9, 10, 11, 12, 13, 14 (Hypothesis). |
| `tests/test_cancel.py` | 5 | 0 | 0 | Cancel sets CANCELLED, no state change; Property 3. |
| `tests/test_concurrency.py` | 3 | 0 | 0 | Competing confirmations, refresh no re-exec, **simulated restart persistence**. |

### Which tests could not run on the available backend, and why

- No test was *logically* skipped on its merits. When the whole suite is run
  in a **single process on file-backed SQLite**, the 42 database-backed tests
  **error at setup** (not fail) with
  `sqlite3.OperationalError: table "transactions" already exists`. This is the
  SQLite `drop_all`/`create_all` DDL-visibility limitation described in
  Section 4 — a test-harness/SQLite interaction, **not** a product defect.
- Running each file in its own process (fresh interpreter, fresh engine, fresh
  DB file) removes the accumulated session state and all 59 tests pass.
- The project's `tests/conftest.py` is written to **skip** (not fail)
  DB-backed tests when the test database is unreachable. In an environment with
  the stale/unreachable MySQL `TEST_DATABASE_URL` still set, those 42 tests
  skip cleanly; pointing `TEST_DATABASE_URL` at a reachable DB runs them.

---

## 4. Known limitation observed: SQLite schema reset in a single process

**Symptom:** running `pytest` for the entire suite in one process against a
file-backed SQLite test DB produced `17 passed, 42 errors`, every error being
`sqlite3.OperationalError: table "transactions" already exists` raised from
`db.create_all()` inside the per-test `reset_db` fixture.

**Cause:** the suite was designed for MySQL. The session-scoped `_schema`
fixture builds the schema once, and the per-test `reset_db` fixture does
`drop_all` + `create_all` before each test using the shared Flask-SQLAlchemy
scoped session. On a file-backed SQLite database, the DDL from `drop_all` is
not always visible to the subsequent `create_all` within the same pooled
connection state, so `create_all` tries to recreate a table that still exists.
This is a SQLite + connection-pool interaction, not a defect in the application
or in the trading logic.

**Effect:** none on correctness. The identical tests pass when each file runs
in its own process (fresh engine), and the scenario/web driver in Section 5
exercises the same `prepare_order`/`confirm_order`/`cancel_order` code paths
cleanly.

**Suggested fix (future, test-infra only — no product change):** on MySQL this
does not occur, so the simplest fix is to run the suite against MySQL as
designed. If SQLite support for the suite is wanted, dispose/recreate the
engine (or use a fresh in-memory DB with a shared cache) between resets, or run
`drop_all`/`create_all` on an explicit fresh connection. No application code
needs to change.

---

## 5. Milestone scenario evidence (actual before/after DB values)

These values were produced by driving the real `create_app` application and
the trading service against a fresh seeded database, reading every value back
from the database (not from screen text). They match the pytest milestone
tests `test_buy_5_demo1_exact_scenario` and `test_sell_2_demo1_exact_scenario`.

### 5.1 BUY 5 DEMO1 @ $50.00 (Requirement 13)

| Field | Before | After | Required (Req 13) |
|-------|-------:|------:|------------------:|
| Account cash | `10000.00` | **`9750.00`** | `9750.00` ✓ |
| DEMO1 position (shares) | `0` (no row) | **`5`** | `5` ✓ |
| DEMO1 inventory (available_shares) | `1000` | **`995`** | `995` ✓ |
| Order status | — | **`COMPLETED`** | COMPLETED ✓ |
| Transactions created | `0` | **`1`** (total `250.00`, exec price `50.00`) | exactly 1 ✓ |
| EventLogs created | `0` | **`1`** | exactly 1 ✓ |

Review step evidence: `prepare_order` returned `total = 250.00` and
`expected_cash = 9750.00`, created exactly one `PENDING` order, and left cash
at `10000.00` (no change at review) — confirming Pre-Execution Checks run at
review without mutating state (Req 6.1, 8.1).

### 5.2 SELL 2 DEMO1 @ $50.00 while owning 5, cash $9,750.00 (Requirement 14)

| Field | Before | After | Required (Req 14) |
|-------|-------:|------:|------------------:|
| Account cash | `9750.00` | **`9850.00`** | `9850.00` ✓ |
| DEMO1 position (shares) | `5` | **`3`** | `3` ✓ |
| DEMO1 inventory (available_shares) | `995` | **`997`** | `997` ✓ |
| Order status | — | **`COMPLETED`** | COMPLETED ✓ |
| Transactions (running total) | `1` | **`2`** | +1 (exactly one sale) ✓ |
| EventLogs (running total) | `1` | **`2`** | +1 (exactly one) ✓ |

Both scenarios confirm the all-or-nothing execution applies cash, position,
inventory, order status, the single Transaction, and the single EventLog
together (Req 10.1–10.11), and that sold shares are returned to inventory
(Req 10.7, 14.3).

---

## 6. Bound-parameter audit of `app/db.py` (Req 8.14, 8.15, 12.x)

A search of the entire `app/` package found exactly **one** hand-written SQL
statement, in `lock_order_if_pending`:

```python
result = session.execute(
    text(
        "UPDATE orders SET status = :executing "
        "WHERE id = :oid AND status = :pending"
    ),
    {
        "executing": ORDER_STATUS_EXECUTING,
        "oid": order_id,
        "pending": ORDER_STATUS_PENDING,
    },
)
```

Findings:

- The statement uses **named bound parameters** (`:executing`, `:oid`,
  `:pending`) with values passed in a parameter dict. There is **no f-string,
  `%`-formatting, `.format()`, or string concatenation** of user input into
  SQL.
- The status literals come from the `ORDER_STATUS_*` constants in
  `app/models.py`, not from request data.
- Every other data-access helper (`get_demo_account`, `get_stock`,
  `get_position`, `get_order_for_update`, `upsert_position`,
  `add_transaction`, `add_event_log`) uses the SQLAlchemy ORM query API
  (`session.query(...).filter(...)`, `session.get(...)`, `with_for_update()`,
  `session.add`, `session.delete`), which parameterizes automatically.
- The `app.db` helpers never call `commit()`/`rollback()`; the trading service
  owns the single transaction boundary — confirmed by reading `app/db.py` and
  `app/services/trading.py`.
- Note: `confirm_order` builds the EventLog *description* with an f-string
  (`f"{action} {quantity} {stock.ticker} @ {execution_price} ..."`). This is a
  plain Python string stored via the ORM as a column value — it is **not** SQL
  and is not an injection vector.

**Conclusion: all database access uses bound parameters. Pass.**

---

## 7. Web-page exercise (Flask test client)

The real application was built with `create_app` and driven through the Flask
test client against a fresh seeded database. All values were read back from the
database.

| Step | Result |
|------|--------|
| `GET /dashboard` | 200; account id, cash, holdings rendered |
| `GET /stocks` | 200; stock list rendered |
| `GET /stocks/<id>` | 200; details + buy/sell forms rendered |
| GETs change no data | ✓ full DB snapshot identical before/after all three GETs (Req 5.6) |
| `POST /orders/review` | 200; exactly one new `PENDING` order created (Req 6.1) |
| `POST /orders/<id>/confirm` | **302 redirect** (Post/Redirect/Get) to `/orders/<id>/confirmation`; cash applied; order `COMPLETED` (Req 11.1, 12.1) |
| `GET /orders/<id>/confirmation` | 200; shows saved DB values |
| `GET …/confirmation` again (refresh) | 200; **no state change, no new Transaction** — refresh does not re-execute (Req 11.3, 17.3) |
| `POST /orders/<id>/cancel` | **302 redirect**; order `CANCELLED`; cash, positions, inventory, transactions unchanged (Req 7) |

This confirms: every record-changing action uses POST; GET is read-only; the
confirmation page uses Post/Redirect/Get so a browser refresh re-runs only a
GET; and cancel leaves the world unchanged.

---

## 8. Persistence across application restart (Req 11.4)

Restart durability is proven by
`tests/test_concurrency.py::test_saved_results_survive_simulated_restart`
(**passed**). After a confirmed buy it expires the ORM identity map, disposes
the SQLAlchemy engine (dropping every pooled connection, as a real process
restart would), opens a brand-new application context, and re-reads every
record: cash balance, position, inventory, order status, the single
Transaction, and the single EventLog all match the saved results. Because the
app factory never seeds or resets on startup (schema/seed are explicit
scripts), committed rows survive a restart. **Confirmed.**

---

## 9. Remaining problems and limitations

| # | Item | Effect | Suggested fix |
|---|------|--------|---------------|
| 1 | **MySQL not available in this environment** | Could not exercise the production MySQL path; verified on throwaway SQLite instead. `FOR UPDATE` row locking and exact on-disk `DECIMAL` were not exercised under real contention. | Start a local MySQL (or point `TEST_DATABASE_URL`/`DATABASE_URL` at a reachable MySQL / AWS RDS) and re-run `pytest`. No code change — the connection string is fully env-driven. |
| 2 | **Whole-suite `pytest` errors on file-backed SQLite** | `17 passed, 42 errors` in a single process because `drop_all`+`create_all` between tests hits a SQLite DDL-visibility quirk. Not a product defect; all 59 pass with per-file process isolation or on MySQL. | Run on MySQL as designed; or (test-infra only) dispose/recreate the engine between resets, or use a shared-cache in-memory SQLite for the suite. |
| 3 | **`datetime.datetime.utcnow()` deprecation** | Benign `DeprecationWarning` on Python 3.12+ (seen at `app/services/trading.py:475` and in model `default=`). No functional impact today; `utcnow()` is scheduled for future removal. | Switch to timezone-aware `datetime.now(datetime.UTC)` (and store/compare consistently) in a future cleanup. Out of scope for this milestone. |
| 4 | **Concurrency tests model a race sequentially** | On SQLite a true two-thread race would serialize on the DB lock; the tests use a deterministic sequential double-confirm to assert the idempotency guard. Real multi-connection contention is only truly exercised on MySQL. | Re-run the concurrency tests against MySQL to confirm row-lock behavior under genuine parallel requests. |

No defects were found in the application or trading logic. No application or
test code was modified during verification.

---

## 10. Short demo walkthrough

### (a) Setup / database

1. Create the two MySQL databases: `CREATE DATABASE sts; CREATE DATABASE sts_test;`
   (or point `DATABASE_URL` at any reachable database).
2. `cp .env.example .env` and fill in `DATABASE_URL`, `TEST_DATABASE_URL`,
   `SECRET_KEY`.
3. `python -m pip install -r requirements.txt`.
4. `python scripts/init_db.py` → creates the 7 empty tables.
5. `python scripts/seed_demo.py` → loads the trainee, the practice Account with
   `$10,000.00`, DEMO1 (`$50.00`, 1,000 shares), DEMO2 (`$25.00`, 500 shares)
   with a starter Position of 10 shares, and DEMO3 (`$120.00`, 300 shares).
6. Start the app: set `FLASK_APP=app:create_app` and run `flask run`.
7. Open the Dashboard — it shows Account id, cash `$10,000.00`, and the DEMO2
   holding.

### (b) Buying

1. From the Stock List, open **DEMO1**'s details page (price `$50.00`, 1,000
   available, 0 owned).
2. In the Buy form enter `5` and submit → the Order Review page shows company,
   ticker, Buy, 5 shares, `$50.00`/share, total `$250.00`, and expected cash
   `$9,750.00`. One `PENDING` order now exists; nothing else changed.
3. Click **Confirm** → the app executes the trade in one transaction and
   redirects (PRG) to the Confirmation page, which shows the saved cash
   `$9,750.00`. The database now has: cash `9750.00`, DEMO1 position `5`,
   DEMO1 inventory `995`, the order `COMPLETED`, one Transaction, one EventLog.
4. Refresh the Confirmation page → the same saved values display and the trade
   does **not** execute again (GET only).

### (c) Selling

1. Open **DEMO1**'s details page again — it now shows 5 shares owned.
2. In the Sell form enter `2` and submit → the Review page shows Sell, 2
   shares, `$50.00`/share, total proceeds `$100.00`, and expected cash
   `$9,850.00`.
3. Click **Confirm** → the database now has: cash `9850.00`, DEMO1 position
   `3`, DEMO1 inventory `997` (sold shares returned to inventory), the order
   `COMPLETED`, and a second Transaction and EventLog.
4. (Optional) Selling the entire remaining position removes the Position row;
   cancelling a pending order instead returns to the details page and changes
   nothing.

---

*Prepared for spec task 11.1. Verification used a throwaway SQLite database
because MySQL was not reachable; all throwaway databases and temporary scripts
created during verification were removed afterward (this report is the only
retained artifact).*
