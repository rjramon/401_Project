"""Exact milestone SELL verification scenario (task 6.2, Requirement 14).

This exercises the one known sell scenario the trainer uses to verify the sell
workflow end to end: sell 2 DEMO1 shares at $50.00 while owning 5 DEMO1 shares
and holding $9,750.00 cash, from a DEMO1 inventory of 995. Every assertion
reads the result back from the SEPARATE MySQL test database
(``TEST_DATABASE_URL``) so the test proves what was actually persisted.

Setup choice (documented)
-------------------------
Requirement 14 names the exact preconditions "owning 5 DEMO1 shares",
"$9,750.00 cash", and "a DEMO1 inventory of 995" — which is precisely the
state left by the Requirement 13 buy. So this test first runs the BUY of 5
DEMO1 (prepare + confirm) to reach that state through the real service, then
runs the SELL of 2. This keeps the sell starting from inventory 995 so the
result is 997 exactly as Req 14.3 requires, and avoids hand-poking rows.

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


def test_sell_2_demo1_exact_scenario(seeded):
    """Sell 2 DEMO1 @ $50 while owning 5 and holding $9,750.00 cash.

    First reach the Requirement 14 preconditions by buying 5 DEMO1 (which
    leaves cash $9,750.00, a DEMO1 position of 5, and DEMO1 inventory 995),
    then prepare and confirm a SELL of 2 DEMO1. Read back from the test DB:

      * cash balance == $9,850.00                     (Req 14.1)
      * DEMO1 Position quantity == 3                  (Req 14.2)
      * DEMO1 available inventory == 997              (Req 14.3)
      * Order status COMPLETED with completed_at set  (Req 14.4)
      * exactly one sale Transaction and one EventLog (Req 14.5)

    **Validates: Requirements 14.1, 14.2, 14.3, 14.4, 14.5**
    """
    account = seeded.account
    demo1 = seeded.demo1

    # --- Reach the Req 14 preconditions via the real buy workflow. ---------
    buy_review = prepare_order(account.id, demo1.id, "buy", 5)
    confirm_order(account.id, buy_review.order_id)

    # Confirm the preconditions straight from the DB: owns 5 DEMO1, cash
    # $9,750.00, inventory 995 (Req 14 "while owning 5 ... holding $9,750.00
    # ... from a DEMO1 inventory of 995").
    account_before = db.session.get(Account, account.id)
    assert account_before.cash_balance == Decimal("9750.00")
    position_before = (
        db.session.query(Position)
        .filter_by(account_id=account.id, stock_id=demo1.id)
        .one_or_none()
    )
    assert position_before is not None and position_before.quantity == 5
    stock_before = db.session.get(Stock, demo1.id)
    assert stock_before.available_shares == 995

    # --- Sell 2 DEMO1. -----------------------------------------------------
    sell_review = prepare_order(account.id, demo1.id, "sell", 2)
    assert sell_review.total == Decimal("100.00")
    assert sell_review.expected_cash == Decimal("9850.00")

    result = confirm_order(account.id, sell_review.order_id)
    assert result.action == "sell"
    assert result.quantity == 2
    assert result.total == Decimal("100.00")
    assert result.actual_cash == Decimal("9850.00")

    # --- Everything below is read back from the test database. -------------

    # Req 14.1: cash balance rose from $9,750.00 to $9,850.00.
    saved_account = db.session.get(Account, account.id)
    assert saved_account.cash_balance == Decimal("9850.00")

    # Req 14.2: the DEMO1 position dropped from 5 to 3.
    position = (
        db.session.query(Position)
        .filter_by(account_id=account.id, stock_id=demo1.id)
        .one_or_none()
    )
    assert position is not None
    assert position.quantity == 3

    # Req 14.3: DEMO1 inventory rose from 995 back to 997.
    saved_stock = db.session.get(Stock, demo1.id)
    assert saved_stock.available_shares == 997

    # Req 14.4: the sell order is COMPLETED with a completion timestamp.
    saved_order = db.session.get(Order, sell_review.order_id)
    assert saved_order.status == ORDER_STATUS_COMPLETED
    assert saved_order.completed_at is not None

    # Req 14.5: exactly one sale Transaction for the sell order.
    sell_transactions = (
        db.session.query(Transaction)
        .filter_by(order_id=sell_review.order_id)
        .all()
    )
    assert len(sell_transactions) == 1
    tx = sell_transactions[0]
    assert tx.action == "sell"
    assert tx.quantity == 2
    assert tx.execution_price == Decimal("50.00")
    assert tx.total_amount == Decimal("100.00")

    # Exactly one EventLog created by the sell trade. The earlier buy created
    # its own EventLog, so filter to the sell's description (which names the
    # sell order id) to assert the sell produced exactly one.
    sell_event_logs = [
        e
        for e in db.session.query(EventLog)
        .filter_by(user_id=saved_account.user_id)
        .all()
        if f"order {sell_review.order_id}" in (e.description or "")
    ]
    assert len(sell_event_logs) == 1
    assert sell_event_logs[0].event_type == "TRADE_COMPLETED"
