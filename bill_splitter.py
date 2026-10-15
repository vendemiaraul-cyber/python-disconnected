"""Split a restaurant bill between friends, including a tip.

Two ways to split:

* Evenly -- everyone pays the same share of the bill plus tip:

    python bill_splitter.py 120.50 --tip 18 --people 4

* By what each person ordered -- each person pays their own items, an equal
  part of any shared items, and a tip proportional to their portion:

    python bill_splitter.py --tip 20 --item Alice=32.50 --item Bob=18 \
        --item Cara=24.75 --shared 15

Money is handled with Decimal and rounded to cents. Leftover cents from
rounding are handed out one at a time, so the shares always add up to the
exact total.
"""

import argparse
import sys
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


def to_money(value):
    """Convert a number or string to a Decimal rounded to cents."""
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"not a valid amount: {value!r}") from None
    if not amount.is_finite():
        raise ValueError(f"not a valid amount: {value!r}")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def calculate_tip(subtotal, tip_percent):
    """Return the tip on a subtotal, rounded to cents."""
    if tip_percent < 0:
        raise ValueError("tip percent cannot be negative")
    tip = Decimal(str(subtotal)) * Decimal(str(tip_percent)) / 100
    return tip.quantize(CENT, rounding=ROUND_HALF_UP)


def allocate(total, weights):
    """Split ``total`` in proportion to ``weights``, to the cent.

    Each share is first rounded down, then the remaining cents go to the
    shares that lost the most to rounding, so the result sums to ``total``.
    """
    total = to_money(total)
    weights = [Decimal(str(w)) for w in weights]
    if not weights:
        raise ValueError("need at least one person")
    if any(w < 0 for w in weights):
        raise ValueError("weights cannot be negative")
    weight_sum = sum(weights)
    if weight_sum == 0:
        weights = [Decimal(1)] * len(weights)
        weight_sum = Decimal(len(weights))

    exact = [total * w / weight_sum for w in weights]
    shares = [e.quantize(CENT, rounding=ROUND_DOWN) for e in exact]
    leftover_cents = int((total - sum(shares)) / CENT)
    by_remainder = sorted(
        range(len(shares)), key=lambda i: exact[i] - shares[i], reverse=True
    )
    for i in by_remainder[:leftover_cents]:
        shares[i] += CENT
    return shares


def split_evenly(subtotal, tip_percent, people):
    """Split the bill plus tip evenly between ``people`` people.

    Returns a dict with ``subtotal``, ``tip``, ``total`` and a ``shares`` list.
    """
    if people < 1:
        raise ValueError("need at least one person")
    subtotal = to_money(subtotal)
    if subtotal < 0:
        raise ValueError("bill cannot be negative")
    tip = calculate_tip(subtotal, tip_percent)
    total = subtotal + tip
    return {
        "subtotal": subtotal,
        "tip": tip,
        "total": total,
        "shares": allocate(total, [1] * people),
    }


def split_itemized(orders, tip_percent, shared=0):
    """Split a bill by what each person ordered.

    ``orders`` maps each name to the cost of their own items. ``shared`` is
    the cost of items everyone shares (appetizers, a bottle of wine), split
    evenly. The tip is divided in proportion to each person's portion.

    Returns a dict with ``subtotal``, ``tip``, ``total`` and a ``shares``
    dict mapping each name to what they owe.
    """
    if not orders:
        raise ValueError("need at least one person")
    names = list(orders)
    own = [to_money(orders[name]) for name in names]
    shared = to_money(shared)
    if any(amount < 0 for amount in own) or shared < 0:
        raise ValueError("amounts cannot be negative")

    shared_parts = allocate(shared, [1] * len(names))
    portions = [o + s for o, s in zip(own, shared_parts)]
    subtotal = sum(portions, Decimal(0))
    tip = calculate_tip(subtotal, tip_percent)
    tip_parts = allocate(tip, portions)
    return {
        "subtotal": subtotal,
        "tip": tip,
        "total": subtotal + tip,
        "shares": {
            name: portion + tip_part
            for name, portion, tip_part in zip(names, portions, tip_parts)
        },
    }


def format_summary(result, tip_percent):
    """Return a printable summary of a split."""
    shares = result["shares"]
    if isinstance(shares, list):
        shares = {f"Person {i}": s for i, s in enumerate(shares, start=1)}
    rows = [
        ("Subtotal", result["subtotal"]),
        (f"Tip ({tip_percent:g}%)", result["tip"]),
        ("Total", result["total"]),
    ]
    rows += [(name, amount) for name, amount in shares.items()]
    width = max(len(label) for label, _ in rows)
    lines = [f"{label:<{width}}  ${amount:>8}" for label, amount in rows]
    lines.insert(3, "-" * len(lines[0]))
    return "\n".join(lines)


def parse_item(text):
    """Parse a NAME=AMOUNT command-line argument."""
    name, sep, amount = text.partition("=")
    if not sep or not name.strip():
        raise argparse.ArgumentTypeError(f"expected NAME=AMOUNT, got {text!r}")
    try:
        return name.strip(), to_money(amount)
    except ValueError as err:
        raise argparse.ArgumentTypeError(str(err)) from None


def build_parser():
    parser = argparse.ArgumentParser(
        description="Split a restaurant bill between friends, with a tip."
    )
    parser.add_argument(
        "bill", nargs="?", type=to_money, help="bill before tip (for an even split)"
    )
    parser.add_argument(
        "--tip", type=float, default=15.0, help="tip percent (default: 15)"
    )
    parser.add_argument(
        "--people", type=int, help="number of people splitting evenly"
    )
    parser.add_argument(
        "--item",
        action="append",
        type=parse_item,
        metavar="NAME=AMOUNT",
        help="what one person ordered; repeat for each person",
    )
    parser.add_argument(
        "--shared",
        type=to_money,
        default=Decimal(0),
        help="cost of shared items, split evenly (with --item)",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.item:
            if args.bill is not None or args.people is not None:
                parser.error("use either BILL --people N or --item, not both")
            orders = {}
            for name, amount in args.item:
                orders[name] = orders.get(name, Decimal(0)) + amount
            result = split_itemized(orders, args.tip, args.shared)
        else:
            if args.bill is None or args.people is None:
                parser.error("give BILL and --people N, or one --item per person")
            result = split_evenly(args.bill, args.tip, args.people)
    except ValueError as err:
        parser.error(str(err))
    print(format_summary(result, args.tip))
    return 0


if __name__ == "__main__":
    sys.exit(main())
