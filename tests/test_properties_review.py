"""Hypothesis property-based tests for the review step (``prepare_order``).

Task 5.5. These tests exercise the six review-step correctness properties from
``design.md`` ("Correctness Properties") across many generated inputs. Each
property runs a MINIMUM of 100 Hypothesis examples (``@settings`` below).

How clean state is guaranteed per example
------------------------------------------
``prepare_order`` reads TRUSTED values straight from the test database (the
demo account's cash, the stock's price and inventory, and the owned position).
To test a property across many inputs we therefore have to vary those DB rows
and then read the resulting state back. Hypothesis drives many examples inside
a single pytest function, but the function-scoped ``seeded`` fixture only
resets once per function. So each example resets the schema and re-seeds the
exact demo world itself via :func:`_fresh_world`, mirroring ``reset_db`` +
``scripts/seed_demo.seed`` semantics. Every assertion reads state back from the
test DB, never from the value we passed in.

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

from app.extensions import db
from app.models import (
    Account,
    EventLog,
    Order,
    Position,
    Stock,
    Transaction,
    ORDER_STATUS_PENDING,
)
from app.services.trading import InvalidQuantity, prepare_order
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
    counts of orders, transactions and event logs — enough to prove a call
    "changed nothing else" (beyond, where expected, a single PENDING order).
    """
    account = db.session.get(Account, account_id)
    positions = {
        p.stock_id: p.quantity for p in db.session.query(Position).all()
    }
    inventory = {
        s.id: s.available_shares for s in db.session.query(Stock).all()
    }
    return {
        "cash": account.cash_balance,
        "positions": positions,
        "inventory": inventory,
        "orders": db.session.query(Order).count(),
        "transactions": db.session.query(Transaction).count(),
        "event_logs": db.session.query(EventLog).count(),
    }


# Strategies ---------------------------------------------------------------

# Prices as Decimals with exactly two places, strictly positive.
_prices = st.integers(min_value=1, max_value=100_000).map(
    lambda cents: (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"))
)

# Non-positive-whole quantities: blank/whitespace, nonnumeric, fractional,
# zero, and negative — every rejection path from Req 15.1-15.5.
_invalid_quantities = st.one_of(
    st.sampled_from(["", "   ", "abc", "1.5", "-0", "3.0.0", "一", "0", "-1", "-42"]),
    st.integers(max_value=0),
    st.floats(allow_nan=False, allow_infinity=False).filter(
        lambda f: f != int(f) if f == f else False
    ),
    st.decimals(allow_nan=False, allow_infinity=False, places=1).filter(
        lambda d: d != d.to_integral_value()
    ),
)


# ---------------------------------------------------------------------------
# Property 1
# ---------------------------------------------------------------------------

# Feature: local-buy-and-sell, Property 1: Total equals price times quantity at two decimals
@PBT_SETTINGS
@given(price=_prices, quantity=st.integers(min_value=1, max_value=10))
def test_property_1_total_is_price_times_quantity_two_decimals(seeded, price, quantity):
    """For any valid price and positive whole quantity, the reviewed order's
    total equals price * quantity stored at exactly two decimal places.

    **Validates: Requirements 5.3**
    """
    world = _fresh_world()
    stock = world.demo1
    # Set a trusted DB price and make sure inventory/cash never limit the buy
    # so this test isolates the arithmetic property.
    stock.current_price = price
    stock.available_shares = 10
    world.account.cash_balance = Decimal("100000000.00")
    db.session.commit()

    review = prepare_order(world.account.id, stock.id, "buy", quantity)

    expected = (price * quantity).quantize(Decimal("0.01"))
    assert review.total == expected
    # Stored at exactly two decimal places (DECIMAL(12,2)) — read back from DB.
    stored = db.session.get(Order, review.order_id).total
    assert stored == expected
    assert stored.as_tuple().exponent == -2


# ---------------------------------------------------------------------------
# Property 2
# ---------------------------------------------------------------------------

# Feature: local-buy-and-sell, Property 2: Preparing a valid order creates exactly one PENDING order and changes nothing else
@PBT_SETTINGS
@given(
    action=st.sampled_from(["buy", "sell"]),
    quantity=st.integers(min_value=1, max_value=5),
)
def test_property_2_valid_prepare_creates_one_pending_and_changes_nothing_else(
    seeded, action, quantity
):
    """A valid prepare creates exactly one PENDING order whose reviewed_price
    equals the current DB price, and leaves cash, positions, inventory, and
    transactions unchanged.

    **Validates: Requirements 6.1, 8.1**
    """
    world = _fresh_world()
    # Pick a target that is always affordable/available for both actions:
    # buy DEMO1 (1000 inventory, cheap vs seeded $10k) or sell the owned DEMO2
    # starter position (10 shares). Keep quantity <= 5 so both always pass.
    if action == "buy":
        stock = world.demo1
    else:
        stock = world.demo2

    before = _state_snapshot(world.account.id)

    review = prepare_order(world.account.id, stock.id, action, quantity)

    after = _state_snapshot(world.account.id)

    # Exactly one new PENDING order, and nothing else changed.
    orders = db.session.query(Order).all()
    assert len(orders) == 1
    order = orders[0]
    assert order.status == ORDER_STATUS_PENDING
    assert order.reviewed_price == stock.current_price
    assert review.order_id == order.id

    assert after["cash"] == before["cash"]
    assert after["positions"] == before["positions"]
    assert after["inventory"] == before["inventory"]
    assert after["transactions"] == before["transactions"]
    assert after["event_logs"] == before["event_logs"]


# ---------------------------------------------------------------------------
# Property 4
# ---------------------------------------------------------------------------

# Feature: local-buy-and-sell, Property 4: Invalid quantities are always rejected
@PBT_SETTINGS
@given(action=st.sampled_from(["buy", "sell"]), quantity_raw=_invalid_quantities)
def test_property_4_invalid_quantities_always_rejected(seeded, action, quantity_raw):
    """Any non-positive-whole-number quantity is rejected with the exact
    message "Enter a positive whole number." and makes no state change.

    **Validates: Requirements 8.7, 15.1, 15.2, 15.3, 15.4, 15.5**
    """
    world = _fresh_world()
    stock = world.demo1 if action == "buy" else world.demo2

    before = _state_snapshot(world.account.id)

    with pytest.raises(InvalidQuantity) as exc_info:
        prepare_order(world.account.id, stock.id, action, quantity_raw)
    assert exc_info.value.message == "Enter a positive whole number."

    after = _state_snapshot(world.account.id)
    assert after == before
    # No order was created on the rejection path.
    assert db.session.query(Order).count() == 0


# ---------------------------------------------------------------------------
# Property 5
# ---------------------------------------------------------------------------

# Feature: local-buy-and-sell, Property 5: A buy never spends more cash than the account holds
@PBT_SETTINGS
@given(
    price=_prices,
    quantity=st.integers(min_value=1, max_value=50),
    cash_cents=st.integers(min_value=0, max_value=1_000_000),
)
def test_property_5_buy_never_spends_more_cash_than_held(
    seeded, price, quantity, cash_cents
):
    """When price * quantity exceeds the DB cash balance, the buy is rejected
    with "Not enough money." and no state changes; otherwise the cash check
    passes (a PENDING order is created).

    **Validates: Requirements 8.9, 16.4**
    """
    from app.services.trading import NotEnoughMoney

    world = _fresh_world()
    stock = world.demo1
    stock.current_price = price
    # Make inventory never the limiting factor for this cash-focused property.
    stock.available_shares = 1_000_000
    cash = (Decimal(cash_cents) / Decimal(100)).quantize(Decimal("0.01"))
    world.account.cash_balance = cash
    db.session.commit()

    total = (price * quantity).quantize(Decimal("0.01"))
    before = _state_snapshot(world.account.id)

    if total > cash:
        with pytest.raises(NotEnoughMoney) as exc_info:
            prepare_order(world.account.id, stock.id, "buy", quantity)
        assert exc_info.value.message == "Not enough money."
        after = _state_snapshot(world.account.id)
        assert after == before
        assert db.session.query(Order).count() == 0
    else:
        review = prepare_order(world.account.id, stock.id, "buy", quantity)
        assert review.total == total
        # Cash check passed: one PENDING order, balances still unchanged
        # (review never moves money).
        assert db.session.query(Order).count() == 1
        after = _state_snapshot(world.account.id)
        assert after["cash"] == before["cash"]


# ---------------------------------------------------------------------------
# Property 6
# ---------------------------------------------------------------------------

# Feature: local-buy-and-sell, Property 6: A buy never exceeds available inventory
@PBT_SETTINGS
@given(
    inventory=st.integers(min_value=0, max_value=500),
    quantity=st.integers(min_value=1, max_value=1000),
)
def test_property_6_buy_never_exceeds_available_inventory(seeded, inventory, quantity):
    """When quantity exceeds the DB stock inventory, the buy is rejected with
    "Not enough shares available to buy." and no state changes.

    **Validates: Requirements 8.10, 16.5**
    """
    from app.services.trading import NotEnoughInventory

    world = _fresh_world()
    stock = world.demo1
    stock.available_shares = inventory
    # Keep the price small and cash large so cash is never the limiting factor.
    stock.current_price = Decimal("1.00")
    world.account.cash_balance = Decimal("100000000.00")
    db.session.commit()

    before = _state_snapshot(world.account.id)

    if quantity > inventory:
        with pytest.raises(NotEnoughInventory) as exc_info:
            prepare_order(world.account.id, stock.id, "buy", quantity)
        assert exc_info.value.message == "Not enough shares available to buy."
        after = _state_snapshot(world.account.id)
        assert after == before
        assert db.session.query(Order).count() == 0
    else:
        review = prepare_order(world.account.id, stock.id, "buy", quantity)
        assert review.quantity == quantity
        assert db.session.query(Order).count() == 1
        # Inventory is not decremented at the review step.
        after = _state_snapshot(world.account.id)
        assert after["inventory"] == before["inventory"]


# ---------------------------------------------------------------------------
# Property 7
# ---------------------------------------------------------------------------

# Feature: local-buy-and-sell, Property 7: A sell never exceeds the owned position
@PBT_SETTINGS
@given(
    owned=st.integers(min_value=0, max_value=100),
    quantity=st.integers(min_value=1, max_value=200),
)
def test_property_7_sell_never_exceeds_owned_position(seeded, owned, quantity):
    """When a sell quantity exceeds the DB position, the sell is rejected with
    "Not enough shares." and no state changes.

    **Validates: Requirements 8.11, 16.6**
    """
    from app.services.trading import NotEnoughShares

    world = _fresh_world()
    stock = world.demo2  # the stock the trainee holds a starter position in

    # Set the owned quantity precisely (delete the row for a zero holding so we
    # also cover the "no position" case).
    position = (
        db.session.query(Position)
        .filter_by(account_id=world.account.id, stock_id=stock.id)
        .one_or_none()
    )
    if owned == 0:
        if position is not None:
            db.session.delete(position)
    else:
        if position is None:
            position = Position(
                account_id=world.account.id, stock_id=stock.id, quantity=owned
            )
            db.session.add(position)
        else:
            position.quantity = owned
    db.session.commit()

    before = _state_snapshot(world.account.id)

    if quantity > owned:
        with pytest.raises(NotEnoughShares) as exc_info:
            prepare_order(world.account.id, stock.id, "sell", quantity)
        assert exc_info.value.message == "Not enough shares."
        after = _state_snapshot(world.account.id)
        assert after == before
        assert db.session.query(Order).count() == 0
    else:
        review = prepare_order(world.account.id, stock.id, "sell", quantity)
        assert review.quantity == quantity
        assert db.session.query(Order).count() == 1
        # The owned position is unchanged by the review step.
        after = _state_snapshot(world.account.id)
        assert after["positions"] == before["positions"]
