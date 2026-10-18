"""Record-changing order routes (the review → confirm/cancel flow).

This blueprint owns every POST that changes data plus the one GET that shows
the saved result of a completed trade. It is the thin web layer over the
Flask-free trading service (``app/services/trading.py``): each route reads the
submitted form values, resolves the TRUSTED demo identity on the backend
(never from the browser), calls the matching service function, and then either
renders a page or issues a redirect. All trade logic, validation, money math,
and the transaction boundary live in the service — the routes only translate
between HTTP and plain service calls (design: Route interface, Page Flow).

Design rules enforced here:
    * POST is used for every record-changing action; GET never changes data
      (Req 5.5, 5.6).
    * Confirmation uses **Post/Redirect/Get**: the confirm POST redirects to a
      GET confirmation page, so refreshing that page re-runs only a GET and
      never re-executes the trade (Req 11.3, 12.1).
    * The backend selects the demo account via ``get_demo_account``; any
      browser-supplied user/account id is ignored (Req 8.14, 8.15).
    * On any failed check during review the route catches the ``TradeError``,
      re-renders the stock details form, and preserves the submitted input so
      the trainee can correct it, showing ``err.message`` (Req 9.1, 9.7).

The blueprint attribute MUST be named ``bp`` — the app factory in
``app/__init__.py`` registers ``app.routes.orders:bp`` automatically.

Assumptions documented for the parallel GET routes (task 8.1, blueprint
``main``):
    * The main blueprint is named ``"main"`` with a ``main.dashboard`` endpoint
      and a ``main.stock_details`` endpoint that takes a ``stock_id`` keyword.
      These are referenced only through ``url_for`` for redirects. If task 8.1
      names them differently, only the two ``url_for`` calls here need to
      change.
    * The stock details template is ``stock_details.html``. When a review
      check fails we re-render that template directly (rather than redirecting)
      so the submitted buy/sell input is preserved for correction; the shared
      context variables are documented on ``_render_stock_details`` below.

Template context variable names (consumed by task 9.1 templates):
    * ``order_review.html`` receives ``review`` — an ``OrderReview`` dataclass
      (``company``, ``ticker``, ``action``, ``quantity``, ``price``,
      ``total``, ``expected_cash``, ``order_id``).
    * ``confirmation.html`` receives ``trade`` — a dict of the saved DB values
      for the completed order (see ``confirmation`` below).
    * ``stock_details.html`` receives ``stock``, ``shares_owned``, and
      ``form`` (the preserved submitted ``action``/``quantity``) plus an
      ``error`` message on a failed review.
"""

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from ..db import get_demo_account, get_position, get_stock
from ..extensions import db
from ..models import ORDER_STATUS_COMPLETED
from ..models import Order, Transaction
from ..services.trading import (
    TradeError,
    cancel_order,
    confirm_order,
    prepare_order,
)

# The app factory registers this exact attribute name (``bp``).
bp = Blueprint("orders", __name__, url_prefix="/orders")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_stock_details(stock_id, submitted_action, submitted_quantity, error=None, status=200):
    """Re-render the stock details page, preserving the submitted input.

    Used when a review check fails: instead of losing what the trainee typed,
    we re-show the Stock_Details_Page with the same ``action`` and
    ``quantity`` filled in and the exact error message displayed (Req 9.1,
    9.7). All displayed values (price, shares owned) are read fresh from the
    database — the browser never supplies trusted numbers (Req 8.14, 8.15).

    Context passed to ``stock_details.html``:
        * ``stock`` — the Stock row (name, ticker, current_price,
          available_shares), or ``None`` if it no longer exists.
        * ``shares_owned`` — the demo account's current quantity of this stock.
        * ``form`` — a dict with the preserved ``action`` and ``quantity`` so
          the forms re-populate.
        * ``error`` — the user-facing message to display (may be ``None``).
    """
    session = db.session
    account = get_demo_account(session)
    stock = get_stock(session, stock_id)

    shares_owned = 0
    if account is not None and stock is not None:
        position = get_position(session, account.id, stock.id)
        shares_owned = position.quantity if position is not None else 0

    return (
        render_template(
            "stock_details.html",
            stock=stock,
            shares_owned=shares_owned,
            form={"action": submitted_action, "quantity": submitted_quantity},
            error=error,
        ),
        status,
    )


# ---------------------------------------------------------------------------
# POST /orders/review  — prepare_order() → render review
# ---------------------------------------------------------------------------


@bp.route("/review", methods=["POST"])
def review():
    """Validate a proposed trade and show the review page (Req 6.1, 9.7).

    Reads ``stock_id``, ``action``, and ``quantity`` from the submitted form
    as plain strings. The demo account is resolved on the backend; any
    account id sent by the browser is ignored (Req 8.14, 8.15). On success the
    service creates one PENDING Order and returns an ``OrderReview``, which is
    rendered on ``order_review.html``. On any ``TradeError`` the stock details
    form is re-rendered with the submitted input preserved and ``err.message``
    shown (Req 9.1, 9.7).
    """
    # Raw form values (plain strings; the service does all parsing/validation).
    stock_id_raw = request.form.get("stock_id", "")
    action = request.form.get("action", "")
    quantity_raw = request.form.get("quantity", "")

    # Resolve the TRUSTED demo account on the backend (ignore any browser id).
    account = get_demo_account(db.session)
    if account is None:
        # No demo account seeded; nothing to trade against.
        flash("That trade is not available.")
        return redirect(url_for("main.dashboard"))

    # The stock id addresses the form page we fall back to on error. Parse it
    # loosely so an unparsable value still lets us re-render something useful;
    # the service re-validates the real target against the database anyway.
    try:
        stock_id = int(stock_id_raw)
    except (TypeError, ValueError):
        stock_id = None

    try:
        order_review = prepare_order(account.id, stock_id, action, quantity_raw)
    except TradeError as err:
        # Re-render the form with what the trainee typed and the exact message.
        return _render_stock_details(
            stock_id, action, quantity_raw, error=err.message
        )

    return render_template("order_review.html", review=order_review)


# ---------------------------------------------------------------------------
# POST /orders/<id>/confirm  — confirm_order() → redirect (PRG) to confirmation
# ---------------------------------------------------------------------------


@bp.route("/<int:order_id>/confirm", methods=["POST"])
def confirm(order_id):
    """Execute a PENDING order, then redirect to the confirmation GET.

    Post/Redirect/Get: on success we REDIRECT to the confirmation GET route so
    a browser refresh re-runs only a GET and never re-executes the trade
    (Req 11.3, 12.1). The demo account is resolved on the backend (Req 8.14,
    8.15). On a ``TradeError`` (e.g. ``PriceChanged`` or ``OrderNotPending``)
    we flash the exact message and redirect to a safe page rather than
    re-executing — the trade logic and rollback already ran in the service.
    """
    account = get_demo_account(db.session)
    if account is None:
        flash("That trade is not available.")
        return redirect(url_for("main.dashboard"))

    try:
        confirm_order(account.id, order_id)
    except TradeError as err:
        # PriceChanged leaves the order PENDING (review again); OrderNotPending
        # means it was already handled. Either way, show the message and send
        # the trainee somewhere safe. Prefer the order's stock details page
        # when we can resolve it, otherwise the dashboard.
        flash(err.message)
        stock_id = _stock_id_for_order(order_id)
        if stock_id is not None:
            return redirect(url_for("main.stock_details", stock_id=stock_id))
        return redirect(url_for("main.dashboard"))

    # Success: Post/Redirect/Get to the confirmation GET page.
    return redirect(url_for("orders.confirmation", order_id=order_id))


# ---------------------------------------------------------------------------
# POST /orders/<id>/cancel  — cancel_order() → redirect to stock details
# ---------------------------------------------------------------------------


@bp.route("/<int:order_id>/cancel", methods=["POST"])
def cancel(order_id):
    """Cancel a still-PENDING order, then redirect to the stock details page.

    Cancelling makes no cash/position/inventory/transaction change (Req 7).
    The service returns a ``CancelResult`` carrying the ``stock_id`` so we can
    redirect the trainee back to the Stock_Details_Page (Req 7.2). On a
    ``TradeError`` we flash the exact message and redirect to the dashboard.
    """
    account = get_demo_account(db.session)
    if account is None:
        flash("That trade is not available.")
        return redirect(url_for("main.dashboard"))

    try:
        result = cancel_order(account.id, order_id)
    except TradeError as err:
        flash(err.message)
        return redirect(url_for("main.dashboard"))

    return redirect(url_for("main.stock_details", stock_id=result.stock_id))


# ---------------------------------------------------------------------------
# GET /orders/<id>/confirmation  — saved values (no DB change)
# ---------------------------------------------------------------------------


@bp.route("/<int:order_id>/confirmation", methods=["GET"])
def confirmation(order_id):
    """Show the ACTUAL saved values for a completed order (Req 11.1-11.3).

    This is a GET, so opening or refreshing it never re-executes the trade.
    Every value shown is read from the database: the completed Order, its one
    Transaction, and the demo account's saved cash balance. If the order is
    missing or not COMPLETED we send the trainee to the dashboard with a
    message rather than showing a half-finished page.

    Context passed to ``confirmation.html`` as ``trade`` (a plain dict so the
    template never touches ORM internals):
        * ``company`` / ``ticker`` — the traded stock.
        * ``action`` / ``quantity`` — what was executed.
        * ``price`` — the execution price (Transaction.execution_price).
        * ``total`` — the total cost (buy) or proceeds (sell).
        * ``actual_cash`` — the account's saved cash balance after the trade.
        * ``order_id`` — the completed order's id.
    """
    session = db.session

    order = session.get(Order, order_id)
    if order is None or order.status != ORDER_STATUS_COMPLETED:
        # Nothing saved to confirm (or not finished): go back to the dashboard.
        flash("That trade is not available.")
        return redirect(url_for("main.dashboard"))

    account = get_demo_account(session)
    if account is None or order.account_id != account.id:
        # The order must belong to the backend-selected demo account.
        flash("That trade is not available.")
        return redirect(url_for("main.dashboard"))

    # The one permanent Transaction holds the executed price/amount.
    transaction = (
        session.query(Transaction)
        .filter(Transaction.order_id == order.id)
        .first()
    )
    stock = get_stock(session, order.stock_id)

    # Prefer the Transaction's saved execution values; fall back to the order's
    # reviewed values if the Transaction is somehow absent.
    execution_price = (
        transaction.execution_price if transaction is not None else order.reviewed_price
    )
    total_amount = (
        transaction.total_amount if transaction is not None else order.total
    )

    trade = {
        "company": stock.company_name if stock is not None else "",
        "ticker": stock.ticker if stock is not None else "",
        "action": order.action,
        "quantity": order.quantity,
        "price": execution_price,
        "total": total_amount,
        "actual_cash": account.cash_balance,
        "order_id": order.id,
    }

    return render_template("confirmation.html", trade=trade)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _stock_id_for_order(order_id):
    """Return the ``stock_id`` for ``order_id`` for redirect targeting, or None.

    Used only to pick a sensible redirect destination after a failed confirm;
    it reads the order's stock id and never changes any data.
    """
    order = db.session.get(Order, order_id)
    return order.stock_id if order is not None else None
