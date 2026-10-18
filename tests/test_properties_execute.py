"""Hypothesis property-based tests for the execute step (``confirm_order``).

Task 6.4. These tests exercise the execute-step correctness properties
(Properties 8-14) from ``design.md`` ("Correctness Properties") across many
generated inputs. Each property runs a MINIMUM of 100 Hypothesis examples
(``@settings`` below).

How clean state is guaranteed per example
------------------------------------------
``confirm_order`` reads TRUSTED values straight from the test database (the
demo account's cash, the stock's price and inventory, and the owned position),
executes the trade inside a single transaction, and commits. To test a
property across many inputs we vary those DB rows, run a full prepare +
confirm, and read the resulting state back. Hypothesis drives many examples
inside a single pytest function, but the function-scoped ``seeded`` fixture
only resets once per function. So each example resets the schema and re-seeds
the exact demo world itself via :func:`_fresh_world`, mirroring ``reset_db`` +
``scripts/seed_demo.seed`` semantics. Every assertion reads state back from the
test DB, never from the value we passed in.

Failure injection (Properties 11 and 13)
-----------------------------------------
To prove the "all-or-nothing" rollback guarantee we make the execute step fail
partway through by replacing the ``add_transaction`` helper that
``app.services.trading`` imported into its own namespace with one that raises.
That mirrors a real mid-trade DB error (the last writes before commit) without
touching production code. The patch is always restored in a ``finally`` block
so one example can never leak into the next.

Database requirement
--------------------
These tests need the SEPARATE MySQL test database (``TEST_DATABASE_URL``). The
module-level ``pytestmark`` requests the DB-backed fixtures so, when no test
database is reachable, the shared conftest fixtures SKIP these tests cleanly
rather than failing (same behavior as the other DB-backed tests).
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import app.services.trading as trading
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
from app.services.trading import (
    OrderNotPending,
    PriceChanged,
    cancel_order,
    confirm_order,
    prepare_order,
)
from scripts.seed_demo import seed as seed_demo


# Minimum Hypothesis examples per property (task requires >= 100).
MAX_EXAMPLES = 100

# Hypothesis reuses one pytest function across many examples; the function-
# scoped fixtures only set up once. We reset/seed inside each example instead,
# so silence the "function-scoped fixture" health check (we do not rely on the
# fixture resetting per example).
PBT_SETTINGS = settings(
    max_examples=MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Every test in this module needs a reset, seeded test database. Requesting
# ``seeded`` here makes the whole module skip cleanly when the test DB is
# unreachable (the conftest fixtures handle the skip), and gives us an app
# context plus a first clean world for free.
pytestmark = pytest.mark.usefixtures("seeded")

#: Money is stored as DECIMAL(12,2); the two-decimal-place exponent.
_CENTS = Decimal("0.01")


# ---------------------------------------------------------------------------
# Per-example helpers
# ---------------------------------------------------------------------------

def _fresh_world():
    """Reset the TEST database to an empty schema and load the demo world.

    Mirrors ``reset_db`` (drop_all + create_all on the test DB) followed by
    ``scripts/seed_demo.seed``, so each Hypothesis example starts from the
    exact, known demo state. Returns a small namespace of the seeded records
    read back from the DB (ids and values the application would see).
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
    counts of transactions and event logs plus every order's status — enough
    to prove a call "changed nothing" or "changed exactly the expected rows".
    """
    account = db.session.get(Account, account_id)
    positions = {
        p.stock_id: p.quantity for p in db.session.query(Position).all()
    }
    inventory = {
        s.id: s.available_shares for s in db.session.query(Stock).all()
    }
    statuses = {o.id: o.status for o in db.session.query(Order).all()}
    return {
        "cash": account.cash_balance,
        "positions": positions,
        "inventory": inventory,
        "order_statuses": statuses,
        "transactions": db.session.query(Transaction).count(),
        "event_logs": db.session.query(EventLog).count(),
    }


def _set_owned(account_id, stock_id, owned):
    """Force the owned position for an account+stock to ``owned`` shares.

    Deletes the row for a zero holding so the "no position" case is covered;
    otherwise creates or updates the single Position row.
    """
    position = (
        db.session.query(Position)
        .filter_by(account_id=account_id, stock_id=stock_id)
        .one_or_none()
    )
    if owned == 0:
        if position is not None:
            db.session.delete(position)
    elif position is None:
        db.session.add(
            Position(account_id=account_id, stock_id=stock_id, quantity=owned)
        )
    else:
        position.quantity = owned
    db.session.commit()


def _two_places(value):
    """True when a Decimal is stored at exactly two decimal places."""
    return isinstance(value, Decimal) and value.as_tuple().exponent == -2


# Strategies ---------------------------------------------------------------

# Prices as Decimals with exactly two places, strictly positive. The upper
# bound keeps buy totals affordable within the seeded $10,000 cash.
_prices = st.integers(min_value=1, max_value=50_000).map(
    lambda cents: (Decimal(cents) / Decimal(100)).quantize(_CENTS)
)


# ---------------------------------------------------------------------------
# Property 8
# ---------------------------------------------------------------------------

# Feature: local-buy-and-sell, Property 8: Confirmation requires the reviewed price to still match the database price
@PBT_SETTINGS
@given(
    action=st.sampled_from(["buy", "sell"]),
    quantity=st.integers(min_value=1, max_value=5),
    new_price=_prices,
)
def test_property_8_confirm_blocks_when_price_changed(
    seeded, action, quantity, new_price
):
    """If the DB price no longer matches the order's ``reviewed_price`` at
    confirm time, execution is blocked with :class:`PriceChanged`, the order
    stays PENDING, and no cash/position/inventory/transaction/event changes.

    **Validates: Requirements 8.16**
    """
    world = _fresh_world()
    if action == "buy":
        stock = world.demo1
        stock.current_price = Decimal("50.00")
        stock.available_shares = 1000
        world.account.cash_balance = Decimal("10000.00")
    else:
        stock = world.demo2
        stock.current_price = Decimal("25.00")
        _set_owned(world.account.id, stock.id, 10)
    db.session.commit()

    review = prepare_order(world.account.id, stock.id, action, quantity)

    # Move the DB price to something different from the reviewed price.
    stock = db.session.get(Stock, stock.id)
    if new_price == stock.current_price:
        new_price = (new_price + _CENTS).quantize(_CENTS)
    stock.current_price = new_price
    db.session.commit()

    before = _state_snapshot(world.account.id)

    with pytest.raises(PriceChanged):
        confirm_order(world.account.id, review.order_id)

    after = _state_snapshot(world.account.id)
    assert after == before
    # The order must be returned to PENDING so the trainee reviews again.
    assert db.session.get(Order, review.order_id).status == ORDER_STATUS_PENDING
    assert db.session.query(Transaction).count() == 0


# ---------------------------------------------------------------------------
# Property 9
# ---------------------------------------------------------------------------

# Feature: local-buy-and-sell, Property 9: A confirmed trade applies the correct deltas and conserves shares
@PBT_SETTINGS
@given(
    action=st.sampled_from(["buy", "sell"]),
    quantity=st.integers(min_value=1, max_value=5),
)
def test_property_9_confirmed_trade_applies_deltas_and_conserves_shares(
    seeded, action, quantity
):
    """A confirmed buy decreases cash by ``total``, increases the position by
    ``quantity`` and decreases inventory by ``quantity``; a sell is the mirror
    image. In both cases the shares removed from one side equal those added to
    the other (shares are conserved).

    **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.6, 10.7**
    """
    world = _fresh_world()
    if action == "buy":
        stock = world.demo1
        stock.current_price = Decimal("50.00")
        stock.available_shares = 1000
        world.account.cash_balance = Decimal("10000.00")
        _set_owned(world.account.id, stock.id, 0)
    else:
        stock = world.demo2
        stock.current_price = Decimal("25.00")
        _set_owned(world.account.id, stock.id, 10)
    db.session.commit()

    stock_id = stock.id
    before = _state_snapshot(world.account.id)
    before_pos = before["positions"].get(stock_id, 0)
    before_inv = before["inventory"][stock_id]

    review = prepare_order(world.account.id, stock_id, action, quantity)
    total = review.total
    confirm_order(world.account.id, review.order_id)

    after = _state_snapshot(world.account.id)
    after_pos = after["positions"].get(stock_id, 0)
    after_inv = after["inventory"][stock_id]

    if action == "buy":
        assert after["cash"] == (before["cash"] - total).quantize(_CENTS)
        assert after_pos == before_pos + quantity
        assert after_inv == before_inv - quantity
        # Shares leaving inventory equal shares added to the position.
        assert (before_inv - after_inv) == (after_pos - before_pos)
    else:
        assert after["cash"] == (before["cash"] + total).quantize(_CENTS)
        assert after_pos == before_pos - quantity
        assert after_inv == before_inv + quantity
        # Shares leaving the position equal shares returned to inventory.
        assert (before_pos - after_pos) == (after_inv - before_inv)


# ---------------------------------------------------------------------------
# Property 10
# ---------------------------------------------------------------------------

# Feature: local-buy-and-sell, Property 10: A completed trade produces exactly one transaction and one event log, all atomically
@PBT_SETTINGS
@given(
    action=st.sampled_from(["buy", "sell"]),
    quantity=st.integers(min_value=1, max_value=5),
)
def test_property_10_completed_trade_one_transaction_one_event_atomic(
    seeded, action, quantity
):
    """After a confirmed trade the order is COMPLETED, exactly one Transaction
    references that order, exactly one EventLog records that trade, and all
    six changes are committed together (counts move from 0 to 1).

    **Validates: Requirements 10.8, 10.9, 10.10, 10.11**
    """
    world = _fresh_world()
    if action == "buy":
        stock = world.demo1
        stock.current_price = Decimal("50.00")
        stock.available_shares = 1000
        world.account.cash_balance = Decimal("10000.00")
    else:
        stock = world.demo2
        stock.current_price = Decimal("25.00")
        _set_owned(world.account.id, stock.id, 10)
    db.session.commit()

    review = prepare_order(world.account.id, stock.id, action, quantity)
    confirm_order(world.account.id, review.order_id)

    order = db.session.get(Order, review.order_id)
    assert order.status == ORDER_STATUS_COMPLETED
    assert order.completed_at is not None

    # Exactly one Transaction, and it references this order.
    txns = db.session.query(Transaction).all()
    assert len(txns) == 1
    assert txns[0].order_id == review.order_id

    # Exactly one EventLog for the trade.
    assert db.session.query(EventLog).count() == 1


# ---------------------------------------------------------------------------
# Property 11
# ---------------------------------------------------------------------------

# Feature: local-buy-and-sell, Property 11: Any failure during execution rolls back every change
@PBT_SETTINGS
@given(
    action=st.sampled_from(["buy", "sell"]),
    quantity=st.integers(min_value=1, max_value=5),
)
def test_property_11_failure_during_execution_rolls_back_everything(
    seeded, action, quantity
):
    """When a failure is injected during execution, the full DB state after
    rollback (cash, positions, inventory, order back to PENDING, no
    transaction, no event log) is identical to the pre-execution state.

    **Validates: Requirements 10.12, 17.4**
    """
    world = _fresh_world()
    if action == "buy":
        stock = world.demo1
        stock.current_price = Decimal("50.00")
        stock.available_shares = 1000
        world.account.cash_balance = Decimal("10000.00")
    else:
        stock = world.demo2
        stock.current_price = Decimal("25.00")
        _set_owned(world.account.id, stock.id, 10)
    db.session.commit()

    review = prepare_order(world.account.id, stock.id, action, quantity)

    # Snapshot the exact pre-execution state (order is PENDING here).
    before = _state_snapshot(world.account.id)

    # Inject a mid-trade failure: the last write before commit raises, so the
    # whole transaction must roll back. Always restore the real helper.
    def _boom(*args, **kwargs):
        raise RuntimeError("injected mid-trade DB error")

    original = trading.add_transaction
    trading.add_transaction = _boom
    try:
        with pytest.raises(RuntimeError):
            confirm_order(world.account.id, review.order_id)
    finally:
        trading.add_transaction = original

    # The ORM session was rolled back; expire everything and re-read.
    db.session.expire_all()
    after = _state_snapshot(world.account.id)

    assert after == before
    assert db.session.get(Order, review.order_id).status == ORDER_STATUS_PENDING
    assert db.session.query(Transaction).count() == 0
    assert db.session.query(EventLog).count() == 0


# ---------------------------------------------------------------------------
# Property 12
# ---------------------------------------------------------------------------

# Feature: local-buy-and-sell, Property 12: Confirmation is idempotent
@PBT_SETTINGS
@given(
    action=st.sampled_from(["buy", "sell"]),
    quantity=st.integers(min_value=1, max_value=5),
    extra_calls=st.integers(min_value=1, max_value=3),
)
def test_property_12_confirmation_is_idempotent(
    seeded, action, quantity, extra_calls
):
    """Calling confirmation more than once executes the trade at most once.
    The first call succeeds; every later call raises :class:`OrderNotPending`
    and leaves cash, positions, and inventory unchanged.

    **Validates: Requirements 12.1, 12.3, 17.2**
    """
    world = _fresh_world()
    if action == "buy":
        stock = world.demo1
        stock.current_price = Decimal("50.00")
        stock.available_shares = 1000
        world.account.cash_balance = Decimal("10000.00")
    else:
        stock = world.demo2
        stock.current_price = Decimal("25.00")
        _set_owned(world.account.id, stock.id, 10)
    db.session.commit()

    review = prepare_order(world.account.id, stock.id, action, quantity)

    # First confirm wins and executes exactly once.
    confirm_order(world.account.id, review.order_id)
    db.session.expire_all()
    after_first = _state_snapshot(world.account.id)
    assert after_first["transactions"] == 1
    assert after_first["event_logs"] == 1

    # Every later confirm is rejected and changes nothing.
    for _ in range(extra_calls):
        with pytest.raises(OrderNotPending):
            confirm_order(world.account.id, review.order_id)
        db.session.expire_all()
        after_again = _state_snapshot(world.account.id)
        assert after_again == after_first

    # Still exactly one Transaction and one EventLog: executed at most once.
    assert db.session.query(Transaction).count() == 1
    assert db.session.query(EventLog).count() == 1


# ---------------------------------------------------------------------------
# Property 13
# ---------------------------------------------------------------------------

# Feature: local-buy-and-sell, Property 13: A cancelled or failed order yields no completed-trade transaction
@PBT_SETTINGS
@given(
    action=st.sampled_from(["buy", "sell"]),
    quantity=st.integers(min_value=1, max_value=5),
    outcome=st.sampled_from(["cancelled", "failed"]),
)
def test_property_13_cancelled_or_failed_order_has_no_transaction(
    seeded, action, quantity, outcome
):
    """An order that is cancelled, or whose execution fails, leaves no
    Transaction referencing that order.

    **Validates: Requirements 10.13, 9.8**
    """
    world = _fresh_world()
    if action == "buy":
        stock = world.demo1
        stock.current_price = Decimal("50.00")
        stock.available_shares = 1000
        world.account.cash_balance = Decimal("10000.00")
    else:
        stock = world.demo2
        stock.current_price = Decimal("25.00")
        _set_owned(world.account.id, stock.id, 10)
    db.session.commit()

    review = prepare_order(world.account.id, stock.id, action, quantity)

    if outcome == "cancelled":
        cancel_order(world.account.id, review.order_id)
        assert (
            db.session.get(Order, review.order_id).status
            == ORDER_STATUS_CANCELLED
        )
    else:  # failed execution
        def _boom(*args, **kwargs):
            raise RuntimeError("injected mid-trade DB error")

        original = trading.add_transaction
        trading.add_transaction = _boom
        try:
            with pytest.raises(RuntimeError):
                confirm_order(world.account.id, review.order_id)
        finally:
            trading.add_transaction = original
        db.session.expire_all()
        # A failed order rolls back to PENDING (not COMPLETED).
        assert (
            db.session.get(Order, review.order_id).status
            == ORDER_STATUS_PENDING
        )

    # In neither outcome does a Transaction exist for the order.
    assert (
        db.session.query(Transaction)
        .filter_by(order_id=review.order_id)
        .count()
        == 0
    )
    assert db.session.query(Transaction).count() == 0


# ---------------------------------------------------------------------------
# Property 14
# ---------------------------------------------------------------------------

# Feature: local-buy-and-sell, Property 14: Stored money always has two decimal places
@PBT_SETTINGS
@given(
    action=st.sampled_from(["buy", "sell"]),
    quantity=st.integers(min_value=1, max_value=5),
    price=_prices,
)
def test_property_14_stored_money_has_two_decimal_places(
    seeded, action, quantity, price
):
    """For any completed trade, the stored cash balance, the transaction
    ``total_amount`` and the ``execution_price`` are each stored at exactly
    two decimal places.

    **Validates: Requirements 17.2** (and the money-rule from the design)
    """
    world = _fresh_world()
    if action == "buy":
        stock = world.demo1
        stock.current_price = price
        stock.available_shares = 1000
        # Give enough cash to afford any price in range at quantity <= 5.
        world.account.cash_balance = Decimal("10000000.00")
        _set_owned(world.account.id, stock.id, 0)
    else:
        stock = world.demo2
        stock.current_price = price
        _set_owned(world.account.id, stock.id, 10)
    db.session.commit()

    review = prepare_order(world.account.id, stock.id, action, quantity)
    confirm_order(world.account.id, review.order_id)

    db.session.expire_all()
    account = db.session.get(Account, world.account.id)
    txn = (
        db.session.query(Transaction)
        .filter_by(order_id=review.order_id)
        .one()
    )
    order = db.session.get(Order, review.order_id)

    assert _two_places(account.cash_balance)
    assert _two_places(txn.total_amount)
    assert _two_places(txn.execution_price)
    assert _two_places(order.total)
    assert _two_places(order.reviewed_price)
