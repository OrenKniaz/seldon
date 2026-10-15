<!-- Generated from spine.json; document revision c76fd0c0331c -->

# V1: tip-split: vision (6c3146e77c34)

A tiny command-line tool that splits a restaurant bill, plus tip, fairly between people, so nobody argues about the last cent.

First user moment: `tipsplit 100.00 3 --tip 15` prints three lines that add up to exactly $115.00, with the extra cent given to the first person.

Out of scope: currencies other than dollars; uneven or weighted splits; saving history.

## Decisions

### D1: Money is integer cents everywhere (05e89921fed6)

tip-split amounts are integer cents everywhere; never floats (D1)

Parents: V1

### D2: Fair split (731102d9b93a)

Shares differ by at most one cent. Leftover cents go to the first people in order.

Parents: V1

### D3: Tip rounding (07791ce992bd)

The tip is a whole-number percentage from 0 to 100, rounded half up to the cent.

Parents: V1

### D4: Input forms (bf25c831d46b)

Amounts like `12`, `12.3`, `12.34` and `$12.34` are accepted. Negative amounts, more than two decimals and garbage are rejected.

Parents: V1

### D5: Error contract (663bd0ba718b)

Invalid input prints a one-line error to stderr and exits with code 2. Success exits with 0.

Parents: V1
