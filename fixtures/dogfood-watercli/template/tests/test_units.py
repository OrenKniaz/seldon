import unittest

from watercli.units import parse_amount


class UnitsTests(unittest.TestCase):
    # R1: exact conversion and rounding, implicit ml.
    def test_conversion(self):
        for amount, unit, expected in [
            ("250", "ml", 250),
            ("1", "oz", 30),
            ("8", "oz", 237),
            ("1.5", "ml", 2),
        ]:
            with self.subTest(amount=amount, unit=unit):
                self.assertEqual(parse_amount(amount, unit), expected)
        self.assertEqual(parse_amount("125"), 125)

    # R1/R2: local Decimal precision preserves long exact ties and normalizes overflow.
    def test_decimal_precision(self):
        self.assertEqual(
            parse_amount("1234567890123456789012345678.5"), 1234567890123456789012345679
        )
        with self.assertRaises(ValueError):
            parse_amount("1e10000000")

    # R2: no zero/negative/unknown/nonfinite/sub-ml intake.
    def test_invalid_input(self):
        for amount, unit in [
            ("0", "ml"),
            ("-5", "ml"),
            ("5", "cups"),
            ("garbage", "ml"),
            ("NaN", "ml"),
            ("Infinity", "oz"),
            ("0.1", "ml"),
        ]:
            with self.subTest(amount=amount, unit=unit):
                with self.assertRaises(ValueError):
                    parse_amount(amount, unit)
