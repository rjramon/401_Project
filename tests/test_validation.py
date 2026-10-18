"""Example (not property-based) tests for ``prepare_order`` (review step).

These tests exercise the Pre-Execution Checks and the "create exactly one
PENDING order and change nothing else" guarantee of the trading service's
review step, asserting on **persisted DB state read back from the separate
MySQL test database** (never the dev DB).

They use the ``seeded`` fixture from ``tests/conftest.py``, which loads the
exact demo world:
    * one practice ``Account`` with $10,000.00 cash;
    * DEMO1 @ $50.00 with 1,000 available shares and NO starter position;
    * DEMO2 @ $25.00 with 500 available shares and a 10-share starter
      position (so a Sell can be exercised);
    * DEMO3 @ $120.00 with 300 available shares.

If ``TEST_DATABASE_URL`` is unset or the test database is unreachable, the
DB-backed fixtures skip these tests cleanly (handled in conftest.py).

Covers Requirements: 6.1, 8.1, 15.1-15.5, 16.1, 16.2, 16.4, 16.5, 16.6.
"""

from decimal import Decimal

import pytest

from app.models import (
    Account,
    EventLog,
    Order,
    Position,
    Stock,
    Transaction,
    ORDER_STATUS_PENDING,
)
from app.services.trading import (
    InvalidQuantity,
    InvalidTarget,
    NotEnoughInventory,
    NotEnoughMoney,
    NotEnoughShares,
    prepare_order,
)


# ---------------------------------------------------------------------------
# Helpers — snapshot the DB state so we can assert "nothing else changed".
# ---------------------------------------------------------------------------

def _snapshot(session):
    """Capture the parts of the test DB a successful/failed review must leave
    untouched: account cash, every position, every stock's inventory, and the
    counts of transactions and event logs. Read straight from the DB.
    """
    accounts = {
        a.id: a.cash_balance for a in session.query(Account).all()
    }
    positions = {
        (p.account_id, p.stock_id): p.quantity
        for p in session.query(Position).all()
    }
    inventory = {
        s.id: s.available_shares for s in session.query(Stock).all()
    }
    return {
        "accounts": accounts,
        "positions": positions,
        "inventory": inventory,
        "transactions": session.query(Transaction).count(),
        "event_logs": session.query(EventLog).count(),
    }


def _assert_unchanged(session, before):
    """Assert the capital-preserving state matches an earlier snapshot."""
    after = _snapshot(session)
    assert after["accounts"] == before["accounts"], "cash balances changed"
    assert after["positions"] == before["positions"], "positions changed"
    assert after["inventory"] == before["inventory"], "stock inventory changed"
    assert after["transactions"] == before["transactions"], "transactions changed"
    assert after["event_logs"] == before["event_logs"], "event logs changed"


# ---------------------------------------------------------------------------
# Valid prepare — creates exactly one PENDING order, changes nothing else.
# ---------------------------------------------------------------------------

def test_valid_buy_creates_one_pending_order_and_changes_nothing_else(seeded):
    """A valid buy review persists exactly one PENDING order whose
    ``reviewed_price`` equals the DB price and ``total`` == price * qty, and
    leaves cash, positions, inventory, transactions, and logs untouched
    (Req 6.1, 8.1, 16.4, 16.5)."""
    session = seeded.session
    account = seeded.account
    demo1 = seeded.demo1  # $50.00, 1000 shares, no position
    before = _snapshot(session)
    assert session.query(Order).count() == 0

    review = prepare_order(account.id, demo1.id, "buy", "5")

    # Exactly one order was persisted, and it is PENDING.
    orders = session.query(Order).all()
    assert len(orders) == 1
    order = orders[0]
    assert order.status == ORDER_STATUS_PENDING
    assert order.account_id == account.id
    assert order.stock_id == demo1.id
    assert order.action == "buy"
    assert order.quantity == 5

    # reviewed_price is the DB price; total == price * quantity (two places).
    assert order.reviewed_price == Decimal("50.00")
    assert order.total == Decimal("250.00")
    assert review.order_id == order.id
    assert review.total == Decimal("250.00")
    assert review.expected_cash == Decimal("9750.00")

    # Nothing else moved: no cash/position/inventory/transaction/log change.
    _assert_unchanged(session, before)


def test_valid_sell_creates_one_pending_order_and_changes_nothing_else(seeded):
    """A valid sell review (within the owned DEMO2 position) persists exactly
    one PENDING order and changes nothing else (Req 6.1, 8.1, 16.6)."""
    session = seeded.session
    account = seeded.account
    demo2 = seeded.demo2  # $25.00, owns 10
    before = _snapshot(session)

    review = prepare_order(account.id, demo2.id, "sell", "4")

    orders = session.query(Order).all()
    assert len(orders) == 1
    order = orders[0]
    assert order.status == ORDER_STATUS_PENDING
    assert order.action == "sell"
    assert order.quantity == 4
    assert order.reviewed_price == Decimal("25.00")
    assert order.total == Decimal("100.00")
    assert review.expected_cash == Decimal("10100.00")

    _assert_unchanged(session, before)


# ---------------------------------------------------------------------------
# Invalid quantity variants (Req 15.1-15.5) — all raise InvalidQuantity with
# the one exact message and persist nothing.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "quantity_raw",
    ["", "0", "-3", "1.5", "abc"],
    ids=["blank", "zero", "negative", "fractional", "nonnumeric"],
)
def test_invalid_quantity_rejected_with_exact_message_and_no_state_change(
    seeded, quantity_raw
):
    """Each invalid-quantity variant raises ``InvalidQuantity`` with
    "Enter a positive whole number." and creates no order / no state change
    (Req 15.1, 15.2, 15.3, 15.4, 15.5)."""
    session = seeded.session
    account = seeded.account
    demo1 = seeded.demo1
    before = _snapshot(session)

    with pytest.raises(InvalidQuantity) as exc_info:
        prepare_order(account.id, demo1.id, "buy", quantity_raw)

    assert exc_info.value.message == "Enter a positive whole number."
    assert session.query(Order).count() == 0
    _assert_unchanged(session, before)


# ---------------------------------------------------------------------------
# Affordability / availability checks.
# ---------------------------------------------------------------------------

def test_insufficient_cash_raises_not_enough_money(seeded):
    """A buy whose cost exceeds the $10,000.00 cash balance raises
    ``NotEnoughMoney`` ("Not enough money.") and persists nothing. DEMO1 @ $50
    with 1,000 shares available: 300 shares costs $15,000 > cash, and inventory
    is sufficient so the cash check is what fails (Req 16.4)."""
    session = seeded.session
    account = seeded.account
    demo1 = seeded.demo1  # $50.00, 1000 available
    before = _snapshot(session)

    with pytest.raises(NotEnoughMoney) as exc_info:
        prepare_order(account.id, demo1.id, "buy", "300")

    assert exc_info.value.message == "Not enough money."
    assert session.query(Order).count() == 0
    _assert_unchanged(session, before)


def test_insufficient_inventory_raises_not_enough_inventory(seeded):
    """A buy for more shares than the stock has available raises
    ``NotEnoughInventory`` ("Not enough shares available to buy."). To isolate
    the inventory check from the cash check, cash is bumped so that inventory
    is the binding constraint: DEMO2 has 500 shares available and we buy 600
    (Req 16.5)."""
    session = seeded.session
    account = seeded.account
    demo2 = seeded.demo2  # $25.00, 500 available

    # Raise cash high enough that inventory (not cash) is the binding limit:
    # buying 600 DEMO2 shares costs $15,000, which exceeds the default
    # $10,000. Bump cash so the cash check passes and inventory fails.
    account.cash_balance = Decimal("1000000.00")
    session.commit()
    before = _snapshot(session)

    with pytest.raises(NotEnoughInventory) as exc_info:
        prepare_order(account.id, demo2.id, "buy", "600")

    assert exc_info.value.message == "Not enough shares available to buy."
    assert session.query(Order).count() == 0
    _assert_unchanged(session, before)


def test_insufficient_owned_shares_raises_not_enough_shares(seeded):
    """Selling more shares than the account owns raises ``NotEnoughShares``
    ("Not enough shares."). The account owns 10 DEMO2 shares; selling 11 must
    fail and persist nothing (Req 16.6)."""
    session = seeded.session
    account = seeded.account
    demo2 = seeded.demo2  # owns 10
    before = _snapshot(session)

    with pytest.raises(NotEnoughShares) as exc_info:
        prepare_order(account.id, demo2.id, "sell", "11")

    assert exc_info.value.message == "Not enough shares."
    assert session.query(Order).count() == 0
    _assert_unchanged(session, before)


def test_sell_with_no_position_raises_not_enough_shares(seeded):
    """Selling a stock the account holds no position in (DEMO1) raises
    ``NotEnoughShares`` and persists nothing (Req 16.6)."""
    session = seeded.session
    account = seeded.account
    demo1 = seeded.demo1  # no position
    before = _snapshot(session)

    with pytest.raises(NotEnoughShares) as exc_info:
        prepare_order(account.id, demo1.id, "sell", "1")

    assert exc_info.value.message == "Not enough shares."
    assert session.query(Order).count() == 0
    _assert_unchanged(session, before)


# ---------------------------------------------------------------------------
# Target resolution: missing stock, missing/mismatched account (Req 16.1, 16.2).
# ---------------------------------------------------------------------------

def test_missing_stock_raises_invalid_target(seeded):
    """A stock_id that does not exist raises ``InvalidTarget`` and persists
    nothing (Req 16.1)."""
    session = seeded.session
    account = seeded.account
    before = _snapshot(session)

    # Pick an id guaranteed not to exist.
    missing_stock_id = max((s.id for s in session.query(Stock).all())) + 999

    with pytest.raises(InvalidTarget):
        prepare_order(account.id, missing_stock_id, "buy", "1")

    assert session.query(Order).count() == 0
    _assert_unchanged(session, before)


def test_mismatched_account_raises_invalid_target(seeded):
    """An account_id that is not the backend-selected demo account raises
    ``InvalidTarget`` and persists nothing (Req 16.2)."""
    session = seeded.session
    account = seeded.account
    demo1 = seeded.demo1
    before = _snapshot(session)

    not_the_demo_account_id = account.id + 999

    with pytest.raises(InvalidTarget):
        prepare_order(not_the_demo_account_id, demo1.id, "buy", "1")

    assert session.query(Order).count() == 0
    _assert_unchanged(session, before)
