"""Validate already adjusted daily prices and publish them atomically.

The existing database stores OHLCV only. ``adj_close``, ``currency``, ``provider``
and ``price_basis`` are validated provenance, not extra database columns. No
adjustment is performed here, and adjusted close never replaces close_price.
The caller owns the connection; it must have no transaction in progress.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import re
from typing import Any


PRICE_FIELDS = ("open_price", "high_price", "low_price", "close_price")
PRICE_QUANTUM = Decimal("0.0001")
MAX_PRICE = Decimal("9999999999999999.9999")
MAX_VOLUME = 2**63 - 1
CURRENCIES = {"KOSPI": "KRW", "NASDAQ": "USD"}


class ValidationError(ValueError):
    """An input row cannot represent the agreed daily-price contract."""


class CompanyMappingError(ValidationError):
    """At least one exact market/ticker pair lacks a unique company UUID."""


def _text(value: Any, field: str, *, maximum: int) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > maximum or any(ord(character) < 32 for character in value)):
        raise ValidationError(f"{field} must be a nonempty trimmed string of at most {maximum} characters")
    return value


def _price(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValidationError(f"{field} must be a finite nonnegative number")
    # Bound parsing work even when this API is used independently of the collector.
    if isinstance(value, str) and (not value.strip() or len(value) > 128):
        raise ValidationError(f"{field} has an invalid numeric representation")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError(f"{field} has an invalid numeric representation") from None
    if not result.is_finite() or result < 0 or result > MAX_PRICE:
        raise ValidationError(f"{field} must fit nonnegative NUMERIC(20,4)")
    return result


def _quantize(value: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 32
        try:
            return value.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise ValidationError("price cannot be represented as NUMERIC(20,4)") from None


def validate_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a new normalized row without I/O or modifying the supplied mapping."""
    if not isinstance(row, Mapping):
        raise ValidationError("each price row must be an object")
    market = row.get("market")
    if not isinstance(market, str) or market not in CURRENCIES:
        raise ValidationError("market must be KOSPI or NASDAQ")
    ticker = _text(row.get("ticker"), "ticker", maximum=30)
    if market == "KOSPI" and not re.fullmatch(r"[0-9A-Z]{6}", ticker):
        raise ValidationError("a KOSPI ticker must contain six uppercase letters or digits")
    if market == "NASDAQ" and not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,29}", ticker):
        raise ValidationError("a NASDAQ ticker must use uppercase letters, digits, dots or hyphens")
    raw_date = row.get("trading_date")
    if not isinstance(raw_date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw_date):
        raise ValidationError("trading_date must be YYYY-MM-DD")
    try:
        trading_date = date.fromisoformat(raw_date)
    except ValueError:
        raise ValidationError("trading_date must be a valid calendar date") from None
    if row.get("interval_type") != "1D":
        raise ValidationError("interval_type must be 1D")
    currency = row.get("currency")
    if currency != CURRENCIES[market]:
        raise ValidationError(f"currency must be {CURRENCIES[market]} for {market}")
    provider = _text(row.get("provider"), "provider", maximum=80)
    price_basis = _text(row.get("price_basis"), "price_basis", maximum=80)
    prices = {field: _price(row.get(field), field) for field in PRICE_FIELDS}
    # Validate before rounding so rounding cannot hide an invalid daily candle.
    if not (prices["low_price"] <= prices["open_price"] <= prices["high_price"]
            and prices["low_price"] <= prices["close_price"] <= prices["high_price"]):
        raise ValidationError("OHLC must satisfy low <= open/close <= high")
    if prices["close_price"] <= 0 or _quantize(prices["close_price"]) <= 0:
        raise ValidationError("close_price must remain positive at four decimal places")
    volume = row.get("trading_volume")
    if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= MAX_VOLUME:
        raise ValidationError("trading_volume must be an integer in 0..9223372036854775807")
    adj_close = row.get("adj_close")
    if adj_close is not None:
        adj_close = _quantize(_price(adj_close, "adj_close"))
    return {
        "ticker": ticker, "market": market, "trading_date": raw_date,
        "trading_at": datetime.combine(trading_date, time.min, tzinfo=timezone.utc),
        "interval_type": "1D", **{field: _quantize(value) for field, value in prices.items()},
        "trading_volume": volume, "adj_close": adj_close, "currency": currency,
        "provider": provider, "price_basis": price_basis,
    }


def validate_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate the entire input and reject every duplicate daily natural key."""
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for number, row in enumerate(rows, 1):
        try:
            normalized = validate_row(row)
            key = tuple(normalized[field] for field in ("market", "ticker", "trading_date", "interval_type"))
            if key in seen:
                raise ValidationError("duplicate market/ticker/trading_date/interval_type")
            seen.add(key)
            result.append(normalized)
        except ValidationError as error:
            raise ValidationError(f"row {number}: {error}") from None
    return result


COMPANY_SQL = """
SELECT company_id, market, stock_code FROM company
WHERE status = 'ACTIVE' AND (market, stock_code) IN (
    SELECT requested.market, requested.stock_code
    FROM unnest(%s::text[], %s::text[]) AS requested(market, stock_code)
)
FOR KEY SHARE
"""

UPSERT_SQL = """
INSERT INTO stock_price_history AS current_price
    (company_id, trading_at, interval_type, open_price, high_price, low_price,
     close_price, trading_volume)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (company_id, trading_at, interval_type) DO UPDATE SET
    open_price = EXCLUDED.open_price, high_price = EXCLUDED.high_price,
    low_price = EXCLUDED.low_price, close_price = EXCLUDED.close_price,
    trading_volume = EXCLUDED.trading_volume
WHERE (current_price.open_price, current_price.high_price, current_price.low_price,
       current_price.close_price, current_price.trading_volume)
      IS DISTINCT FROM
      (EXCLUDED.open_price, EXCLUDED.high_price, EXCLUDED.low_price,
       EXCLUDED.close_price, EXCLUDED.trading_volume)
"""


def load_rows(connection, rows: Iterable[Mapping[str, Any]], dry_run: bool = True) -> dict[str, Any]:
    """Upsert a batch, or exercise the same SQL and roll it back by default.

    All validation and all company mappings complete before the first price
    write. Database failures roll back the complete batch. An exact replay does
    not update existing rows. This function never opens or closes connections.
    """
    if not isinstance(dry_run, bool):
        raise ValidationError("dry_run must be a boolean")
    normalized = validate_rows(rows)
    from psycopg.pq import TransactionStatus
    from psycopg.rows import tuple_row
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise ValidationError("load_rows requires an idle connection without an existing transaction")
    keys = sorted({(row["market"], row["ticker"]) for row in normalized})
    affected = 0
    with connection.transaction(force_rollback=dry_run):
        with connection.cursor(row_factory=tuple_row) as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cursor.execute("SET LOCAL TIME ZONE 'UTC'")
            mapping = {}
            if keys:
                cursor.execute(COMPANY_SQL, ([key[0] for key in keys], [key[1] for key in keys]))
                for company_id, market, ticker in cursor.fetchall():
                    key = (market, ticker)
                    if key in mapping:
                        raise CompanyMappingError(f"ambiguous company mapping for {market}/{ticker}")
                    mapping[key] = company_id
                missing = [f"{market}/{ticker}" for market, ticker in keys if (market, ticker) not in mapping]
                if missing:
                    raise CompanyMappingError("unregistered or inactive companies: " + ", ".join(missing))
                # Stable lock order reduces deadlocks between overlapping batches.
                ordered = sorted(normalized, key=lambda row: (str(mapping[(row["market"], row["ticker"])]), row["trading_at"]))
                cursor.executemany(UPSERT_SQL, (
                    (mapping[(row["market"], row["ticker"])], row["trading_at"], "1D",
                     *(row[field] for field in PRICE_FIELDS), row["trading_volume"])
                    for row in ordered
                ))
                affected = cursor.rowcount
    return {
        "dry_run": dry_run, "input_rows": len(normalized), "validated_rows": len(normalized),
        "company_count": len(keys), "affected_rows": affected,
        "committed_rows": 0 if dry_run else affected,
    }
