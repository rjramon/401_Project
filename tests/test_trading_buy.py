"""Exact milestone BUY verification scenario (task 6.2, Requirement 13).

This exercises the one known buy scenario the trainer uses to verify the buy
workflow end to end: buy 5 DEMO1 shares at $50.00 from a $10,000.00 starting
cash balance. Every assertion reads the result back from the SEPARATE MySQL
test database (``TEST_DATABASE_URL``) so the test proves what was actually
persisted, not just what the service returned.

Money is compared as ``Decimal`` (never ``float``) to match the
``DECIMAL(12,2)`` money columns.

Database requirement
--------------------
These tests need the separate MySQL test database. Requesting the ``seeded``
fixture makes the module skip cleanly (via the shared conftest fixtures) when
no test database is reachable, instead of failing.
"""

from decimal import Decimal

from app.extensions import db
from app.models import (
    Account,
    EventLog,
    Order,
    Position,
    Stock,
    Transaction,
    ORDER_STATUS_COMPLETED,
)
from app.services.trading import confirm_order, prepare_order


def test_buy_5_demo1_exact_scenario(seeded):
    """Buy 5 DEMO1 @ $50 from $10,000 cash produces the exact milestone result.

    Starting from the seeded demo world (account with $10,000.00, DEMO1 @
    $50.00 with 1,000 shares available and no DEMO1 position), prepare and
    confirm a BUY of 5 DEMO1. Then read back from the test DB:

      * cash balance == $9,750.00                     (Req 13.1)
      * DEMO1 Position quantity == 5                  (Req 13.2)
      * DEMO1 available inventory == 995              (Req 13.3)
      * Order status COMPLETED with completed_at set  (Req 13.4)
      * exactly one Transaction and one EventLog      (Req 13.5)

    **Validates: Requirements 13.1, 13.2, 13.3, 13.4, 13.5**
    """
    account = seeded.account
    demo1 = seeded.demo1

    # Sanity-check the seeded preconditions straight from the DB so the exact
    # scenario starts from exactly the state Requirement 13 names.
    assert account.cash_balance == Decimal("10000.00")
    assert demo1.current_price == Decimal("50.00")
    assert demo1.available_shares == 1000
    # The trainee starts with no DEMO1 position (Req 1.7).
    assert (
        db.session.query(Position)
        .filter_by(account_id=account.id, stock_id=demo1.id)
        .one_or_none()
        is None
    )

    # Review then confirm the buy of 5 shares.
    review = prepare_order(account.id, demo1.id, "buy", 5)
    assert review.total == Decimal("250.00")
    assert review.expected_cash == Decimal("9750.00")

    result = confirm_order(account.id, review.order_id)
    assert result.action == "buy"
    assert result.quantity == 5
    assert result.total == Decimal("250.00")
    assert result.actual_cash == Decimal("9750.00")

    # --- Everything below is read back from the test database. -------------

    # Req 13.1: cash balance is exactly $9,750.00.
    saved_account = db.session.get(Account, account.id)
    assert saved_account.cash_balance == Decimal("9750.00")

    # Req 13.2: the DEMO1 position now holds 5 shares.
    position = (
        db.session.query(Position)
        .filter_by(account_id=account.id, stock_id=demo1.id)
        .one_or_none()
    )
    assert position is not None
    assert position.quantity == 5

    # Req 13.3: DEMO1 inventory dropped from 1,000 to 995.
    saved_stock = db.session.get(Stock, demo1.id)
    assert saved_stock.available_shares == 995

    # Req 13.4: the order is COMPLETED with a completion timestamp.
    saved_order = db.session.get(Order, review.order_id)
    assert saved_order.status == ORDER_STATUS_COMPLETED
    assert saved_order.completed_at is not None

    # Req 13.5: exactly one Transaction for this order and exactly one EventLog
    # created by the trade.
    transactions = (
        db.session.query(Transaction)
        .filter_by(order_id=review.order_id)
        .all()
    )
    assert len(transactions) == 1
    tx = transactions[0]
    assert tx.action == "buy"
    assert tx.quantity == 5
    assert tx.execution_price == Decimal("50.00")
    assert tx.total_amount == Decimal("250.00")

    # One EventLog for the trade, owned by the account's user.
    event_logs = (
        db.session.query(EventLog)
        .filter_by(user_id=saved_account.user_id)
        .all()
    )
    assert len(event_logs) == 1
    assert event_logs[0].event_type == "TRADE_COMPLETED"
