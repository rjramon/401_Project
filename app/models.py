"""Database models for the STS buy-and-sell milestone.

These are the seven tables in scope for Phase 1 (per design.md): User,
Account, Stock, Order, Transaction, Position, and EventLog. The deferred
``Administrators`` and ``Market_Schedule`` tables are intentionally NOT
defined here.

Money rules (from the design):
    * Every money column is ``Numeric(12, 2)`` -> MySQL ``DECIMAL(12,2)``.
      In Python money is always ``Decimal``, never ``float``.
    * Share quantities are plain integers.

The single shared SQLAlchemy instance is imported from ``app.extensions``.
We never create a second ``SQLAlchemy()`` here; defining models against the
shared ``db`` registers their tables on the one metadata the app uses.
"""

from datetime import datetime

from sqlalchemy import UniqueConstraint

from .extensions import db


# Allowed values for Order.status.
#   PENDING    - created at review, not yet executed.
#   COMPLETED  - trade executed and committed.
#   CANCELLED  - trainee cancelled a still-pending order.
#   EXECUTING  - transient claim state set by the atomic "lock" during
#                confirm_order so only one confirmation can win.
# Stored as a short String with a documented allowed set (kept simple and
# easy for a student to explain, rather than a DB-specific ENUM type).
ORDER_STATUS_PENDING = "PENDING"
ORDER_STATUS_COMPLETED = "COMPLETED"
ORDER_STATUS_CANCELLED = "CANCELLED"
ORDER_STATUS_EXECUTING = "EXECUTING"
ORDER_STATUSES = (
    ORDER_STATUS_PENDING,
    ORDER_STATUS_COMPLETED,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_EXECUTING,
)


class User(db.Model):
    """A person using the system. This milestone preloads one trainee."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    # Role is a plain column, e.g. 'trainee' or 'admin' (no Administrators table).
    role = db.Column(db.String(20), nullable=False)

    # One User has many Accounts and many EventLogs.
    accounts = db.relationship("Account", back_populates="user")
    event_logs = db.relationship("EventLog", back_populates="user")


class Account(db.Model):
    """A practice trading account that holds cash and positions."""

    __tablename__ = "accounts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # Money columns: Decimal with 2 places; never float.
    cash_balance = db.Column(db.Numeric(12, 2), nullable=False)
    starting_balance = db.Column(db.Numeric(12, 2), nullable=False)

    user = db.relationship("User", back_populates="accounts")

    # One Account has many Orders, Transactions, and Positions.
    orders = db.relationship("Order", back_populates="account")
    transactions = db.relationship("Transaction", back_populates="account")
    positions = db.relationship("Position", back_populates="account")


class Stock(db.Model):
    """A fictional, fixed-price stock available to trade."""

    __tablename__ = "stocks"

    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(120), nullable=False)
    # Ticker is unique across all stocks.
    ticker = db.Column(db.String(20), unique=True, nullable=False)
    current_price = db.Column(db.Numeric(12, 2), nullable=False)
    # Share counts are whole numbers.
    available_shares = db.Column(db.Integer, nullable=False)

    # One Stock appears in many Orders, Transactions, and Positions.
    orders = db.relationship("Order", back_populates="stock")
    transactions = db.relationship("Transaction", back_populates="stock")
    positions = db.relationship("Position", back_populates="stock")


class Order(db.Model):
    """A proposed trade. Starts PENDING at review; becomes COMPLETED or
    CANCELLED (EXECUTING is a brief internal claim state during confirm)."""

    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    stock_id = db.Column(db.Integer, db.ForeignKey("stocks.id"), nullable=False)
    # 'buy' or 'sell'.
    action = db.Column(db.String(4), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    # Price captured at review and the computed total (both money).
    reviewed_price = db.Column(db.Numeric(12, 2), nullable=False)
    total = db.Column(db.Numeric(12, 2), nullable=False)
    # One of ORDER_STATUSES (see constants above).
    status = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    account = db.relationship("Account", back_populates="orders")
    stock = db.relationship("Stock", back_populates="orders")

    # A completed Order has exactly one Transaction (one-to-one via uselist=False).
    transaction = db.relationship(
        "Transaction", back_populates="order", uselist=False
    )


class Transaction(db.Model):
    """The permanent record of a completed trade. The unique order_id means
    at most one Transaction per Order."""

    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    # Unique: guarantees one Transaction per Order.
    order_id = db.Column(
        db.Integer, db.ForeignKey("orders.id"), unique=True, nullable=False
    )
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    stock_id = db.Column(db.Integer, db.ForeignKey("stocks.id"), nullable=False)
    action = db.Column(db.String(4), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    # Money columns.
    execution_price = db.Column(db.Numeric(12, 2), nullable=False)
    total_amount = db.Column(db.Numeric(12, 2), nullable=False)
    completed_at = db.Column(db.DateTime, nullable=False)

    order = db.relationship("Order", back_populates="transaction")
    account = db.relationship("Account", back_populates="transactions")
    stock = db.relationship("Stock", back_populates="transactions")


class Position(db.Model):
    """How many shares of one stock an account currently holds. The unique
    (account_id, stock_id) pair means exactly one Position per account+stock."""

    __tablename__ = "positions"
    # Unique composite: one Position row per (account, stock).
    __table_args__ = (
        UniqueConstraint("account_id", "stock_id", name="uq_position_account_stock"),
    )

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    stock_id = db.Column(db.Integer, db.ForeignKey("stocks.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    account = db.relationship("Account", back_populates="positions")
    stock = db.relationship("Stock", back_populates="positions")


class EventLog(db.Model):
    """An audit entry; one per completed trade (and other notable events)."""

    __tablename__ = "event_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    event_type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", back_populates="event_logs")
