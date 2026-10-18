"""Execute edge-case example tests for ``confirm_order`` (task 6.3).

These verify the trickier guarantees of the execute step beyond the happy
path covered by ``tests/test_trading_buy.py``:

1. Selling an entire holding removes the Position row (Req 10.12, 17.1).
2. Repeated confirmation executes at most once; the second confirm is
   rejected with the exact message and nothing changes (Req 12.1, 12.3,
   17.2).
3. A price change between review and confirm blocks execution: the order
   stays PENDING and no cash/position/inventory/transaction change happens
   (Req 8.16).
4. A forced mid-trade DB error rolls back every change: DB state is identical
   to pre-execution, the order stays PENDING, and no Transaction / EventLog
   is written (Req 10.13, 17.4).
5. Cancelled or failed orders produce no Transaction (Req 17.1, 17.2).

Every assertion reads values back from the SEPARATE MySQL test database
(``TEST_DATABASE_URL``). Requesting the ``seeded`` fixture makes the module
skip cleanly (via the shared conftest fixtures) when no test database is
reachable, instead of failing.

Money is compared as ``Decimal`` (never ``float``) to match the
``DECIMAL(12,2)`` money columns.

**Validates: Requirements 8.16, 10.12, 10.13, 12.1, 12.3, 17.1, 17.2, 17.4**
"""

from decimal import Decimal

import pytest

from app.extensions import db
from app.models import (
    Account,
    EventLog,
    Order,
    Position,
    Stock,
    Transaction,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_COMPLETED,
    ORDER_STATUS_PENDING,
)
from app.services import trading
from app.services.trading import (
    OrderNotPending,
    PriceChanged,
    cancel_order,
    confirm_order,
    prepare_order,
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
    return (
        db.session.query(Transaction).filter_by(order_id=order_id).count()
    )


def _event_count(user_id):
    return db.session.query(EventLog).filter_by(user_id=user_id).count()


# ---------------------------------------------------------------------------
# 1. Sell entire holding removes the Position row.
# ---------------------------------------------------------------------------

def test_sell_entire_holding_removes_position_row(seeded):
    """Selling every owned DEMO2 share deletes the Position row (Req 10.12, 17.1).

    The seeded trainee owns 10 DEMO2 shares @ $25.00. Selling all 10 must zero
    out the holding, and a fully-sold holding is removed rather than kept at
    quantity 0: the (account, DEMO2) Position row no longer exists in the DB.
    """
    account = seeded.account
    demo2 = seeded.demo2

    # Precondition straight from the DB: 10 DEMO2 shares owned.
    assert _position_qty(account.id, demo2.id) == 10
    starting_cash = _cash(account.id)
    starting_inventory = _inventory(demo2.id)

    review = prepare_order(account.id, demo2.id, "sell", 10)
    result = confirm_order(account.id, review.order_id)

    assert result.action == "sell"
    assert result.quantity == 10
    assert result.total == Decimal("250.00")  # 10 * $25.00

    # The Position row for (account, DEMO2) is gone, not kept at zero.
    assert _position_qty(account.id, demo2.id) is None

    # Proceeds were credited and the shares returned to inventory.
    assert _cash(account.id) == (starting_cash + Decimal("250.00"))
    assert _inventory(demo2.id) == starting_inventory + 10

    # The order completed with exactly one Transaction.
    assert _order_status(review.order_id) == ORDER_STATUS_COMPLETED
    assert _tx_count(review.order_id) == 1


# ---------------------------------------------------------------------------
# 2. Repeated confirmation executes at most once.
# ---------------------------------------------------------------------------

def test_repeated_confirmation_executes_at_most_once(seeded):
    """A second confirm of the same order is rejected and changes nothing.

    After a successful confirm the order is COMPLETED. Confirming the SAME
    order again must raise ``OrderNotPending`` with the exact milestone
    message, and the cash balance, position, and inventory must be identical
    to the state right after the first confirm (Req 12.1, 12.3, 17.2).
    """
    account = seeded.account
    demo1 = seeded.demo1

    review = prepare_order(account.id, demo1.id, "buy", 5)
    confirm_order(account.id, review.order_id)

    # Snapshot the post-first-confirm state from the DB.
    cash_after_first = _cash(account.id)
    position_after_first = _position_qty(account.id, demo1.id)
    inventory_after_first = _inventory(demo1.id)
    tx_after_first = _tx_count(review.order_id)
    events_after_first = _event_count(account.user_id)

    assert cash_after_first == Decimal("9750.00")
    assert position_after_first == 5
    assert inventory_after_first == 995
    assert tx_after_first == 1

    # Second confirm of the same order is rejected with the exact message.
    with pytest.raises(OrderNotPending) as excinfo:
        confirm_order(account.id, review.order_id)
    assert excinfo.value.message == (
        "This order has already been completed or cancelled."
    )

    # Nothing changed: cash, position, inventory, transaction, and event
    # counts match the state after the first (and only) execution.
    assert _cash(account.id) == cash_after_first
    assert _position_qty(account.id, demo1.id) == position_after_first
    assert _inventory(demo1.id) == inventory_after_first
    assert _tx_count(review.order_id) == tx_after_first
    assert _event_count(account.user_id) == events_after_first


# ---------------------------------------------------------------------------
# 3. Price-changed requires re-review.
# ---------------------------------------------------------------------------

def test_price_change_blocks_execution_and_leaves_order_pending(seeded):
    """A price change between review and confirm blocks the trade (Req 8.16).

    Prepare an order at the reviewed price, then change the stock's
    ``current_price`` in the DB. Confirm must raise ``PriceChanged`` and leave
    the order PENDING with no cash / position / inventory / transaction change,
    so the trainee has to review the updated price again.
    """
    account = seeded.account
    demo1 = seeded.demo1

    review = prepare_order(account.id, demo1.id, "buy", 5)

    # Snapshot pre-confirm DB state.
    starting_cash = _cash(account.id)
    starting_inventory = _inventory(demo1.id)
    starting_position = _position_qty(account.id, demo1.id)
    starting_events = _event_count(account.user_id)

    # Change the stock price in the DB after review but before confirm.
    stock = db.session.get(Stock, demo1.id)
    stock.current_price = Decimal("55.00")
    db.session.commit()

    with pytest.raises(PriceChanged):
        confirm_order(account.id, review.order_id)

    # The order is left PENDING for a fresh review.
    assert _order_status(review.order_id) == ORDER_STATUS_PENDING

    # No cash / position / inventory / transaction / event change.
    assert _cash(account.id) == starting_cash
    assert _inventory(demo1.id) == starting_inventory
    assert _position_qty(account.id, demo1.id) == starting_position
    assert _tx_count(review.order_id) == 0
    assert _event_count(account.user_id) == starting_events


# ---------------------------------------------------------------------------
# 4. Forced mid-trade DB error rolls back every change.
# ---------------------------------------------------------------------------

def test_mid_trade_failure_rolls_back_every_change(seeded, monkeypatch):
    """An exception mid-execution rolls the whole trade back (Req 10.13, 17.4).

    Inject a failure at a clean point inside ``confirm_order`` by monkeypatching
    the ``add_transaction`` helper (bound into the trading module's namespace)
    so it raises after the cash / position / inventory changes have been staged
    but before commit. The service's ``except`` block must ``rollback()`` so
    the DB ends up identical to the pre-execution state: order still PENDING,
    no Transaction, no EventLog, and cash / position / inventory unchanged.
    """
    account = seeded.account
    demo1 = seeded.demo1

    review = prepare_order(account.id, demo1.id, "buy", 5)

    # Snapshot the full pre-execution DB state.
    starting_cash = _cash(account.id)
    starting_inventory = _inventory(demo1.id)
    starting_position = _position_qty(account.id, demo1.id)
    starting_events = _event_count(account.user_id)

    # Force a mid-trade failure. ``add_transaction`` is called after cash,
    # position, and inventory have been mutated in the session but before the
    # commit, so this exercises the rollback of partially-applied changes.
    def _boom(*args, **kwargs):
        raise RuntimeError("injected mid-trade DB failure")

    monkeypatch.setattr(trading, "add_transaction", _boom)

    with pytest.raises(RuntimeError, match="injected mid-trade DB failure"):
        confirm_order(account.id, review.order_id)

    # Restore the real helper so read-backs below use a clean session; the
    # rollback already discarded the staged changes.
    monkeypatch.undo()

    # Every change is rolled back: DB state is identical to pre-execution.
    assert _order_status(review.order_id) == ORDER_STATUS_PENDING
    assert _cash(account.id) == starting_cash
    assert _inventory(demo1.id) == starting_inventory
    assert _position_qty(account.id, demo1.id) == starting_position

    # No permanent trade records were written.
    assert _tx_count(review.order_id) == 0
    assert _event_count(account.user_id) == starting_events


# ---------------------------------------------------------------------------
# 5. Cancelled or failed orders produce no Transaction.
# ---------------------------------------------------------------------------

def test_cancelled_order_produces_no_transaction(seeded):
    """Cancelling a PENDING order leaves no Transaction (Req 17.1, 17.2).

    A cancelled order never executed, so there is no completed-trade
    Transaction for it; the order ends CANCELLED and balances are untouched.
    """
    account = seeded.account
    demo1 = seeded.demo1

    review = prepare_order(account.id, demo1.id, "buy", 5)

    starting_cash = _cash(account.id)
    starting_inventory = _inventory(demo1.id)
    starting_position = _position_qty(account.id, demo1.id)

    cancel_order(account.id, review.order_id)

    assert _order_status(review.order_id) == ORDER_STATUS_CANCELLED
    assert _tx_count(review.order_id) == 0

    # Cancel changes no balances / positions / inventory.
    assert _cash(account.id) == starting_cash
    assert _inventory(demo1.id) == starting_inventory
    assert _position_qty(account.id, demo1.id) == starting_position


def test_failed_order_produces_no_transaction(seeded, monkeypatch):
    """A failed execution leaves no Transaction and the order PENDING (Req 17.2).

    When a confirm fails mid-trade (forced DB error), the rollback must leave
    no completed-trade Transaction and keep the order PENDING so it can be
    retried — the no-transaction invariant also holds for failed orders, not
    just cancelled ones.
    """
    account = seeded.account
    demo1 = seeded.demo1

    review = prepare_order(account.id, demo1.id, "buy", 5)

    def _boom(*args, **kwargs):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(trading, "add_transaction", _boom)

    with pytest.raises(RuntimeError):
        confirm_order(account.id, review.order_id)

    monkeypatch.undo()

    # The failed order wrote no Transaction and stayed PENDING.
    assert _tx_count(review.order_id) == 0
    assert _order_status(review.order_id) == ORDER_STATUS_PENDING
