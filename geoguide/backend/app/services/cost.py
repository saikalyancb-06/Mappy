"""What a place costs *this* traveller, and whether it fits their budget.

Money is handled as ``Decimal`` from exact decimal text; nothing shown to the
user passes through float arithmetic. Different currencies are never compared
without a rate: the fit is then reported as unknown.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.models import Candidate

_SYMBOL = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "AED": "AED ", "THB": "฿", "JPY": "¥", "SGD": "S$", "LKR": "Rs ", "NPR": "Rs "}


def to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def money_text(amount: Decimal | None, currency: str | None) -> str | None:
    if amount is None:
        return None
    if amount == 0:
        return "Free"
    text = f"{amount:,.2f}"
    return f"{_SYMBOL.get(currency or '', (currency or '') + ' ')}{text[:-3] if text.endswith('.00') else text}"


def user_budget(profile: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    return to_decimal(profile.get("max_daily_budget")), (profile.get("budget_currency") or None)


def cost_for_user(candidate: Candidate, profile: dict[str, Any]) -> dict[str, Any]:
    """{kind, amount, currency, display, fits_budget, share_of_budget_pct, note}."""
    if candidate.kind == "stay":
        amount, currency, kind = to_decimal(candidate.price_per_night), candidate.price_currency, "per_night"
    else:
        amount, currency, kind = to_decimal(candidate.entry_cost if candidate.entry_cost is not None else candidate.entry_fee), candidate.fee_currency, "entry"
    result: dict[str, Any] = {"kind": kind, "amount": str(amount) if amount is not None else None, "currency": currency, "display": money_text(amount, currency), "fits_budget": None, "share_of_budget_pct": None, "note": None}
    if amount is None:
        result["kind"] = "unknown"
        result["note"] = "price not available" if kind == "per_night" else "entry cost not verified"
        return result
    if amount == 0:
        result["kind"] = "free"
    budget, budget_currency = user_budget(profile)
    if budget is None or budget <= 0:
        return result
    if amount > 0 and not currency:
        result["note"] = "price currency not stated, so it can't be compared with your budget"
        return result
    if budget_currency and currency and budget_currency != currency:
        result["note"] = f"priced in {currency}; your budget is in {budget_currency}"
        return result
    result["fits_budget"] = amount <= budget
    result["share_of_budget_pct"] = int((amount / budget * 100).to_integral_value())
    result["note"] = f"{money_text(amount, currency)} of your {money_text(budget, budget_currency or currency)} daily budget"
    return result


def sum_money(values: list[tuple[Any, str | None]]) -> tuple[Decimal, str | None, bool]:
    """Exact total of (amount, currency) pairs in one currency. Returns (total, currency, complete)."""
    total = Decimal("0.00")
    currency = None
    complete = True
    for amount, cur in values:
        value = to_decimal(amount)
        if value is None:
            complete = False
            continue
        if currency and cur and cur != currency:
            complete = False
            continue
        currency = currency or cur
        total += value
    return total, currency, complete
