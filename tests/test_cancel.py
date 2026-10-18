"""Cancel-step tests for ``cancel_order`` (task 7.2).

These verify the cancel step of the trading service:

1. Cancelling a still-PENDING order flips its status to CANCELLED and leaves
   cash, positions, Stock inventory, and Transaction records unchanged
   (Req 7.1, 7.3, 7.4).
2. A non-PENDING order can no longer be cancelled: cancelling the same order
   twice, or cancelling an order that was already confirmed, is rejected with
   ``OrderNotPending`` and the exact message
   "This order has already been completed or cancelled." (Req 7.5).

It also includes a Hypothesis property test (min 100 iterations) for
design.md Correctness Property 3: *for any prepared order, cancel_order sets
status CANCELLED and leaves cash, positions, inventory, and transaction
records unchanged.* Each example resets and re-seeds the exact demo world via
the ``_fresh_world()`` pattern established in
``tests/test_properties_review.py``, and every assertion reads state back from
the SEPARATE MySQL test database.

Database requirement
--------------------
These tests need the SEPARATE MySQL test database (``TEST_DATABASE_URL``).
Requesting the ``seeded`` fixture makes the module skip cleanly (via the
shared conftest fixtures) when no test database is reachable, instead of
failing. Money is compared as ``Decimal`` (never ``float``) to match the
``DECIMAL(12,2)`` money columns.

**Validates: Requirements 7.1, 7.3, 7.4**
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.extensions import db
from app.models import (
    Account,
    EventLog,
    Order,
    Position,
    Stock,
    Transaction,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_PENDING,
)
from app.services.trading import (
    OrderNotPending,
    cancel_order,
    confirm_order,
    prepare_order,
)
from scripts.seed_demo import seed as seed_demo


# Minimum Hypothesis examples for the property test (task requires >= 100).
MAX_EXAMPLES = 100

# Hypothesis reuses one pytest function across many examples; the function-
# scoped ``seeded`` fixture only sets up once. We reset/seed inside each
# example via ``_fresh_world`` instead, so silence the function-scoped fixture
# health check (we do not rely on the fixture resetting per example).
PBT_SETTINGS = settings(
    max_examples=MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# Small read-back helpers (always query the DB fresh, never trust in-memory).
# ---------------------------------------------------------------------------

def _position_qty(account_id, stock_id):
    """Return the owned share count, or ``None`` when no Position row exists."""
    position = (
        db.session.query(Position)
        .filter_by(account_id=account_id, stock_id=stock_id)
        .one_or_none()
    )
    return None if position is None else position.quantity


def _cash(account_id):
    return db.session.get(Account, account_id).cash_balance


def _inventory(stock_id):
    return db.session.get(Stock, stock_id).available_shares


def _order_status(order_id):
    return db.session.get(Order, order_id).status


def _tx_count(order_id):
    return db.session.query(Transaction).filter_by(order_id=order_id).count()


def _event_count(user_id):
    return db.session.query(EventLog).filter_by(user_id=user_id).count()


# ---------------------------------------------------------------------------
# Example tests
# ---------------------------------------------------------------------------

def test_cancel_pending_order_sets_cancelled_and_changes_nothing(seeded):
    """Cancelling a PENDING order flips status to CANCELLED and nothing else.

    Prepare a buy, then cancel it before confirming. The order must end
    CANCELLED and cash, positions, Stock inventory, and Transaction records
    must be identical to the pre-cancel state read back from the DB
    (Req 7.1, 7.3, 7.4).
    """
    account = seeded.account
    demo1 = seeded.demo1

    review = prepare_order(account.id, demo1.id, "buy", 5)
    assert _order_status(review.order_id) == ORDER_STATUS_PENDING

    # Snapshot the full pre-cancel DB state (review never moved money).
    starting_cash = _cash(account.id)
    starting_inventory = _inventory(demo1.id)
    starting_position = _position_qty(account.id, demo1.id)
    starting_events = _event_count(account.user_id)

    result = cancel_order(account.id, review.order_id)

    # The result carries the ids a route needs to redirect to stock details.
    assert result.order_id == review.order_id
    assert result.stock_id == demo1.id

    # The order is now CANCELLED.
    assert _order_status(review.order_id) == ORDER_STATUS_CANCELLED

    # Cash, positions, inventory, and transactions are untouched by cancel.
    assert _cash(account.id) == starting_cash
    assert _inventory(demo1.id) == starting_inventory
    assert _position_qty(account.id, demo1.id) == starting_position
    assert _tx_count(review.order_id) == 0
    assert _event_count(account.user_id) == starting_events


def test_cancel_also_leaves_a_held_position_unchanged(seeded):
    """Cancelling a SELL does not touch the owned position or inventory.

    The seeded trainee owns 10 DEMO2 shares. Preparing then cancelling a sell
    must leave that holding, the stock inventory, cash, and transactions
    exactly as they were (Req 7.1, 7.3, 7.4).
    """
    account = seeded.account
    demo2 = seeded.demo2

    assert _position_qty(account.id, demo2.id) == 10

    review = prepare_order(account.id, demo2.id, "sell", 4)

    starting_cash = _cash(account.id)
    starting_inventory = _inventory(demo2.id)

    cancel_order(account.id, review.order_id)

    assert _order_status(review.order_id) == ORDER_STATUS_CANCELLED
    assert _position_qty(account.id, demo2.id) == 10
    assert _cash(account.id) == starting_cash
    assert _inventory(demo2.id) == starting_inventory
    assert _tx_count(review.order_id) == 0


def test_cancelling_twice_is_rejected(seeded):
    """A second cancel of the same order is rejected with the exact message.

    After one successful cancel the order is CANCELLED (no longer PENDING), so
    cancelling it again raises ``OrderNotPending`` with the milestone message
    and makes no further change (Req 7.5).
    """
    account = seeded.account
    demo1 = seeded.demo1

    review = prepare_order(account.id, demo1.id, "buy", 5)
    cancel_order(account.id, review.order_id)

    # Snapshot state after the first (successful) cancel.
    cash_after_first = _cash(account.id)
    inventory_after_first = _inventory(demo1.id)
    position_after_first = _position_qty(account.id, demo1.id)

    with pytest.raises(OrderNotPending) as excinfo:
        cancel_order(account.id, review.order_id)
    assert excinfo.value.message == (
        "This order has already been completed or cancelled."
    )

    # Still CANCELLED, and nothing else moved on the rejected second cancel.
    assert _order_status(review.order_id) == ORDER_STATUS_CANCELLED
    assert _cash(account.id) == cash_after_first
    assert _inventory(demo1.id) == inventory_after_first
    assert _position_qty(account.id, demo1.id) == position_after_first


def test_cancel_after_confirm_is_rejected(seeded):
    """A completed order can no longer be cancelled (Req 7.5).

    Confirm an order (it becomes COMPLETED) then try to cancel it: the cancel
    must raise ``OrderNotPending`` with the exact message, and the completed
    trade's effects (cash, position, inventory, the single Transaction) must
    be left untouched.
    """
    account = seeded.account
    demo1 = seeded.demo1

    review = prepare_order(account.id, demo1.id, "buy", 5)
    confirm_order(account.id, review.order_id)

    # Snapshot the post-confirm (COMPLETED) DB state.
    cash_after_confirm = _cash(account.id)
    inventory_after_confirm = _inventory(demo1.id)
    position_after_confirm = _position_qty(account.id, demo1.id)
    tx_after_confirm = _tx_count(review.order_id)

    assert cash_after_confirm == Decimal("9750.00")
    assert position_after_confirm == 5
    assert inventory_after_confirm == 995
    assert tx_after_confirm == 1

    with pytest.raises(OrderNotPending) as excinfo:
        cancel_order(account.id, review.order_id)
    assert excinfo.value.message == (
        "This order has already been completed or cancelled."
    )

    # The completed trade is untouched by the rejected cancel.
    assert _cash(account.id) == cash_after_confirm
    assert _inventory(demo1.id) == inventory_after_confirm
    assert _position_qty(account.id, demo1.id) == position_after_confirm
    assert _tx_count(review.order_id) == tx_after_confirm


# ---------------------------------------------------------------------------
# Per-example helpers for the Hypothesis property test
# ---------------------------------------------------------------------------

def _fresh_world():
    """Reset the TEST database to an empty schema and load the demo world.

    Mirrors ``reset_db`` (drop_all + create_all on the test DB) followed by
    ``scripts/seed_demo.seed``, so each Hypothesis example starts from the
    exact, known demo state. Returns a small namespace of the seeded records
    read back from the DB (ids and values the application would see). This is
    the same per-example reset pattern used in ``tests/test_properties_review.py``.
    """
    db.session.remove()
    db.drop_all()
    db.create_all()

    seed_demo(db.session)
    db.session.commit()

    account = db.session.query(Account).order_by(Account.id.asc()).first()
    stocks = {s.ticker: s for s in db.session.query(Stock).all()}
    position = (
        db.session.query(Position)
        .filter_by(account_id=account.id, stock_id=stocks["DEMO2"].id)
        .one_or_none()
    )
    return SimpleNamespace(
        account=account,
        stocks=stocks,
        demo1=stocks["DEMO1"],
        demo2=stocks["DEMO2"],
        position=position,
    )


def _state_snapshot(account_id):
    """Read back the whole mutable world as plain values for comparison.

    Captures cash, every position quantity, every stock inventory, and the
    counts of transactions and event logs — enough to prove cancel changed no
    balances. The Order count is intentionally excluded because cancel is
    expected to flip the single prepared order's status (not create/remove
    rows); the status change is asserted separately.
    """
    account = db.session.get(Account, account_id)
    positions = {p.stock_id: p.quantity for p in db.session.query(Position).all()}
    inventory = {s.id: s.available_shares for s in db.session.query(Stock).all()}
    return {
        "cash": account.cash_balance,
        "positions": positions,
        "inventory": inventory,
        "transactions": db.session.query(Transaction).count(),
        "event_logs": db.session.query(EventLog).count(),
    }


# ---------------------------------------------------------------------------
# Property 3
# ---------------------------------------------------------------------------

# Feature: local-buy-and-sell, Property 3: Cancelling a pending order changes no balances
@PBT_SETTINGS
@given(
    action=st.sampled_from(["buy", "sell"]),
    quantity=st.integers(min_value=1, max_value=5),
)
def test_property_3_cancel_pending_changes_no_balances(seeded, action, quantity):
    """For any prepared order, ``cancel_order`` sets the status to CANCELLED
    and leaves cash, positions, inventory, and transaction records unchanged.

    Buy targets DEMO1 (1000 inventory, cheap vs the seeded $10k) and sell
    targets the owned DEMO2 starter position (10 shares); quantity <= 5 keeps
    both prepares valid. The snapshot is taken AFTER prepare (which only
    creates a PENDING order and moves no money) so the comparison isolates the
    cancel step's "changes nothing" guarantee.

    **Validates: Requirements 7.1, 7.3, 7.4**
    """
    world = _fresh_world()
    stock = world.demo1 if action == "buy" else world.demo2

    review = prepare_order(world.account.id, stock.id, action, quantity)
    assert _order_status(review.order_id) == ORDER_STATUS_PENDING

    before = _state_snapshot(world.account.id)

    result = cancel_order(world.account.id, review.order_id)

    after = _state_snapshot(world.account.id)

    # The prepared order is now CANCELLED and the result points back at it.
    assert result.order_id == review.order_id
    assert result.stock_id == stock.id
    assert _order_status(review.order_id) == ORDER_STATUS_CANCELLED

    # Cancel moved no money and touched no positions / inventory / records.
    assert after["cash"] == before["cash"]
    assert after["positions"] == before["positions"]
    assert after["inventory"] == before["inventory"]
    assert after["transactions"] == before["transactions"]
    assert after["event_logs"] == before["event_logs"]
