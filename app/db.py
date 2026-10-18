"""No-commit data-access helpers for the buy-and-sell milestone.

Every helper here is a thin wrapper over a SQLAlchemy ``Session``. They
query, add, flush, and delete, but they NEVER call ``commit()`` or
``rollback()``. The trading service (``app/services/trading.py``) owns the
single transaction boundary so that all six changes of a completed trade
land together or not at all (design: "Transaction boundary ownership").

Every helper takes an explicit ``session`` as its first parameter so the
service controls exactly which transaction the work happens in. All SQL
goes through SQLAlchemy with bound parameters; the few hand-written
statements use ``sqlalchemy.text`` with ``:named`` params — never f-strings
or string concatenation with user input.

Money is always ``Decimal`` (the ORM maps it to ``Numeric(12, 2)``); never
``float``.
"""

from datetime import datetime

from sqlalchemy import text

from .models import (
    Account,
    EventLog,
    Order,
    Position,
    Stock,
    Transaction,
    ORDER_STATUS_PENDING,
    ORDER_STATUS_EXECUTING,
)


# ---------------------------------------------------------------------------
# Task 3.1 — read / lookup helpers (query-only, no commits)
# ---------------------------------------------------------------------------

def get_demo_account(session):
    """Return the single backend-selected demo Account (Req 1.2, 1.3).

    This milestone preloads exactly one trainee User and one practice
    Account. The identity is chosen HERE on the backend — never from
    anything the browser sends. Selection rule: pick the Account with the
    lowest id (the single/first preloaded Account). With one seeded account
    this is deterministic and returns that account; it also stays safe if a
    second account ever appears.
    """
    return session.query(Account).order_by(Account.id.asc()).first()


def get_stock(session, stock_id):
    """Return the Stock with ``stock_id``, or ``None`` if it does not exist."""
    return session.get(Stock, stock_id)


def get_position(session, account_id, stock_id):
    """Return the Position for this account+stock, or ``None`` if none exists.

    There is at most one Position per (account_id, stock_id) because of the
    unique composite constraint on the Position table.
    """
    return (
        session.query(Position)
        .filter(Position.account_id == account_id, Position.stock_id == stock_id)
        .first()
    )


# ---------------------------------------------------------------------------
# Task 3.2 — locking and conditional-claim helpers (no commits)
# ---------------------------------------------------------------------------

def get_order_for_update(session, order_id):
    """Fetch an Order with a row lock (``SELECT ... FOR UPDATE``).

    The ``with_for_update()`` call tells the database to lock the selected
    Order row for the life of the current transaction, so a competing
    transaction cannot read-then-write the same order concurrently (design:
    concurrency; Req 12.2). The service holds the lock until it commits or
    rolls back — this helper does neither. Returns the Order or ``None``.
    """
    return (
        session.query(Order)
        .filter(Order.id == order_id)
        .with_for_update()
        .first()
    )


def lock_order_if_pending(session, order_id):
    """Atomically claim an Order: flip status PENDING -> EXECUTING.

    This is a single conditional UPDATE that only changes the row when its
    status is still PENDING. The database applies it atomically, so when two
    confirmation requests race, exactly ONE sees a row change and "wins" the
    claim; the other sees zero rows affected and must be rejected. That is
    how a trade executes at most once (idempotency / competing requests,
    Req 12.1, 12.3, 17.2).

    Uses ``sqlalchemy.text`` with bound parameters only (``:oid``,
    ``:executing``, ``:pending``) — never f-strings or string concatenation.
    The PENDING and EXECUTING values come from the ORDER_STATUS_* constants
    in ``app.models`` rather than hard-coded strings. Does NOT commit; the
    service owns the commit.

    Returns the number of rows affected (0 or 1) so the caller can tell
    whether this request won the claim.
    """
    result = session.execute(
        text(
            "UPDATE orders SET status = :executing "
            "WHERE id = :oid AND status = :pending"
        ),
        {
            "executing": ORDER_STATUS_EXECUTING,
            "oid": order_id,
            "pending": ORDER_STATUS_PENDING,
        },
    )
    return result.rowcount


# ---------------------------------------------------------------------------
# Task 3.3 — mutation helpers (add / flush only, never commit)
# ---------------------------------------------------------------------------

def upsert_position(session, account_id, stock_id, delta):
    """Create, increase, decrease, or delete a Position by ``delta``.

    ``delta`` is positive for a buy and negative for a sell. Behavior:
      * no Position yet and delta > 0  -> create one with quantity = delta;
      * Position exists                -> quantity += delta;
      * resulting quantity == 0        -> delete the Position row so a
        fully-sold holding is removed (Req 10.5, 17.1).

    Safety guard: this helper refuses to produce a negative quantity. The
    trading service already enforces the sell-shares check, but keeping the
    helper safe makes the rule obvious. Uses ``session.add`` /
    ``session.delete`` and ``session.flush()``; it never commits.

    Returns the resulting Position, or ``None`` if the Position was deleted
    (or if a no-op delta <= 0 arrives with no existing Position).
    """
    position = get_position(session, account_id, stock_id)

    if position is None:
        # Nothing owned yet. Only a positive delta (a buy) creates a row.
        if delta <= 0:
            return None
        position = Position(
            account_id=account_id, stock_id=stock_id, quantity=delta
        )
        session.add(position)
        session.flush()
        return position

    new_quantity = position.quantity + delta
    if new_quantity < 0:
        # Should never happen because the service checks owned shares first.
        raise ValueError("Position quantity cannot go negative.")

    if new_quantity == 0:
        # A fully-sold holding is removed rather than kept at zero.
        session.delete(position)
        session.flush()
        return None

    position.quantity = new_quantity
    session.flush()
    return position


def add_transaction(
    session,
    order_id,
    account_id,
    stock_id,
    action,
    quantity,
    execution_price,
    total_amount,
    completed_at,
):
    """Build and ``session.add`` a Transaction for a completed trade.

    Records the permanent trade detail (Req 10.9). Money values
    (``execution_price``, ``total_amount``) are ``Decimal``. Does NOT commit;
    the service commits the whole trade together. Returns the new Transaction.
    """
    transaction = Transaction(
        order_id=order_id,
        account_id=account_id,
        stock_id=stock_id,
        action=action,
        quantity=quantity,
        execution_price=execution_price,
        total_amount=total_amount,
        completed_at=completed_at,
    )
    session.add(transaction)
    return transaction


def add_event_log(session, user_id, event_type, description, created_at=None):
    """Build and ``session.add`` an EventLog entry (Req 10.10).

    ``created_at`` defaults to the current UTC time when not supplied. Does
    NOT commit; the service commits. Returns the new EventLog.
    """
    if created_at is None:
        created_at = datetime.utcnow()
    event = EventLog(
        user_id=user_id,
        event_type=event_type,
        description=description,
        created_at=created_at,
    )
    session.add(event)
    return event
