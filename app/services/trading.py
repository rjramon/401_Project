"""Trading service: typed errors and plain result data.

This module holds the Flask-free, page-free trading logic for the STS
buy-and-sell milestone. The review and confirm steps (``prepare_order``,
``confirm_order``, ``cancel_order``) are added in later tasks; this task
defines the two building blocks they depend on:

1. A small family of typed exceptions (:class:`TradeError` and subclasses).
   Each one carries the *exact* user-facing message in a ``.message``
   attribute, so routes can show ``err.message`` to the trainee without
   re-deriving text. The default message per subclass is fixed by the
   design's Error Handling table; callers may override it when a situation
   needs more specific wording.

2. Two plain dataclasses (:class:`OrderReview` and :class:`TradeResult`) that
   carry trade data between the service, routes, and templates. Keeping these
   as plain data means routes and templates never touch ORM internals and
   tests can assert on ordinary Python values.

Money values are always ``Decimal`` (never ``float``); share quantities are
plain integers (see ``app/models.py``).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from ..db import (
    add_event_log,
    add_transaction,
    get_demo_account,
    get_order_for_update,
    get_position,
    get_stock,
    lock_order_if_pending,
    upsert_position,
)
from ..extensions import db
from ..models import (
    Order,
    Position,
    Stock,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_COMPLETED,
    ORDER_STATUS_PENDING,
)

#: Money is stored as DECIMAL(12,2); every money value we persist or return is
#: quantized to exactly two decimal places with this exponent.
_CENTS = Decimal("0.01")

#: The two allowed trade actions.
_BUY = "buy"
_SELL = "sell"


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------
#
# Every trade failure is one of these. The base class stores the plain text
# shown to the trainee in ``.message``; each subclass sets a sensible default
# so raising ``NotEnoughMoney()`` already carries the right words. A caller
# can still pass a custom message to override the default when needed.


class TradeError(Exception):
    """Base class for all trade failures.

    ``.message`` is the plain text shown to the trainee. Subclasses provide a
    default via :attr:`default_message`; passing ``message`` overrides it.
    """

    #: Default user-facing text. Subclasses override this.
    default_message = "That trade could not be completed."

    def __init__(self, message=None):
        # Fall back to the subclass default when no explicit message is given.
        self.message = message if message is not None else self.default_message
        super().__init__(self.message)


class InvalidQuantity(TradeError):
    """Quantity is blank, zero, negative, fractional, or nonnumeric."""

    default_message = "Enter a positive whole number."


class NotEnoughMoney(TradeError):
    """Buy cost exceeds the account's cash balance."""

    default_message = "Not enough money."


class NotEnoughShares(TradeError):
    """Sell quantity exceeds the shares the account owns."""

    default_message = "Not enough shares."


class NotEnoughInventory(TradeError):
    """Buy quantity exceeds the stock's available inventory."""

    default_message = "Not enough shares available to buy."


class OrderNotPending(TradeError):
    """Order was already completed or cancelled (or claimed by another request)."""

    default_message = "This order has already been completed or cancelled."


class PriceUnavailable(TradeError):
    """The stock's DB price is missing or not positive."""

    default_message = "This stock's price is unavailable right now."


class InvalidTarget(TradeError):
    """Missing stock/account, or an order owned by another account."""

    default_message = "That trade is not available."


class PriceChanged(TradeError):
    """DB price no longer matches the price shown at review."""

    default_message = "The price changed. Please review the updated price again."


# ---------------------------------------------------------------------------
# Plain result data
# ---------------------------------------------------------------------------
#
# These carry trade data out of the service as ordinary values. ``OrderReview``
# is what ``prepare_order`` returns for the review page (what the trade *would*
# do, incl. the cash the account would have afterward). ``TradeResult`` is what
# ``confirm_order`` returns after the trade commits (incl. the saved cash).


@dataclass
class OrderReview:
    """What a proposed trade would do, for the review page.

    ``expected_cash`` is the cash balance the account *would* have if the
    trade were confirmed; no balance is changed when this is produced.
    """

    company: str
    ticker: str
    action: str
    quantity: int
    price: Decimal
    total: Decimal
    expected_cash: Decimal
    order_id: int


@dataclass
class TradeResult:
    """The outcome of a confirmed, committed trade.

    ``actual_cash`` is the account's saved cash balance after the trade.
    """

    company: str
    ticker: str
    action: str
    quantity: int
    price: Decimal
    total: Decimal
    actual_cash: Decimal
    order_id: int


@dataclass
class CancelResult:
    """The outcome of cancelling a still-pending order.

    Carries the cancelled order's id and its ``stock_id`` so a route can
    redirect the trainee back to the Stock_Details_Page (Req 7.2) without
    touching ORM internals.
    """

    order_id: int
    stock_id: int


# ---------------------------------------------------------------------------
# Quantity parsing
# ---------------------------------------------------------------------------
#
# The browser sends the quantity as a raw string (or nothing). A valid trade
# quantity is a POSITIVE WHOLE number of shares. This helper is the single
# place that decides what counts as valid, so every rejection path raises the
# same InvalidQuantity error with the one exact message (Req 15.1-15.5).


def _parse_quantity(quantity_raw):
    """Parse ``quantity_raw`` into a positive ``int`` share count.

    Rejects blank, nonnumeric, fractional, zero, and negative input by raising
    :class:`InvalidQuantity` ("Enter a positive whole number."). Accepts an
    ``int`` directly and a string that names a positive whole number (e.g.
    ``"5"``). A value like ``"1.5"`` or ``"abc"`` or ``""`` is rejected
    (Req 8.7, 15.1-15.5).
    """
    # Blank / missing: covers None and empty-or-whitespace strings.
    if quantity_raw is None:
        raise InvalidQuantity()
    if isinstance(quantity_raw, str):
        quantity_raw = quantity_raw.strip()
        if quantity_raw == "":
            raise InvalidQuantity()

    # Reject floats outright: money/shares never arrive as float, and a float
    # cannot represent a "whole number" intent we want to trust.
    if isinstance(quantity_raw, float):
        raise InvalidQuantity()

    # Parse through Decimal so "1.5" (fractional) and "abc" (nonnumeric) are
    # both caught cleanly, and so "5" / "5.0" are understood as numbers.
    try:
        value = Decimal(quantity_raw)
    except (InvalidOperation, ValueError, TypeError):
        raise InvalidQuantity()

    # Fractional quantities are not allowed (whole shares only).
    if value != value.to_integral_value():
        raise InvalidQuantity()

    quantity = int(value)

    # Zero and negative are not positive whole numbers.
    if quantity <= 0:
        raise InvalidQuantity()

    return quantity


# ---------------------------------------------------------------------------
# prepare_order (review step)
# ---------------------------------------------------------------------------


def prepare_order(account_id, stock_id, action, quantity_raw):
    """Validate a proposed trade and create one PENDING Order for review.

    This is the review step from the design. It reads TRUSTED values (the
    demo account, the stock, and the current position) from the database and
    runs every Pre-Execution Check (Req 8). It never trusts price, balance,
    ownership, or inventory values from the browser (Req 8.14, 8.15).

    On success it computes ``total = price * quantity`` (a ``Decimal`` at two
    decimal places), creates exactly ONE :class:`Order` with status
    ``PENDING`` storing ``reviewed_price`` and ``total``, and commits that new
    order so its id survives the request for the review page. It makes NO
    cash, position, or inventory change (Req 6.1, 8.1).

    Parameters are plain values (no Flask request, no page rendering):
      * ``account_id`` / ``stock_id`` — ints identifying the trade target;
      * ``action`` — ``"buy"`` or ``"sell"``;
      * ``quantity_raw`` — the raw submitted quantity (string or int).

    Returns an :class:`OrderReview` describing what the trade would do,
    including the expected cash balance afterward. Raises a :class:`TradeError`
    subclass on any failed check and persists nothing in that case.
    """
    session = db.session

    # 1. Resolve the trusted identity on the backend. The demo account is
    #    chosen by the server (Req 1.2, 1.3); any account_id from the browser
    #    must match that single backend-selected account, otherwise the trade
    #    targets a record this trainee does not own (Req 8.4, 16.2, 16.3).
    account = get_demo_account(session)
    if account is None or account.id != account_id:
        raise InvalidTarget()

    # 2. The selected stock must exist (Req 8.5, 16.1).
    stock = get_stock(session, stock_id)
    if stock is None:
        raise InvalidTarget()

    # 3. The action must be buy or sell (Req 8.6). Normalize case/whitespace so
    #    "Buy" and "BUY" are accepted, then store the canonical lowercase form.
    normalized_action = action.strip().lower() if isinstance(action, str) else action
    if normalized_action not in (_BUY, _SELL):
        raise InvalidTarget()

    # 4. Quantity must be a positive whole number (Req 8.7, 15.1-15.5).
    quantity = _parse_quantity(quantity_raw)

    # 5. The current DB price must be greater than zero (Req 5.4, 8.8). Guard
    #    against a missing price too.
    price = stock.current_price
    if price is None or price <= 0:
        raise PriceUnavailable()

    # Total cost (buy) or proceeds (sell): exact Decimal math, two places.
    # quantity is an int, so price * quantity is exact at two decimals.
    total = (price * quantity).quantize(_CENTS)

    # 6. Action-specific affordability / availability checks against trusted
    #    DB values.
    if normalized_action == _BUY:
        # Enough cash for the purchase (Req 8.9, 16.4).
        if total > account.cash_balance:
            raise NotEnoughMoney()
        # Enough shares available in inventory to buy (Req 8.10, 16.5).
        if quantity > stock.available_shares:
            raise NotEnoughInventory()
        expected_cash = (account.cash_balance - total).quantize(_CENTS)
    else:  # sell
        # Enough owned shares to sell (Req 8.11, 16.6).
        position = get_position(session, account_id, stock_id)
        owned = position.quantity if position is not None else 0
        if quantity > owned:
            raise NotEnoughShares()
        expected_cash = (account.cash_balance + total).quantize(_CENTS)

    # 7. All checks passed. Create exactly ONE PENDING order capturing the
    #    reviewed price and total. No cash/position/inventory change is made.
    order = Order(
        account_id=account.id,
        stock_id=stock.id,
        action=normalized_action,
        quantity=quantity,
        reviewed_price=price,
        total=total,
        status=ORDER_STATUS_PENDING,
    )
    session.add(order)
    # Commit so the PENDING order (and its id) persists for the review page and
    # the later confirm/cancel request. The no-commit rule applies to db.py
    # helpers; the trading service owns this boundary.
    session.commit()

    return OrderReview(
        company=stock.company_name,
        ticker=stock.ticker,
        action=normalized_action,
        quantity=quantity,
        price=price,
        total=total,
        expected_cash=expected_cash,
        order_id=order.id,
    )


# ---------------------------------------------------------------------------
# confirm_order (execute step)
# ---------------------------------------------------------------------------
#
# The event type recorded in the EventLog for a completed trade.
_EVENT_TRADE_COMPLETED = "TRADE_COMPLETED"


def _recheck_pre_execution(stock, account, position, action, quantity, total):
    """Re-run the action-specific Pre-Execution Checks at confirm time (Req 8.2).

    This mirrors the affordability / availability checks from ``prepare_order``
    but runs against the freshly re-read, row-locked DB values so a competing
    trade cannot have spent the same cash or shares since review. ``stock``,
    ``account``, and ``position`` are the trusted records read with
    ``SELECT ... FOR UPDATE``; ``position`` may be ``None`` when nothing is
    owned. Raises the matching :class:`TradeError` on any failed check;
    returns ``None`` when every check passes.
    """
    # The DB price must still be present and positive (Req 8.8).
    price = stock.current_price
    if price is None or price <= 0:
        raise PriceUnavailable()

    if action == _BUY:
        # Enough cash for the purchase (Req 8.9, 16.4).
        if total > account.cash_balance:
            raise NotEnoughMoney()
        # Enough shares available in inventory to buy (Req 8.10, 16.5).
        if quantity > stock.available_shares:
            raise NotEnoughInventory()
    else:  # sell
        # Enough owned shares to sell (Req 8.11, 16.6).
        owned = position.quantity if position is not None else 0
        if quantity > owned:
            raise NotEnoughShares()


def confirm_order(account_id, order_id):
    """Execute a PENDING order inside ONE all-or-nothing transaction.

    This is the execute step from the design. The sequence is:

    1. **Lock first.** Atomically claim the order with a conditional
       ``UPDATE ... WHERE status='PENDING'`` (``lock_order_if_pending``). If
       zero rows change the order was already COMPLETED/CANCELLED or claimed
       by a competing request, so raise :class:`OrderNotPending` — this is how
       only one confirmation can win (Req 12.1, 12.3, 17.2).
    2. **Re-read trusted values.** Lock the order, account, stock, and
       position rows with ``SELECT ... FOR UPDATE`` and re-run every
       Pre-Execution Check against those fresh values (Req 8.2, 12.2).
    3. **Price match.** If the current DB price no longer equals the order's
       ``reviewed_price``, raise :class:`PriceChanged` and leave the order
       PENDING so the trainee must review again (Req 8.16).
    4. **Apply all changes in ONE transaction** then ``commit()``: cash
       (buy ``-=`` / sell ``+=`` total), Position create/increase (buy) or
       decrease/delete-at-zero (sell), Stock inventory subtract (buy) /
       return (sell), Order -> COMPLETED with ``completed_at``, exactly one
       Transaction, exactly one EventLog (Req 10.1-10.11).
    5. **Failure.** Any error after the claim rolls the whole transaction
       back so nothing persists, and the order is returned to PENDING so it
       can be retried (Req 10.12, 17.4). The invalid-status rejection from
       step 1 is the exception: it stays rejected.

    Parameters are plain values (no Flask request, no page rendering):
    ``account_id`` identifies the demo account; ``order_id`` identifies the
    PENDING order to execute. Returns a :class:`TradeResult` carrying the
    account's saved cash balance read back after the commit. Raises a
    :class:`TradeError` subclass on any failed check.
    """
    session = db.session

    # 1. Lock first: atomically claim the order. Only one request can flip a
    #    still-PENDING order to EXECUTING; everyone else sees zero rows and is
    #    rejected. This is a status rejection, so there is nothing to roll back
    #    (the claim is left as-is for the request that actually won).
    claimed = lock_order_if_pending(session, order_id)
    if claimed == 0:
        # Already completed/cancelled or won by a competing request.
        raise OrderNotPending()

    # From here on, this request owns the EXECUTING claim. Any failure must
    # roll back (which also reverts the claim) and leave the order PENDING.
    try:
        # 2. Re-read trusted values with row locks held for the transaction.
        order = get_order_for_update(session, order_id)
        if order is None:
            # Extremely unlikely after a successful claim; treat as not-pending.
            raise OrderNotPending()

        # The order must belong to the backend-selected demo account; never
        # trust the browser-supplied account_id (Req 8.4, 16.2, 16.3).
        account = get_demo_account(session)
        if account is None or account.id != account_id or order.account_id != account.id:
            raise InvalidTarget()

        # Lock the stock row too so competing trades can't race inventory.
        stock = session.query(Stock).filter(Stock.id == order.stock_id).with_for_update().first()
        if stock is None:
            raise InvalidTarget()

        action = order.action
        quantity = order.quantity
        total = order.total

        # The owned position (locked for update) — may be None for a first buy.
        position = (
            session.query(Position)
            .filter(
                Position.account_id == account.id,
                Position.stock_id == stock.id,
            )
            .with_for_update()
            .first()
        )

        # Re-run all Pre-Execution Checks against the trusted, locked values.
        _recheck_pre_execution(stock, account, position, action, quantity, total)

        # 3. Price match: the price shown at review must still be the DB price.
        if stock.current_price != order.reviewed_price:
            raise PriceChanged()

        # 4. Apply ALL changes in ONE transaction.
        execution_price = stock.current_price
        completed_at = datetime.utcnow()

        if action == _BUY:
            account.cash_balance = (account.cash_balance - total).quantize(_CENTS)
            # Position increases by quantity (created on first buy).
            upsert_position(session, account.id, stock.id, quantity)
            # Inventory leaves the market.
            stock.available_shares = stock.available_shares - quantity
        else:  # sell
            account.cash_balance = (account.cash_balance + total).quantize(_CENTS)
            # Position decreases by quantity (deleted when it reaches zero).
            upsert_position(session, account.id, stock.id, -quantity)
            # Inventory returns to the market.
            stock.available_shares = stock.available_shares + quantity

        # Order -> COMPLETED with the completion time.
        order.status = ORDER_STATUS_COMPLETED
        order.completed_at = completed_at

        # Exactly one Transaction records the permanent trade detail.
        add_transaction(
            session,
            order_id=order.id,
            account_id=account.id,
            stock_id=stock.id,
            action=action,
            quantity=quantity,
            execution_price=execution_price,
            total_amount=total,
            completed_at=completed_at,
        )

        # Exactly one EventLog for the user who performed the trade.
        add_event_log(
            session,
            user_id=account.user_id,
            event_type=_EVENT_TRADE_COMPLETED,
            description=(
                f"{action} {quantity} {stock.ticker} @ {execution_price} "
                f"(order {order.id})"
            ),
            created_at=completed_at,
        )

        # Commit the whole trade: all six changes land together.
        session.commit()
    except Exception:
        # Any failure after the claim: roll back every change (including the
        # EXECUTING claim) so nothing persists and the order returns to
        # PENDING, ready to retry (Req 10.12, 17.4).
        session.rollback()
        raise

    # Read the saved cash balance back after commit for the confirmation page.
    saved_account = get_demo_account(session)
    actual_cash = saved_account.cash_balance

    return TradeResult(
        company=stock.company_name,
        ticker=stock.ticker,
        action=action,
        quantity=quantity,
        price=execution_price,
        total=total,
        actual_cash=actual_cash,
        order_id=order.id,
    )


# ---------------------------------------------------------------------------
# cancel_order (cancel step)
# ---------------------------------------------------------------------------


def cancel_order(account_id, order_id):
    """Cancel a still-PENDING order: PENDING -> CANCELLED, nothing else.

    This is the cancel step from the design. A trainee may back out of a
    proposed trade before confirming it. The sequence is:

    1. **Read the order under a row lock** (``SELECT ... FOR UPDATE``) so a
       competing confirm cannot flip the same order between our status check
       and our write (design: concurrency; consistent with ``confirm_order``).
    2. **Verify ownership.** The order must belong to the backend-selected
       demo account. We never trust the browser-supplied ``account_id``: it
       must match the single demo account, and the order's ``account_id`` must
       match that account too. Any mismatch (or a missing order/account)
       raises :class:`InvalidTarget` (Req 8.4, 16.2, 16.3).
    3. **Verify it is still PENDING.** Once an order has been submitted for
       final confirmation (COMPLETED, CANCELLED, or the transient EXECUTING
       claim) cancel is unavailable, so raise :class:`OrderNotPending`
       ("This order has already been completed or cancelled.") (Req 7.5).
    4. **Set the status to CANCELLED and commit.** This makes NO change to
       cash, Positions, Stock inventory, or Transaction records (Req 7.1,
       7.3, 7.4). Any failure rolls the change back so nothing persists.

    Parameters are plain values (no Flask request, no page rendering):
    ``account_id`` identifies the demo account; ``order_id`` identifies the
    PENDING order to cancel. Returns a :class:`CancelResult` carrying the
    order id and its ``stock_id`` so the route can redirect to the
    Stock_Details_Page (Req 7.2). Raises a :class:`TradeError` subclass on any
    failed check.
    """
    session = db.session

    try:
        # 1. Read the order under a row lock held for this transaction.
        order = get_order_for_update(session, order_id)
        if order is None:
            raise InvalidTarget()

        # 2. The order must belong to the backend-selected demo account; never
        #    trust the browser-supplied account_id (Req 8.4, 16.2, 16.3).
        account = get_demo_account(session)
        if (
            account is None
            or account.id != account_id
            or order.account_id != account.id
        ):
            raise InvalidTarget()

        # 3. Only a still-PENDING order can be cancelled. Anything else means
        #    it was already completed/cancelled or is mid-confirmation (Req 7.5).
        if order.status != ORDER_STATUS_PENDING:
            raise OrderNotPending()

        # Capture the stock_id before commit so the route can redirect even if
        # the ORM expires attributes afterward.
        stock_id = order.stock_id

        # 4. Flip to CANCELLED and commit. No cash/position/inventory/
        #    transaction change is made (Req 7.1, 7.3, 7.4).
        order.status = ORDER_STATUS_CANCELLED
        session.commit()
    except Exception:
        # Any failure leaves nothing persisted (the lock/row change is undone).
        session.rollback()
        raise

    return CancelResult(order_id=order_id, stock_id=stock_id)
