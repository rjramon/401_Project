"""Load the demo data for this milestone into the DATABASE_URL database.

This is an EXPLICIT, standalone script — a companion to
``scripts/init_db.py``. The application never seeds the database on startup
(see app/__init__.py and Requirement 11.4) so that saved results survive
restarts. Seeding is a deliberate step you run yourself.

It assumes the schema ALREADY EXISTS. Create it first with:

    python scripts/init_db.py

Then load the demo data with:

    python scripts/seed_demo.py

What it inserts (exactly, per Requirement 1):
    * One trainee ``User`` (role='trainee') with name, email, and a
      placeholder ``password_hash``. Authentication/login is DEFERRED for
      this milestone, so no real credential is stored — the placeholder is
      only there to satisfy the NOT NULL column.
    * One practice ``Account`` owned by that user with a starting cash
      balance of $10,000.00 (both ``starting_balance`` and ``cash_balance``).
    * At least three fictional, fixed-price ``Stock`` rows:
        - DEMO1 at $50.00/share with 1,000 available shares (Req 1.6).
        - DEMO2 at $25.00/share with 500 available shares.
        - DEMO3 at $120.00/share with 300 available shares.
    * The trainee starts with 0 DEMO1 shares, so NO Position row is created
      for DEMO1 (Req 1.7).
    * A starter ``Position`` of 10 shares in DEMO2 (a stock other than
      DEMO1) so a Sell can be demonstrated immediately (Req 1.8).

Money is handled as Python ``Decimal`` everywhere — never ``float`` — to
match the ``Numeric(12, 2)`` money columns and keep cents exact.

Inventory vs. held shares: a stock's ``available_shares`` is the inventory
still available for trainees to BUY. The trainee's held shares (the starter
Position) are separate from that inventory. DEMO2 keeps a sensible 500 shares
available to buy on top of the 10 the trainee already holds; these numbers
are chosen independently and are not derived from one another.

Idempotency: this script is SAFE TO RE-RUN. It looks up the trainee by email
first; if the demo data already exists it skips creation and prints a notice
instead of inserting duplicates. Committing here is correct and expected —
this is a setup script, not a request handler, so the "db.py helpers never
commit" rule does not apply to it.
"""

import os
import sys
from decimal import Decimal

# Make the project root importable so "app" resolves even if this script is
# launched directly (e.g. `python scripts/seed_demo.py`). The repo root is the
# parent folder of this scripts/ directory.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import create_app  # noqa: E402  (import after sys.path setup)
from app.extensions import db  # noqa: E402
from app.models import Account, Position, Stock, User  # noqa: E402


# --- Exact demo values (single source of truth for the summary print) ------

# The trainee identity. Login is deferred this milestone, so the password
# hash is a clearly-labelled placeholder and never a real credential.
DEMO_USER_NAME = "Demo Trainee"
DEMO_USER_EMAIL = "trainee@demo.sts"
DEMO_USER_ROLE = "trainee"
DEMO_PASSWORD_HASH = "seeded-not-a-real-login"  # placeholder: auth is deferred

# The practice account starts with exactly $10,000.00 (Req 1.4). Money is a
# Decimal with two places, never a float.
STARTING_CASH = Decimal("10000.00")

# Fictional, fixed-price stocks. (ticker, company_name, current_price,
# available_shares). available_shares is the inventory left to BUY and is
# independent of any shares the trainee already holds.
DEMO_STOCKS = (
    # DEMO1 is fixed by Requirement 1.6: $50.00/share, 1,000 shares to buy.
    ("DEMO1", "Demo One Industries", Decimal("50.00"), 1000),
    # DEMO2 is the stock the trainee already holds (starter Position below).
    ("DEMO2", "Demo Two Holdings", Decimal("25.00"), 500),
    # A third stock so there are at least three to browse and trade.
    ("DEMO3", "Demo Three Corp", Decimal("120.00"), 300),
)

# The starter Position is in DEMO2 (NOT DEMO1) so a Sell can be demonstrated
# immediately (Req 1.8). The trainee owns 10 DEMO2 shares; DEMO2 still has 500
# shares available to buy — the held shares and the buyable inventory are
# tracked separately.
STARTER_POSITION_TICKER = "DEMO2"
STARTER_POSITION_QUANTITY = 10


def seed(session):
    """Insert the demo data if it is not already present.

    Idempotent: existence is detected by the trainee's email. Returns a tuple
    ``(created, user, account, stocks_by_ticker, position)`` where ``created``
    is False when the demo data already existed (nothing was inserted).
    """
    # Existence check keyed on the unique email. If the trainee is already
    # there, we assume a previous successful seed and do not insert again.
    existing_user = (
        session.query(User).filter_by(email=DEMO_USER_EMAIL).one_or_none()
    )
    if existing_user is not None:
        account = (
            session.query(Account)
            .filter_by(user_id=existing_user.id)
            .first()
        )
        stocks_by_ticker = {
            s.ticker: s
            for s in session.query(Stock)
            .filter(Stock.ticker.in_([t for t, *_ in DEMO_STOCKS]))
            .all()
        }
        position = None
        if account is not None:
            starter_stock = stocks_by_ticker.get(STARTER_POSITION_TICKER)
            if starter_stock is not None:
                position = (
                    session.query(Position)
                    .filter_by(account_id=account.id, stock_id=starter_stock.id)
                    .one_or_none()
                )
        return (False, existing_user, account, stocks_by_ticker, position)

    # --- Fresh insert --------------------------------------------------------

    # One trainee user. password_hash is a placeholder because login is
    # deferred this milestone (no authentication is implemented yet).
    user = User(
        name=DEMO_USER_NAME,
        email=DEMO_USER_EMAIL,
        password_hash=DEMO_PASSWORD_HASH,
        role=DEMO_USER_ROLE,
    )
    session.add(user)
    # Flush so user.id is assigned before we reference it as a foreign key.
    session.flush()

    # One practice account with $10,000.00 starting cash. Both the starting
    # balance and the current cash balance begin at the same Decimal amount.
    account = Account(
        user_id=user.id,
        starting_balance=STARTING_CASH,
        cash_balance=STARTING_CASH,
    )
    session.add(account)

    # The fictional stocks with their fixed prices and buyable inventory.
    stocks_by_ticker = {}
    for ticker, company_name, price, available in DEMO_STOCKS:
        stock = Stock(
            company_name=company_name,
            ticker=ticker,
            current_price=price,
            available_shares=available,
        )
        session.add(stock)
        stocks_by_ticker[ticker] = stock

    # Flush so the stock ids exist before building the starter Position.
    session.flush()

    # Starter Position in DEMO2 (NOT DEMO1). The trainee owns these 10 shares;
    # DEMO1 intentionally gets NO Position row (trainee starts at 0 DEMO1).
    starter_stock = stocks_by_ticker[STARTER_POSITION_TICKER]
    position = Position(
        account_id=account.id,
        stock_id=starter_stock.id,
        quantity=STARTER_POSITION_QUANTITY,
    )
    session.add(position)

    return (True, user, account, stocks_by_ticker, position)


def _print_summary(created, user, account, stocks_by_ticker, position):
    """Print a clear, human-readable summary of what was seeded."""
    if created:
        print("Seeded demo data into the database identified by DATABASE_URL.")
    else:
        print(
            "Demo data already present (matched by email "
            f"'{DEMO_USER_EMAIL}'); nothing was inserted."
        )

    print(f"  User:    {user.name} <{user.email}> role={user.role}")
    if account is not None:
        print(
            f"  Account: id={account.id} "
            f"cash_balance=${account.cash_balance} "
            f"starting_balance=${account.starting_balance}"
        )

    print("  Stocks:")
    for ticker, company_name, price, available in DEMO_STOCKS:
        stock = stocks_by_ticker.get(ticker)
        shares = stock.available_shares if stock is not None else available
        current = stock.current_price if stock is not None else price
        note = " (trainee holds 0; no Position)" if ticker == "DEMO1" else ""
        print(
            f"    - {ticker} {company_name}: "
            f"${current}/share, {shares} available{note}"
        )

    if position is not None:
        print(
            f"  Starter Position: {position.quantity} shares of "
            f"{STARTER_POSITION_TICKER} (so a Sell can be demonstrated now)"
        )
    else:
        print(
            f"  Starter Position: {STARTER_POSITION_QUANTITY} shares of "
            f"{STARTER_POSITION_TICKER} (so a Sell can be demonstrated now)"
        )


def main():
    """Seed the demo data into the database named by DATABASE_URL."""
    # Development configuration: the connection string comes entirely from
    # DATABASE_URL via DevConfig, exactly like scripts/init_db.py.
    app = create_app("app.config.DevConfig")

    # An app context binds the shared db instance to this app so the session
    # connects to the right database.
    with app.app_context():
        result = seed(db.session)
        # Commit the inserts. This is a setup script (not a request handler),
        # so committing here is correct — the no-commit rule applies only to
        # the app/db.py helpers, where the trading service owns the boundary.
        db.session.commit()
        _print_summary(*result)


if __name__ == "__main__":
    main()
