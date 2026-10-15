"""Bead A proof: requirements R1-R3 (decisions D1-D4)."""

import unittest

from tipsplit.money import parse_amount, split


class ParseAmountR1(unittest.TestCase):
    def test_accepted_forms_become_integer_cents(self):
        for text, cents in [
            ("12.34", 1234),
            ("12", 1200),
            ("12.3", 1230),
            ("$12.34", 1234),
            ("$12", 1200),  # R1 explicitly includes dollar-prefixed whole amounts.
            ("0.29", 29),
            ("90071992547409.93", 9007199254740993),  # D1: exact integer cents.
            (" 0.05 ", 5),
            ("0", 0),
        ]:
            with self.subTest(text=text):
                value = parse_amount(text)
                self.assertEqual(value, cents)
                self.assertIs(type(value), int)

    def test_rejected_forms_raise_value_error(self):
        for text in ["-1", "-$1.00", "12.345", "abc", "", "1.2.3", "$", "1e3"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_amount(text)


class SplitR2(unittest.TestCase):
    def test_shares_sum_to_total_plus_rounded_tip(self):
        self.assertEqual(sum(split(10000, 3, 15)), 11500)
        # 15% of 1001 cents = 150.15 -> 150; 50% of 1 cent = 0.5 -> rounds half up to 1.
        self.assertEqual(sum(split(1001, 2, 15)), 1151)
        self.assertEqual(sum(split(1, 1, 50)), 2)

    def test_fair_split_gives_leftover_cents_to_first_people(self):
        self.assertEqual(split(10000, 3, 15), [3834, 3833, 3833])
        self.assertEqual(split(100, 3), [34, 33, 33])
        self.assertEqual(split(101, 3), [34, 34, 33])
        self.assertEqual(split(300, 3), [100, 100, 100])

    def test_shares_differ_by_at_most_one_cent_and_are_ints(self):
        for total in range(0, 500, 37):
            for people in range(1, 8):
                shares = split(total, people, 10)
                with self.subTest(total=total, people=people):
                    self.assertEqual(len(shares), people)
                    self.assertEqual(sum(shares), total + (total * 10 + 50) // 100)
                    self.assertLessEqual(max(shares) - min(shares), 1)
                    self.assertTrue(all(type(s) is int for s in shares))

    def test_documented_tip_range_and_order_preserve_every_cent(self):
        # R2 / D1-D3: valid endpoints, small bills, ordered leftovers and precision.
        for total, people, tip, expected in [
            (0, 4, 100, [0, 0, 0, 0]),
            (111, 4, 10, [31, 31, 30, 30]),
            (99, 5, 1, [20, 20, 20, 20, 20]),
            (1, 3, 100, [1, 1, 0]),
            (5, 2, 50, [4, 4]),
            (9007199254740993, 3, 100, [6004799503160662] * 3),
        ]:
            with self.subTest(total=total, people=people, tip=tip):
                shares = split(total, people, tip)
                self.assertEqual(shares, expected)
                self.assertTrue(all(type(share) is int for share in shares))


class SplitValidationR3(unittest.TestCase):
    def test_invalid_people_or_tip_raise_value_error(self):
        for args in [(100, 0), (100, -2), (100, 2, -1), (100, 2, 101)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                split(*args)


if __name__ == "__main__":
    unittest.main()
