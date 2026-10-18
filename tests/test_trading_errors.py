"""Unit tests for the typed trade errors and plain result dataclasses.

Covers task 5.1: each :class:`TradeError` subclass carries the exact
user-facing default message in ``.message``, the message can be overridden,
and :class:`OrderReview` / :class:`TradeResult` expose the expected fields.
"""

from decimal import Decimal

import pytest

from app.services.trading import (
    InvalidQuantity,
    InvalidTarget,
    NotEnoughInventory,
    NotEnoughMoney,
    NotEnoughShares,
    OrderNotPending,
    OrderReview,
    PriceChanged,
    PriceUnavailable,
    TradeError,
    TradeResult,
)


# Each subclass paired with its exact default message (from the design's
# Error Handling table). PriceUnavailable / InvalidTarget / PriceChanged use
# plain messages whose wording is checked loosely below.
EXACT_MESSAGE_CASES = [
    (InvalidQuantity, "Enter a positive whole number."),
    (NotEnoughMoney, "Not enough money."),
    (NotEnoughShares, "Not enough shares."),
    (NotEnoughInventory, "Not enough shares available to buy."),
    (OrderNotPending, "This order has already been completed or cancelled."),
]


@pytest.mark.parametrize("exc_cls, expected", EXACT_MESSAGE_CASES)
def test_default_message_is_exact(exc_cls, expected):
    err = exc_cls()
    assert err.message == expected
    # str(err) also carries the message (base class passes it to Exception).
    assert str(err) == expected


@pytest.mark.parametrize(
    "exc_cls, expected",
    EXACT_MESSAGE_CASES + [
        (PriceUnavailable, "This stock's price is unavailable right now."),
        (InvalidTarget, "That trade is not available."),
    ],
)
def test_all_subclasses_are_trade_errors(exc_cls, expected):
    err = exc_cls()
    assert isinstance(err, TradeError)
    assert isinstance(err, Exception)


def test_plain_message_subclasses_have_sensible_defaults():
    assert "unavailable" in PriceUnavailable().message.lower()
    assert "not available" in InvalidTarget().message.lower()
    # PriceChanged should ask the trainee to review the updated price again.
    msg = PriceChanged().message.lower()
    assert "price" in msg and "review" in msg


def test_message_can_be_overridden():
    err = NotEnoughMoney("Custom: you need $5 more.")
    assert err.message == "Custom: you need $5 more."
    assert str(err) == "Custom: you need $5 more."


def test_can_raise_and_catch_via_base():
    with pytest.raises(TradeError) as excinfo:
        raise InvalidQuantity()
    assert excinfo.value.message == "Enter a positive whole number."


def test_order_review_fields():
    review = OrderReview(
        company="Acme Corp",
        ticker="ACME",
        action="buy",
        quantity=3,
        price=Decimal("10.00"),
        total=Decimal("30.00"),
        expected_cash=Decimal("70.00"),
        order_id=42,
    )
    assert review.company == "Acme Corp"
    assert review.ticker == "ACME"
    assert review.action == "buy"
    assert review.quantity == 3
    assert review.price == Decimal("10.00")
    assert review.total == Decimal("30.00")
    assert review.expected_cash == Decimal("70.00")
    assert review.order_id == 42


def test_trade_result_fields():
    result = TradeResult(
        company="Acme Corp",
        ticker="ACME",
        action="sell",
        quantity=2,
        price=Decimal("10.00"),
        total=Decimal("20.00"),
        actual_cash=Decimal("120.00"),
        order_id=42,
    )
    assert result.company == "Acme Corp"
    assert result.ticker == "ACME"
    assert result.action == "sell"
    assert result.quantity == 2
    assert result.price == Decimal("10.00")
    assert result.total == Decimal("20.00")
    assert result.actual_cash == Decimal("120.00")
    assert result.order_id == 42
