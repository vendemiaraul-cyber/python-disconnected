# python-disconnected
## Bill splitter

Split a restaurant bill between friends, with a tip (Python 3, no dependencies).

Even split:

    python bill_splitter.py 120.50 --tip 18 --people 4

By what each person ordered, with shared items split evenly and the tip
divided in proportion to each person's portion:

    python bill_splitter.py --tip 20 --item Alice=32.50 --item Bob=18 --item Cara=24.75 --shared 15

The tip defaults to 15%. Amounts are rounded to the cent and leftover cents
are spread out so the shares always add up to the exact total.

Run the tests with `python -m unittest`.
