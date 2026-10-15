import unittest
from decimal import Decimal

from bill_splitter import allocate, calculate_tip, main, split_evenly, split_itemized


class TestCalculateTip(unittest.TestCase):
    def test_rounds_to_cents(self):
        self.assertEqual(calculate_tip(Decimal("33.33"), 15), Decimal("5.00"))
        self.assertEqual(calculate_tip("100", 18), Decimal("18.00"))

    def test_negative_tip_rejected(self):
        with self.assertRaises(ValueError):
            calculate_tip(10, -5)


class TestAllocate(unittest.TestCase):
    def test_leftover_cents_are_distributed(self):
        shares = allocate("10.00", [1, 1, 1])
        self.assertEqual(sum(shares), Decimal("10.00"))
        self.assertEqual(sorted(shares), [Decimal("3.33"), Decimal("3.33"), Decimal("3.34")])

    def test_proportional(self):
        self.assertEqual(allocate("9.00", [1, 2]), [Decimal("3.00"), Decimal("6.00")])

    def test_zero_weights_split_evenly(self):
        self.assertEqual(allocate("1.00", [0, 0]), [Decimal("0.50"), Decimal("0.50")])


class TestSplitEvenly(unittest.TestCase):
    def test_shares_sum_to_total(self):
        result = split_evenly("120.50", 18, 4)
        self.assertEqual(result["tip"], Decimal("21.69"))
        self.assertEqual(result["total"], Decimal("142.19"))
        self.assertEqual(sum(result["shares"]), result["total"])
        self.assertEqual(len(result["shares"]), 4)

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            split_evenly("50", 15, 0)
        with self.assertRaises(ValueError):
            split_evenly("-5", 15, 2)


class TestSplitItemized(unittest.TestCase):
    def test_shared_items_and_proportional_tip(self):
        result = split_itemized({"Alice": "30", "Bob": "10"}, 20, shared="20")
        # Alice's portion is 40, Bob's is 20; tip on 60 is 12, split 2:1.
        self.assertEqual(result["shares"], {"Alice": Decimal("48.00"), "Bob": Decimal("24.00")})
        self.assertEqual(result["total"], Decimal("72.00"))

    def test_shares_sum_to_total(self):
        result = split_itemized({"A": "12.34", "B": "5.67", "C": "8.91"}, 17.5, "3.01")
        self.assertEqual(sum(result["shares"].values()), result["total"])


class TestCli(unittest.TestCase):
    def test_even_split_runs(self):
        self.assertEqual(main(["100", "--tip", "20", "--people", "3"]), 0)

    def test_itemized_runs(self):
        self.assertEqual(main(["--item", "Al=10", "--item", "Bo=20", "--shared", "6"]), 0)

    def test_missing_arguments_exit(self):
        with self.assertRaises(SystemExit):
            main(["100"])


if __name__ == "__main__":
    unittest.main()
