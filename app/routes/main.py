"""Read-only (GET) routes for the buy-and-sell milestone.

These routes form the guided browsing flow — Dashboard -> Stock List ->
Stock Details (design.md "Route interface" / "Page Flow"). They are pure
reads: every handler only queries the database and renders a template. No
handler here commits, writes, adds, or deletes anything, so opening or
refreshing any of these pages can never change data (Req 5.6).

Trusted identity: the demo account is always chosen on the backend via
``get_demo_account`` (app/db.py). Nothing the browser sends — no user id,
no account id — is ever consulted to pick the identity (Req 1.2, 1.3).

Money values come straight from the ORM as ``Decimal`` (never float); the
templates format them for display.

The blueprint attribute is named ``bp`` on purpose: the app factory in
``app/__init__.py`` registers ``app.routes.main:bp`` automatically, so this
name must not change.

Template context variables (so the templates task — 9.1 — can match):

``dashboard.html``
    * ``account``  — the demo Account (has ``id`` and ``cash_balance``).
    * ``cash``     — Decimal cash balance (``account.cash_balance``).
    * ``holdings`` — list of Position rows the account currently holds.
                     Each Position has ``.stock`` (company_name, ticker,
                     current_price) and ``.quantity`` (shares owned).

``stock_list.html``
    * ``stocks``   — list of all Stock rows. Each has ``company_name``,
                     ``ticker``, ``current_price``, ``available_shares``.

``stock_details.html``
    * ``stock``        — the selected Stock (company_name, ticker,
                         current_price, available_shares, ``id``).
    * ``shares_owned`` — int number of shares the demo account owns of this
                         stock (0 when there is no Position). The buy form and
                         sell form on the page post to the order routes using
                         ``stock.id``.
"""

from flask import Blueprint, abort, render_template

from ..db import get_demo_account, get_position, get_stock
from ..extensions import db
from ..models import Stock

# Named ``bp`` because the app factory registers ``app.routes.main:bp``.
bp = Blueprint("main", __name__)


@bp.route("/")
@bp.route("/dashboard")
def dashboard():
    """Dashboard: demo account id, cash balance, and current holdings.

    All values are read from the database for the backend-selected demo
    account (Req 2.1-2.4). The browser cannot influence which account is
    shown (Req 1.2, 1.3).
    """
    session = db.session
    account = get_demo_account(session)
    if account is None:
        # No demo account seeded yet; render with empty state rather than crash.
        return render_template("dashboard.html", account=None, cash=None, holdings=[])

    holdings = list(account.positions)
    return render_template(
        "dashboard.html",
        account=account,
        cash=account.cash_balance,
        holdings=holdings,
    )


@bp.route("/stocks")
def stock_list():
    """Stock list: company name, ticker, current price, available shares.

    Lists every stock read from the database (Req 3.1, 3.2). Ordered by
    ticker so the page is stable and easy to scan.
    """
    session = db.session
    stocks = session.query(Stock).order_by(Stock.ticker.asc()).all()
    return render_template("stock_list.html", stocks=stocks)


@bp.route("/stocks/<int:stock_id>")
def stock_details(stock_id):
    """Stock details: name, ticker, price, shares owned, plus buy/sell forms.

    Shows the selected stock and how many shares the demo account holds of
    it (Req 4.1-4.3). The buy and sell forms live in the template; this
    handler only supplies the data they need. Reads only — no DB changes.
    """
    session = db.session
    stock = get_stock(session, stock_id)
    if stock is None:
        abort(404)

    account = get_demo_account(session)
    shares_owned = 0
    if account is not None:
        position = get_position(session, account.id, stock.id)
        if position is not None:
            shares_owned = position.quantity

    return render_template(
        "stock_details.html",
        stock=stock,
        shares_owned=shares_owned,
    )
