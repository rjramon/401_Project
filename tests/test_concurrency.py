"""Concurrency and persistence integration tests (task 10.1).

These integration tests exercise the execute step (``confirm_order``) under
the three concurrency / durability scenarios called out by the design and the
acceptance criteria, asserting throughout on values **read back from the
separate MySQL test database** (never on the values passed in):

1. **Competing confirmations for the SAME order** keep cash, positions, and
   inventory correct with only one winner (Req 12.1, 12.3, 12.5, 17.2, 17.5;
   Property 12). See :func:`test_competing_confirmations_only_one_winner`.

2. **A refresh (GET) on the confirmation page does not re-execute the trade**:
   after a confirmed trade, a GET-equivalent read of the saved results changes
   nothing, and a repeated confirmation request is rejected without a second
   execution (Req 11.3, 17.3). See
   :func:`test_refresh_confirmation_does_not_reexecute`.

3. **Saved results survive a fresh app context / simulated restart**: after a
   confirmed trade we dispose the SQLAlchemy engine (dropping every pooled
   connection, as a process restart would) and re-read every record in a brand
   new application context; cash, positions, inventory, order status, the
   transaction, and the event log all match the saved results (Req 11.4,
   17.3). See :func:`test_saved_results_survive_simulated_restart`.

Approach notes
--------------
* **Competing confirmations.** The design makes only one confirmation win via
  an atomic conditional claim (``UPDATE ... WHERE status='PENDING'``) inside
  ``confirm_order``; the loser sees zero claimed rows and raises
  :class:`OrderNotPending`. We model two "competing" confirmation requests for
  the same ``order_id`` with a **sequential double-confirm**: call
  ``confirm_order`` on the same order twice. Exactly one call succeeds and the
  other raises :class:`OrderNotPending`, and the final cash/position/inventory
  reflect exactly one execution. This directly exercises the idempotency guard
  that resolves any race (the second request is always the one that finds the
  order no longer PENDING). A true two-thread race is intentionally NOT used:
  because ``confirm_order`` holds ``SELECT ... FOR UPDATE`` row locks on the
  order, account, stock, and position rows for the duration of a trade, two
  concurrent threads would simply serialize on those locks (one blocks until
  the other commits, then finds the order non-PENDING) — the same observable
  outcome as the sequential double-confirm, but prone to long lock-wait stalls
  in a shared single-connection test setup. The sequential model is therefore
  the robust, deterministic way to assert the guarantee.

* **Refresh (GET) does not re-execute.** The HTTP Confirmation_Page route and
  template are not part of this task's codebase yet (``app/routes`` only has
  package stubs), so there is no confirmation URL to GET. Per the task's
  allowance we assert the guarantee at the service level instead: reading the
  saved results back (the read a GET would perform) changes nothing, and the
  only state-changing path a refresh could trigger — another
  ``confirm_order`` — is rejected with :class:`OrderNotPending` and leaves the
  world untouched. This is exactly the behavior Req 11.3 / 17.3 require of a
  refresh.

* **Simulated restart.** A true process restart loses all in-memory/pooled
  state but keeps committed rows. We reproduce that by expiring the ORM
  identity map and disposing the engine (closing every pooled DB connection),
  then opening a fresh application context and querying again. Anything we can
  still read came from the committed database, not a cached object.

Database requirement
--------------------
These tests need the SEPARATE MySQL test database (``TEST_DATABASE_URL``). The
module-level ``pytestmark`` requests the DB-backed ``seeded`` fixture so, when
no test database is reachable, the shared conftest fixtures SKIP these tests
cleanly rather than failing (same behavior as the other DB-backed tests).
"""

from decimal import Decimal

import pytest
from flask import current_app

from app.extensions import db
from app.models import (
    Account,
    EventLog,
    Order,
    Position,
    Stock,
    Transaction,
    ORDER_STATUS_COMPLETED,
    ORDER_STATUS_PENDING,
)
from app.services.trading import (
    OrderNotPending,
    TradeResult,
    confirm_order,
    prepare_order,
)
from scripts.seed_demo import seed as seed_demo


#: Money is stored as DECIMAL(12,2); the two-decimal-place exponent.
_CENTS = Decimal("0.01")

# Every test here needs a reset, seeded test database. Requesting ``seeded``
# makes the whole module skip cleanly when the test DB is unreachable (the
# conftest fixtures handle the skip) and gives us an app context plus a first
# clean world for free.
pytestmark = pytest.mark.usefixtures("seeded")


# ---------------------------------------------------------------------------
# Helpers (mirror the _fresh_world / _state_snapshot pattern used by the
# property-based tests so each test starts from the exact demo world and
# asserts on values read straight back from the test DB).
# ---------------------------------------------------------------------------

def _fresh_world():
    """Reset the TEST database to an empty schema and load the demo world.

    Mirrors ``reset_db`` (drop_all + create_all on the test DB) followed by
    ``scripts/seed_demo.seed``, so each test starts from the exact, known demo
    state. Returns the ids/values the application would see, read back from
    the DB.
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
    # Capture plain ids/values now so callers do not depend on ORM instances
    # surviving a session.remove() or engine dispose later in a test.
    return {
        "account_id": account.id,
        "demo1_id": stocks["DEMO1"].id,
        "demo2_id": stocks["DEMO2"].id,
        "demo2_owned": position.quantity if position is not None else 0,
    }


def _state_snapshot(account_id):
    """Read the whole mutable world back as plain values for comparison.

    Captures cash, every position quantity, every stock inventory, every
    order's status, and the transaction and event-log counts — enough to prove
    a call "changed nothing" or "executed exactly once".
    """
    db.session.expire_all()
    account = db.session.get(Account, account_id)
    positions = {p.stock_id: p.quantity for p in db.session.query(Position).all()}
    inventory = {s.id: s.available_shares for s in db.session.query(Stock).all()}
    statuses = {o.id: o.status for o in db.session.query(Order).all()}
    return {
        "cash": account.cash_balance,
        "positions": positions,
        "inventory": inventory,
        "order_statuses": statuses,
        "transactions": db.session.query(Transaction).count(),
        "event_logs": db.session.query(EventLog).count(),
    }


def _prepare_buy_5_demo1(world):
    """Seed a known buy world and return a reviewed PENDING order id.

    DEMO1 @ $50.00, 1000 inventory, $10,000.00 cash, no DEMO1 position — the
    exact milestone buy scenario (Req 13). Returns ``order_id``.
    """
    stock = db.session.get(Stock, world["demo1_id"])
    stock.current_price = Decimal("50.00")
    stock.available_shares = 1000
    account = db.session.get(Account, world["account_id"])
    account.cash_balance = Decimal("10000.00")
    db.session.commit()

    review = prepare_order(world["account_id"], world["demo1_id"], "buy", 5)
    return review.order_id


# ---------------------------------------------------------------------------
# Scenario 1 — competing confirmations for the SAME order
# ---------------------------------------------------------------------------

# Feature: local-buy-and-sell, Property 12: Confirmation is idempotent
def test_competing_confirmations_only_one_winner():
    """Two confirmations for the same order execute it at most once.

    Models two competing confirmation requests for one PENDING order as a
    sequential double-confirm: ``confirm_order`` is called twice on the same
    ``order_id``. Exactly one call wins (returns a :class:`TradeResult`); the
    other is rejected with :class:`OrderNotPending`. The final cash, position,
    and inventory reflect a SINGLE buy of 5 DEMO1 @ $50 (cash $9,750.00, DEMO1
    position 5, inventory 995), with exactly one Transaction and one EventLog —
    not two.

    Approach: the design serializes any real race on ``SELECT ... FOR UPDATE``
    row locks plus an atomic conditional claim, so the second request always
    finds the order no longer PENDING. The sequential double-confirm exercises
    exactly that idempotency guard deterministically (see the module docstring
    for why a two-thread race is intentionally avoided). Post-conditions are
    read back from the test DB.

    **Validates: Requirements 12.1, 12.2, 12.3, 12.5, 17.2, 17.5**
    """
    world = _fresh_world()
    account_id = world["account_id"]
    demo1_id = world["demo1_id"]
    order_id = _prepare_buy_5_demo1(world)

    # First (winning) confirmation executes the trade exactly once.
    winner = confirm_order(account_id, order_id)
    assert isinstance(winner, TradeResult)

    # The competing confirmation for the SAME order is rejected: the order is
    # no longer PENDING, so nothing executes a second time.
    with pytest.raises(OrderNotPending):
        confirm_order(account_id, order_id)

    # Final state reflects a SINGLE execution, read back from the test DB. The
    # loser changed nothing — only one winner (the first confirmation).

    after = _state_snapshot(account_id)
    assert after["cash"] == Decimal("9750.00")
    assert after["positions"].get(demo1_id, 0) == 5
    assert after["inventory"][demo1_id] == 995
    assert after["order_statuses"][order_id] == ORDER_STATUS_COMPLETED
    # Executed at most once: exactly one Transaction and one EventLog exist.
    assert after["transactions"] == 1
    assert after["event_logs"] == 1
    txn = db.session.query(Transaction).one()
    assert txn.order_id == order_id
    assert txn.quantity == 5
    assert txn.total_amount == Decimal("250.00")


# ---------------------------------------------------------------------------
# Scenario 2 — a refresh (GET) does not re-execute the trade
# ---------------------------------------------------------------------------

# Feature: local-buy-and-sell, Property 12: Confirmation is idempotent
def test_refresh_confirmation_does_not_reexecute():
    """Refreshing the confirmation view after a completed trade changes nothing.

    After a confirmed buy we capture the saved results, then model a browser
    refresh of the Confirmation_Page. There is no HTTP confirmation route in
    this task's codebase yet, so (per the task's allowance) we model the
    refresh at the service level:

      * the GET-equivalent read of the saved results (cash, position,
        inventory, order, transaction, event log) returns the committed values
        unchanged, re-read repeatedly; and
      * the only state-changing path a refresh could trigger — issuing the
        confirmation again — is rejected with :class:`OrderNotPending` and
        leaves every balance, position, and inventory value unchanged, with
        still exactly one Transaction and one EventLog.

    **Validates: Requirements 11.3, 17.3, 12.3**
    """
    world = _fresh_world()
    account_id = world["account_id"]
    demo1_id = world["demo1_id"]
    order_id = _prepare_buy_5_demo1(world)

    # The trade is confirmed once (the Confirmation_Page would render here).
    confirm_order(account_id, order_id)
    saved = _state_snapshot(account_id)
    assert saved["cash"] == Decimal("9750.00")
    assert saved["positions"].get(demo1_id, 0) == 5
    assert saved["inventory"][demo1_id] == 995
    assert saved["transactions"] == 1
    assert saved["event_logs"] == 1

    # Refresh #1 and #2: a GET re-reads the saved results. Nothing changes.
    for _ in range(2):
        again = _state_snapshot(account_id)
        assert again == saved

    # A refresh must never execute the trade again. The only way a refresh
    # could change state is by re-issuing the confirmation; that is rejected
    # and still changes nothing (Req 11.3 / 17.3).
    with pytest.raises(OrderNotPending):
        confirm_order(account_id, order_id)

    after = _state_snapshot(account_id)
    assert after == saved
    assert after["order_statuses"][order_id] == ORDER_STATUS_COMPLETED
    # Still exactly one Transaction and one EventLog — no second execution.
    assert db.session.query(Transaction).count() == 1
    assert db.session.query(EventLog).count() == 1


# ---------------------------------------------------------------------------
# Scenario 3 — saved results survive a fresh app context / simulated restart
# ---------------------------------------------------------------------------

def test_saved_results_survive_simulated_restart():
    """Every saved record matches after a disposed engine and fresh context.

    After a confirmed buy of 5 DEMO1 @ $50 we capture the saved results, then
    simulate an application restart: expire the ORM identity map and dispose
    the engine so every pooled connection is dropped (as a real process
    restart would), and re-read everything in a brand new app context. The
    cash balance, positions, stock inventory, order status, the single
    Transaction, and the single EventLog all match the saved results — proving
    they came from the committed database, not cached objects.

    **Validates: Requirements 11.4, 17.3**
    """
    world = _fresh_world()
    account_id = world["account_id"]
    demo1_id = world["demo1_id"]
    order_id = _prepare_buy_5_demo1(world)

    confirm_order(account_id, order_id)

    # Capture the saved results as plain values before the simulated restart.
    saved = _state_snapshot(account_id)
    assert saved["cash"] == Decimal("9750.00")
    assert saved["positions"].get(demo1_id, 0) == 5
    assert saved["inventory"][demo1_id] == 995
    assert saved["order_statuses"][order_id] == ORDER_STATUS_COMPLETED
    assert saved["transactions"] == 1
    assert saved["event_logs"] == 1
    saved_txn = db.session.query(Transaction).one()
    saved_txn_values = {
        "order_id": saved_txn.order_id,
        "quantity": saved_txn.quantity,
        "execution_price": saved_txn.execution_price,
        "total_amount": saved_txn.total_amount,
    }
    saved_event = db.session.query(EventLog).one()
    saved_event_type = saved_event.event_type

    # Grab the live Flask app so we can open a brand new context after the
    # simulated restart. (The ``seeded`` fixture pushed this context.)
    app = current_app._get_current_object()

    # Simulated restart: drop all in-memory ORM state and close every pooled
    # DB connection. Nothing survives in Python; only committed rows remain.
    db.session.expire_all()
    db.session.remove()
    db.engine.dispose()

    # A brand new application context — as a freshly started process would get.
    with app.app_context():
        account = db.session.get(Account, account_id)
        assert account.cash_balance == saved["cash"]

        position = (
            db.session.query(Position)
            .filter_by(account_id=account_id, stock_id=demo1_id)
            .one()
        )
        assert position.quantity == 5

        stock = db.session.get(Stock, demo1_id)
        assert stock.available_shares == 995

        order = db.session.get(Order, order_id)
        assert order.status == ORDER_STATUS_COMPLETED
        assert order.completed_at is not None

        txn = db.session.query(Transaction).one()
        assert txn.order_id == saved_txn_values["order_id"]
        assert txn.quantity == saved_txn_values["quantity"]
        assert txn.execution_price == saved_txn_values["execution_price"]
        assert txn.total_amount == saved_txn_values["total_amount"]

        event = db.session.query(EventLog).one()
        assert event.event_type == saved_event_type

        # The full snapshot matches the saved results after the restart.
        restored = _state_snapshot(account_id)
        assert restored == saved
