from decimal import ROUND_HALF_UP, Decimal, DecimalException, localcontext


def parse_amount(amount: str, unit: str = "ml") -> int:
    if unit not in ("ml", "oz"):
        raise ValueError("unit must be ml or oz")
    try:
        value = Decimal(amount)
        if not value.is_finite() or value <= 0:
            raise ValueError("amount must be positive and finite")
        with localcontext() as context:
            context.prec = max(28, len(value.as_tuple().digits) + 8)
            result = int(
                (value * (Decimal("29.5735") if unit == "oz" else 1)).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
    except (DecimalException, OverflowError) as error:
        raise ValueError("invalid amount") from error
    if result <= 0:
        raise ValueError("amount rounds to zero ml")
    return result
