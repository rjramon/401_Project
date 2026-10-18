# Design Document

## Overview

This design describes Milestone / Phase 1 of the Stock Trading System (STS): a working local buy-and-sell workflow for a single preloaded trainee. It implements the approved `requirements.md` using a classic three-tier architecture that students can explain line by line.

Goals of this design:

- One trainee, one practice account, fixed-price fictional stocks, whole-share trades.
- A guided flow: Dashboard → Stock List → Stock Details → Order Review → Confirmation.
- Every record-changing action validated at review **and** again at confirmation, reading trusted values from the database.
- Each completed trade applied as **one all-or-nothing database transaction**.
- Correct balances after page refresh, after restart, and under repeated or competing confirmations.

Non-goals (deferred, per requirements): authentication, deposits/withdrawals, admin tools, market hours, automatic pricing, multiple accounts, and cloud deployment. The database connection is made configurable now so a later move to AWS RDS needs no code change, but no cloud work is done in this milestone.

### Technology choices

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3 | Required; readable for students. |
| Web framework | Flask with an app factory (`create_app`) | Required; the factory keeps config/testing clean. |
| Templating | Jinja server-rendered HTML + CSS + a little JS | Required; keeps presentation on the server. |
| ORM | SQLAlchemy | Required; bound parameters by default, maps cleanly to MySQL. |
| Database | MySQL (local now, AWS RDS later) | Required; connection string from env var. |
| Money type | Python `Decimal` ↔ SQLAlchemy `Numeric` ↔ MySQL `DECIMAL(12,2)` | Exact decimal math; never float for money. |

## Architecture

### Three-tier structure

```mermaid
flowchart LR
    subgraph Presentation [Presentation tier - Browser]
        B[HTML pages, CSS, small JS]
    end
    subgraph Application [Application tier - Flask]
        R[Routes / Blueprints]
        S[Services: trading.py]
        D[Data access: db.py]
        M[Models: models.py]
    end
    subgraph Data [Data tier - MySQL]
        DB[(STS database)]
    end

    B -- HTTP GET/POST --> R
    R -- plain params --> S
    S -- read/write helpers --> D
    D -- SQLAlchemy session --> M
    M -- SQL w/ bound params --> DB
    R -- rendered HTML --> B
```

**Rule enforced by this design:** the browser talks only to Flask. It never opens a MySQL connection and never sends trusted values (price, balance, ownership, inventory). The server reads those from the database (Req 8.14, 8.15).

### Layer responsibilities

- **Routes (presentation glue).** Parse the HTTP request, pull form values as plain strings, call a service function with plain parameters, then render a template or redirect. Routes contain **no trading rules**.
- **Services (`app/services/trading.py`).** All buy/sell/trading-check logic. Functions take plain parameters (account id, stock id, action, quantity string, order id) and return plain result objects or raise typed exceptions. They do **not** import Flask `request`, and they do **not** render pages. This makes them unit-testable without a web server (Req 8, 10).
- **Data access (`app/db.py`).** Thin query/update helpers over the SQLAlchemy session (fetch account, fetch stock, fetch/lock order, upsert position, etc.). These helpers **do not commit**. The service owns the transaction boundary so that the whole trade commits or rolls back together (Req 10.11, 10.12).
- **Models (`app/models.py`).** Table definitions and relationships.

### Transaction boundary ownership

Only the trading service calls `session.commit()` / `session.rollback()`. `db.py` helpers add, update, delete, flush, and query — but never commit a single part on their own. This guarantees the six changes of a completed trade (cash, Position, Stock inventory, Order status, Transaction, EventLog) land in **one** transaction.

### Configuration

- `create_app(config)` reads settings from environment variables.
- `DATABASE_URL` (e.g. `mysql+pymysql://user:pass@localhost:3306/sts`) is the only thing that changes between local MySQL and AWS RDS later.
- `SECRET_KEY` and DB credentials come from the environment, never committed. A `.env.example` lists variable names with placeholder values and **no real secrets**.

## File and Project Structure

```
401 project/
├─ app/
│  ├─ __init__.py            # create_app() app factory, registers blueprints + db
│  ├─ config.py              # Config classes read from env vars (Dev / Test)
│  ├─ extensions.py          # db = SQLAlchemy() created once, bound in create_app
│  ├─ models.py              # Tables + relationships (User, Account, Stock, Order, Transaction, Position, EventLog)
│  ├─ db.py                  # Query/update helpers; NO commits
│  ├─ services/
│  │  ├─ __init__.py
│  │  └─ trading.py          # prepare_order(), confirm_order(), cancel_order(), checks
│  ├─ routes/
│  │  ├─ __init__.py
│  │  ├─ main.py             # dashboard, stock list, stock details (GET)
│  │  └─ orders.py           # review (POST), confirm (POST), cancel (POST)
│  ├─ templates/
│  │  ├─ base.html
│  │  ├─ dashboard.html
│  │  ├─ stock_list.html
│  │  ├─ stock_details.html
│  │  ├─ order_review.html
│  │  └─ confirmation.html
│  └─ static/
│     ├─ style.css
│     └─ app.js              # small helpers (e.g., disable Confirm after click)
├─ scripts/
│  ├─ init_db.py             # create empty schema from models (explicit)
│  ├─ seed_demo.py           # load demo user, account, stocks, starter position
│  └─ reset_demo.py          # explicit reset of the DEMO/TEST db only
├─ tests/
│  ├─ conftest.py            # test app + SEPARATE test MySQL database
│  ├─ test_trading_buy.py
│  ├─ test_trading_sell.py
│  ├─ test_validation.py
│  └─ test_concurrency.py
├─ requirements.txt          # pinned versions
├─ .env.example              # variable names only, no secrets
└─ README.md                 # setup + run + test instructions
```

Why this split: it matches the mandated separation (models in `models.py`, queries in `db.py`, trading in `services/trading.py`) and keeps each file small enough for a student to read and defend in a design review.

## Components and Interfaces

### Service interface (`app/services/trading.py`)

These functions are Flask-free and page-free. They accept plain parameters and return plain results or raise typed exceptions.

```python
# Typed errors carry the exact user-facing message.
class TradeError(Exception):
    """Base class; .message is the plain text shown to the trainee."""

class InvalidQuantity(TradeError):     # "Enter a positive whole number."
class NotEnoughMoney(TradeError):      # "Not enough money."
class NotEnoughShares(TradeError):     # "Not enough shares." (sell)
class NotEnoughInventory(TradeError):  # "Not enough shares available to buy."
class OrderNotPending(TradeError):     # "This order has already been completed or cancelled."
class PriceUnavailable(TradeError):    # price-unavailable message (DB price <= 0 or missing)
class InvalidTarget(TradeError):       # missing stock/account or order owned by another account
class PriceChanged(TradeError):        # DB price != reviewed price -> require new review


def prepare_order(account_id, stock_id, action, quantity_raw):
    """Pre-execution checks (Req 8), then create a PENDING Order.
    Returns an OrderReview (plain data) for the review page.
    Raises a TradeError on any failed check. Makes NO balance/position/inventory change."""

def confirm_order(account_id, order_id):
    """Re-run checks against trusted DB values, verify reviewed price still matches
    DB price, then apply ALL changes in ONE transaction and set Order COMPLETED.
    Idempotent: a second call on a non-PENDING order raises OrderNotPending.
    On any failure: rollback, leave Order PENDING (except invalid-status cases).
    Returns a TradeResult with saved balances."""

def cancel_order(account_id, order_id):
    """PENDING -> CANCELLED. No cash/position/inventory/transaction change (Req 7)."""
```

`OrderReview` and `TradeResult` are plain dataclasses (company, ticker, action, quantity, price, total, expected/actual cash) so routes and templates never touch ORM internals and tests can assert on plain values.

### Data-access interface (`app/db.py`) — no commits

```python
def get_demo_account(session):                 # backend-selected identity (Req 1)
def get_stock(session, stock_id):
def get_position(session, account_id, stock_id):
def get_order_for_update(session, order_id):    # SELECT ... FOR UPDATE row lock
def lock_order_if_pending(session, order_id):    # conditional UPDATE status PENDING->(locked)
def upsert_position(session, account_id, stock_id, delta):  # create/increase/decrease; delete at 0
def add_transaction(session, ...):
def add_event_log(session, ...):
# All helpers add/flush/query only. The SERVICE commits or rolls back.
```

### Route interface

| Route | Method | Purpose | Changes DB? |
|---|---|---|---|
| `/` or `/dashboard` | GET | Dashboard: account, cash, holdings | No |
| `/stocks` | GET | Stock list | No |
| `/stocks/<id>` | GET | Stock details + buy/sell forms | No |
| `/orders/review` | POST | `prepare_order()` → render review | Creates PENDING Order |
| `/orders/<id>/confirm` | POST | `confirm_order()` → **redirect** to confirmation | Executes trade |
| `/orders/<id>/cancel` | POST | `cancel_order()` → redirect to details | Cancels Order |
| `/orders/<id>/confirmation` | GET | Confirmation page (saved values) | No |

GET never changes data; POST performs record-changing actions (Req 5.5, 5.6). Confirmation uses **Post/Redirect/Get**: the confirm POST redirects to a GET confirmation page, so refreshing the confirmation page re-runs only a GET and never re-executes the trade (Req 11.3, 12.1).

## Data Models

### One Consistent Model — SAD mapping and review note

The team model is adopted as the single source of truth for this milestone. The table below records each delta from the Systems Analysis and Design (SAD) paper so reviewers can see exactly where this design intentionally differs. **The team must agree on this one model during the design review before any tables are created.** There are no duplicate balances and no parallel `Holdings` + `Position` entities.

| Area | SAD paper | This milestone (one model) | Delta / rationale |
|---|---|---|---|
| Cash location | Cash on `Users` | Cash on `Account` | Moved to Account; one balance only, no duplication. |
| Ownership entity | `Holdings` | `Position` | Replaces Holdings; exactly one Position per (account, stock). Holdings is **not** created. |
| Review flow | No order entity | `Order` added | Supports PENDING → COMPLETED/CANCELLED review/confirm flow. |
| Roles | `Administrators` table | `role` column on `User` | Admin vs trainee is a role; Administrators table deferred. |
| Scheduling | `Market_Schedule` table | (deferred) | Not needed for fixed-price local trading. |
| Logging | `Event_Log` | `EventLog` | Same concept; one entry per completed trade. |

**Tables created in this milestone only:** `User`, `Account`, `Stock`, `Order`, `Transaction`, `Position`, `EventLog`. `Administrators` and `Market_Schedule` are deferred and not created.

### Entity relationships

```mermaid
erDiagram
    USER ||--o{ ACCOUNT : owns
    USER ||--o{ EVENTLOG : performs
    ACCOUNT ||--o{ ORDER : places
    ACCOUNT ||--o{ TRANSACTION : has
    ACCOUNT ||--o{ POSITION : holds
    STOCK ||--o{ ORDER : referenced_by
    STOCK ||--o{ TRANSACTION : referenced_by
    STOCK ||--o{ POSITION : referenced_by
    ORDER ||--|| TRANSACTION : completes_into

    USER {
        int id PK
        string name
        string email
        string password_hash
        string role
    }
    ACCOUNT {
        int id PK
        int user_id FK
        decimal cash_balance
        decimal starting_balance
    }
    STOCK {
        int id PK
        string company_name
        string ticker UK
        decimal current_price
        int available_shares
    }
    ORDER {
        int id PK
        int account_id FK
        int stock_id FK
        string action
        int quantity
        decimal reviewed_price
        decimal total
        string status
        datetime created_at
        datetime completed_at
    }
    TRANSACTION {
        int id PK
        int order_id FK
        int account_id FK
        int stock_id FK
        string action
        int quantity
        decimal execution_price
        decimal total_amount
        datetime completed_at
    }
    POSITION {
        int id PK
        int account_id FK
        int stock_id FK
        int quantity
    }
    EVENTLOG {
        int id PK
        int user_id FK
        string event_type
        string description
        datetime created_at
    }
```

Relationships supported (per requirements):

- One `User` has many `Account` rows (this milestone uses one).
- One `Account` has many `Order`, `Transaction`, and `Position` rows.
- One `Stock` appears in many `Order`, `Transaction`, and `Position` rows.
- Each `Position` belongs to one `Account` and one `Stock`; **unique (account_id, stock_id)** enforces one Position per pair.
- Each completed `Order` has exactly one `Transaction` (**unique order_id** on Transaction).
- Each trade `EventLog` connects to the `User` who performed it.

### Money precision

- Cash and totals: **`DECIMAL(12,2)`** (SQLAlchemy `Numeric(12, 2)`).
- Prices: **`DECIMAL(12,2)`** (SQLAlchemy `Numeric(12, 2)`).
- In Python, money is always `Decimal`, never `float`.

Justification: `DECIMAL(12,2)` stores exact values to the cent. Twelve total digits allow values up to 9,999,999,999.99 — far beyond this training app's $10,000.00 scale — while two fractional digits match U.S. cents and the fixed per-share prices. Using the same scale for price and money keeps `price * quantity` exact at two decimals (`quantity` is an integer), so no rounding ambiguity arises. Float is avoided because binary floating point cannot represent values like `0.10` exactly, which would corrupt balances over repeated trades.

### Key constraints

- `Stock.ticker` — unique.
- `Position (account_id, stock_id)` — unique composite; a zero-quantity Position is deleted rather than kept at 0 (Req 10.5, 17.1).
- `Transaction.order_id` — unique (guarantees at most one Transaction per Order; Req 10.9).
- `Order.status` — one of `PENDING`, `COMPLETED`, `CANCELLED`.
- Monetary columns — `Numeric(12, 2)`.

## Page Flow

```mermaid
flowchart TD
    DASH[Dashboard GET] --> LIST[Stock List GET]
    LIST --> DET[Stock Details GET]
    DET -- POST buy/sell form --> REV[Order Review - PENDING Order created]
    REV -- POST Confirm --> EXE{confirm_order}
    EXE -- success --> RED[(Redirect PRG)] --> CONF[Confirmation GET - saved values]
    EXE -- price changed --> DET
    EXE -- check fails --> REV
    REV -- POST Cancel --> DET
    CONF -- refresh = GET only --> CONF
```

What each page shows and how the trainee interacts:

1. **Dashboard** (GET) — the demo account id, current cash balance, and current holdings (each Position with stock and shares owned). All values read from the database (Req 2).
2. **Stock List** (GET) — every stock's company name, ticker, current price, and available shares (Req 3).
3. **Stock Details** (GET) — the selected stock's name, ticker, current price, shares the account owns, and a **buy form** and a **sell form** (Req 4). (The SAD wireframes lacked buy controls; this page adds them.)
4. **Order Review** (reached by POST) — the proposed trade: company + ticker, action, shares, price per share, total cost (buy) or proceeds (sell), expected cash after, plus **Confirm** and **Cancel** controls (Req 6). This page is created from a server-side PENDING Order, not from browser-supplied numbers.
5. **Confirmation** (GET after redirect) — the completed trade and the **actual saved** cash balance and holdings read from the database (Req 11).

Opening or refreshing any page is a GET and never executes a trade (Req 5.6). The Confirm action is a POST that redirects to the confirmation GET (Post/Redirect/Get), so a refresh on the confirmation page is safe (Req 11.3).

## Trading Logic (`app/services/trading.py`)

### prepare_order (review step)

1. Resolve trusted records from the DB: demo account, stock, current position.
2. Run Pre-Execution Checks (Req 8):
   - account exists and belongs to the demo user; stock exists (`InvalidTarget` otherwise);
   - action is `buy` or `sell`;
   - quantity parses to a **positive integer** (`InvalidQuantity` otherwise — covers blank, zero, negative, fractional, nonnumeric; Req 15);
   - DB price `> 0` (`PriceUnavailable` otherwise; Req 5.4, 8.8);
   - buy: `price*qty <= cash` (`NotEnoughMoney`) **and** `qty <= inventory` (`NotEnoughInventory`);
   - sell: `qty <= position` (`NotEnoughShares`).
3. On success: compute `total = price * qty`, create one `Order` with status `PENDING`, store `reviewed_price = price` and `total`. **No** cash/position/inventory change.
4. On failure: raise the typed error; the route re-renders the form with the submitted input preserved (Req 9.7).

### confirm_order (execute step)

1. **Lock first.** Atomically claim the order with a conditional update / row lock:

   ```sql
   -- bound parameter :oid, no string building
   UPDATE orders SET status = 'EXECUTING'
   WHERE id = :oid AND status = 'PENDING';
   ```

   If zero rows change, the order was already COMPLETED/CANCELLED or claimed by a competing request → raise `OrderNotPending` ("This order has already been completed or cancelled."). This is how only one confirmation can win (Req 12.1, 12.3, 17.2).
2. **Re-read trusted values** (price, cash, inventory, position) from the DB and re-run all Pre-Execution Checks (Req 8.2) using `SELECT ... FOR UPDATE` on the account, stock, and position rows so competing trades cannot spend the same cash or shares (Req 12.2).
3. **Price match.** If current DB price ≠ `reviewed_price`, raise `PriceChanged` → the trainee must review again (Req 8.16). (On this failure the order is returned to `PENDING`.)
4. **Apply all changes in ONE transaction:**
   - buy: `cash -= total`, position `+= qty` (create if missing), inventory `-= qty`;
   - sell: `cash += total`, position `-= qty` (delete if it reaches 0), inventory `+= qty`;
   - set Order `COMPLETED` with `completed_at`;
   - insert exactly one `Transaction`;
   - insert exactly one `EventLog` for the demo user.
   Then `session.commit()`.
5. **Failure.** Any error → `session.rollback()`; nothing persists. The order is left `PENDING` so it can be retried (Req 10.12, 17.4). Invalid-status cases (step 1) are the exception: they stay rejected.

### cancel_order

Verify the order belongs to the demo account and is `PENDING`, set it `CANCELLED`, commit. No cash/position/inventory/transaction change (Req 7). Once an order has been submitted for confirmation (status no longer `PENDING`), cancel is unavailable (Req 7.5).

### Idempotency and concurrency — why it is correct

- **Repeated confirmation (same request twice).** The conditional `UPDATE ... WHERE status='PENDING'` is atomic in the database. The first confirmation flips the status and proceeds; the second sees zero affected rows and is rejected. The trade executes **at most once** (Req 12.1, 17.2). Combined with Post/Redirect/Get, a browser refresh on the confirmation page issues only a GET and cannot re-execute (Req 11.3, 17.3).
- **Competing confirmations (two requests at once).** The same atomic claim serializes them: exactly one wins the status flip; the other gets `OrderNotPending`. Because step 2 locks the account/stock/position rows with `SELECT ... FOR UPDATE`, no two trades can read-then-write the same cash or shares concurrently, preventing double-spend (Req 12.2, 17.5).
- Either mechanism (conditional UPDATE on `status=PENDING`, or `SELECT ... FOR UPDATE` row lock) is sufficient; this design uses both — the conditional UPDATE guards the order, the row locks guard the shared resources.

### Safety of SQL

All queries go through SQLAlchemy with bound parameters. The few hand-written statements (the conditional status UPDATE, the `FOR UPDATE` locks) use bound parameters (`:oid`) — **never** string concatenation or f-strings with user input.

## Demo Identity and Seed Data

- **Backend-selected identity.** The server always resolves the single demo user and practice account (Req 1.1–1.3). Any user/account id in the request is ignored.
- **Seed values** (loaded by an explicit script, not on app start):
  - Account starting cash **$10,000.00**.
  - At least three fictional stocks with fixed prices and share counts.
  - **DEMO1**: $50.00/share, 1,000 available shares; trainee starts with **0** DEMO1 shares.
  - Trainee owns an existing Position in a different stock so a Sell can be demonstrated immediately (Req 1.8).
- **Explicit scripts (no startup reset):**
  - `scripts/init_db.py` — create the empty schema from the SQLAlchemy models.
  - `scripts/seed_demo.py` — insert the demo user, account, stocks, and starter position.
  - `scripts/reset_demo.py` — explicit reset that targets **only** the demo/test database.
  The application **never** resets or re-seeds the database on startup, so saved results survive restarts (Req 11.4).

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

These properties support later property-based and behavioral testing. Each is universally quantified and traces to requirements. The trading service's Flask-free, page-free design makes them directly testable against a real test database.

### Property 1: Total equals price times quantity at two decimals

*For any* stock price (a `Decimal` with two decimals) and *any* positive whole quantity, the computed trade total equals `price * quantity` and is stored with exactly two decimal places.

**Validates: Requirements 5.3**

### Property 2: Preparing a valid order creates exactly one PENDING order and changes nothing else

*For any* valid trade entry, `prepare_order` creates exactly one `Order` with status `PENDING` whose `reviewed_price` equals the current DB price, and leaves cash, positions, inventory, and transactions unchanged.

**Validates: Requirements 6.1, 8.1**

### Property 3: Cancelling a pending order changes no balances

*For any* prepared order, `cancel_order` sets the status to `CANCELLED` and leaves cash, positions, inventory, and transaction records unchanged.

**Validates: Requirements 7.1, 7.3, 7.4**

### Property 4: Invalid quantities are always rejected

*For any* quantity that is not a positive whole number (blank, zero, negative, fractional, or nonnumeric), `prepare_order` rejects the trade with "Enter a positive whole number." and makes no state change.

**Validates: Requirements 8.7, 15.1, 15.2, 15.3, 15.4, 15.5**

### Property 5: A buy never spends more cash than the account holds

*For any* account state and buy entry where `price * quantity` exceeds the DB cash balance, the buy is rejected with "Not enough money." and no state changes; otherwise the cash check passes.

**Validates: Requirements 8.9, 16.4**

### Property 6: A buy never exceeds available inventory

*For any* buy entry where quantity exceeds the DB stock inventory, the buy is rejected with "Not enough shares available to buy." and no state changes.

**Validates: Requirements 8.10, 16.5**

### Property 7: A sell never exceeds the owned position

*For any* sell entry where quantity exceeds the DB position, the sell is rejected with "Not enough shares." and no state changes.

**Validates: Requirements 8.11, 16.6**

### Property 8: Confirmation requires the reviewed price to still match the database price

*For any* order whose `reviewed_price` differs from the current DB price at confirmation time, execution is blocked, a new review is required, and no state changes.

**Validates: Requirements 8.16**

### Property 9: A confirmed trade applies the correct deltas and conserves shares

*For any* valid confirmed trade: for a buy, cash decreases by `total`, the position increases by `quantity`, and inventory decreases by `quantity`; for a sell, cash increases by `total`, the position decreases by `quantity`, and inventory increases by `quantity`. In both cases the shares removed from one side equal the shares added to the other (total shares are conserved).

**Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.6, 10.7**

### Property 10: A completed trade produces exactly one transaction and one event log, all atomically

*For any* confirmed trade, after commit the order is `COMPLETED`, exactly one `Transaction` references that order, exactly one `EventLog` exists for that trade, and all six changes (cash, position, inventory, order status, transaction, event log) are committed together.

**Validates: Requirements 10.8, 10.9, 10.10, 10.11**

### Property 11: Any failure during execution rolls back every change

*For any* failure injected during execution, the full database state after rollback (cash, position, inventory, order status `PENDING`, no transaction, no event log) is identical to the pre-execution state.

**Validates: Requirements 10.12, 17.4**

### Property 12: Confirmation is idempotent

*For any* order, calling confirmation more than once executes the trade at most once; the second and later calls are rejected with "This order has already been completed or cancelled." and leave cash, positions, and inventory unchanged.

**Validates: Requirements 12.1, 12.3, 17.2**

### Property 13: A cancelled or failed order yields no completed-trade transaction

*For any* order that is cancelled or whose execution fails, no `Transaction` record for that order exists.

**Validates: Requirements 10.13, 9.8**

### Property 14: Stored money always has two decimal places

*For any* completed trade, the stored cash balance, totals, and execution prices each have exactly two decimal places.

**Validates: Requirements 10.1, 10.2, 11.2**

## Error Handling

### Typed errors mapped to exact messages

| Condition | Exception | Exact message |
|---|---|---|
| Not a positive whole number | `InvalidQuantity` | `Enter a positive whole number.` |
| Buy cost > cash | `NotEnoughMoney` | `Not enough money.` |
| Sell qty > position | `NotEnoughShares` | `Not enough shares.` |
| Buy qty > inventory | `NotEnoughInventory` | `Not enough shares available to buy.` |
| Order not PENDING | `OrderNotPending` | `This order has already been completed or cancelled.` |
| DB price ≤ 0 or missing | `PriceUnavailable` | A plain price-unavailable message, e.g. `This stock's price is unavailable right now.` |
| Missing/unauthorized record | `InvalidTarget` | A plain failure message, e.g. `That trade is not available.` |
| DB price changed since review | `PriceChanged` | A plain message asking the trainee to review the updated price again. |

### Handling rules

- On any failed check during review, the route catches the `TradeError`, re-renders the form page, and **preserves the submitted input** so the trainee can correct it (Req 9.1, 9.7).
- A failed check or a mid-trade error leaves cash, positions, inventory, and transactions unchanged, and the order stays `PENDING` (Req 9.8, 9.9) — except invalid-status rejections, which remain rejected.
- Any unexpected database error during execution triggers `session.rollback()` inside the service, so partial writes never persist (Req 10.12, 17.4).
- The browser can never override trusted values; the service always re-reads price, ownership, balance, and inventory from the database before acting (Req 8.14, 8.15).

## Testing Strategy

### Dual approach

- **Unit / property tests** exercise `app/services/trading.py` directly with plain parameters — **no Flask request objects and no page rendering** — and assert on **persisted database values** read back from the test database, not on screen messages (except where a specific message is required).
- **Property-based tests** cover the universal properties above; each runs a **minimum of 100 iterations** using a Python PBT library (Hypothesis). We do not hand-roll input generation.
- **Example-based tests** cover the two exact milestone scenarios and the specific edge/error cases.
- **Integration tests** cover persistence across a fresh session/app context and the concurrency scenario against the real test database.

### Separate test database

Tests connect to a **separate MySQL test database** (its own `DATABASE_URL`, e.g. `sts_test`), created from the models in `conftest.py` and torn down/reset between tests via `scripts/reset_demo.py` semantics (test DB only). The development database is never touched by tests.

### Property test tagging

Each property test is tagged with a comment referencing its design property, in the form:

```
# Feature: local-buy-and-sell, Property 9: A confirmed trade applies the correct deltas and conserves shares
```

PBT library: **Hypothesis** (Python). Minimum 100 iterations per property test.

### Required test coverage (asserting DB state before and after)

| Scenario | Type | Asserts on |
|---|---|---|
| Valid buy applies deltas + conservation | Property (Prop 9, 10) | DB cash, position, inventory, order, 1 Transaction, 1 EventLog |
| Valid sell applies deltas + conservation | Property (Prop 9, 10) | DB cash, position, inventory, order, 1 Transaction, 1 EventLog |
| Blank / zero / negative / fractional / nonnumeric qty | Property (Prop 4) | message + unchanged DB state |
| Missing stock / missing account | Edge case | message + unchanged DB state |
| Order owned by another account | Edge case | rejected + unchanged DB state |
| Insufficient cash | Property (Prop 5) | message + unchanged DB state |
| Insufficient inventory | Property (Prop 6) | message + unchanged DB state |
| Sell more than owned | Property (Prop 7) | message + unchanged DB state |
| Cancel of PENDING | Property (Prop 3) | status CANCELLED + unchanged balances |
| Sell entire holding (position removed) | Edge case (Prop 9) | position row deleted |
| Repeated confirmation (no double execution) | Property (Prop 12) | exactly one execution; second rejected |
| Refresh safety of confirmation | Integration | GET re-read matches saved values, no re-exec |
| Competing requests | Edge case / concurrency | exactly one COMPLETED; correct final cash/inventory/position |
| Forced mid-trade DB error → full rollback | Property (Prop 11) | DB state identical to pre-trade snapshot |
| Price changed between review and confirm | Property (Prop 8) | blocked; new review required |
| Price unavailable (DB price ≤ 0) | Edge case | price-unavailable message; blocked |

### The two exact milestone scenarios

- **Buy verification (Req 13):** start cash `$10,000.00`, DEMO1 inventory `1000`, position `0`. Confirm a buy of **5** DEMO1 at `$50.00`. Assert DB after commit: cash `== 9750.00`, DEMO1 position `== 5`, DEMO1 inventory `== 995`, order status `COMPLETED`, exactly **one** purchase `Transaction`, exactly **one** `EventLog`.
- **Sell verification (Req 14):** start cash `$9,750.00`, DEMO1 position `5`, inventory `995`. Confirm a sell of **2** DEMO1 at `$50.00`. Assert DB after commit: cash `== 9850.00`, DEMO1 position `== 3`, DEMO1 inventory `== 997`, order status `COMPLETED`, exactly **one** sale `Transaction`, exactly **one** `EventLog`.

Both are example-based tests that read values **back from the test database** after commit, confirming persistence, not just in-memory results.

### Setup and dependencies

- `requirements.txt` pins exact versions (Flask, SQLAlchemy, PyMySQL, python-dotenv, pytest, Hypothesis).
- `.env.example` documents `DATABASE_URL`, `TEST_DATABASE_URL`, and `SECRET_KEY` with placeholder values and no real secrets.
- `README.md` documents: create local MySQL databases (`sts`, `sts_test`), copy `.env.example` → `.env`, `pip install -r requirements.txt`, `python scripts/init_db.py`, `python scripts/seed_demo.py`, run the app, and `pytest` for the test suite.
