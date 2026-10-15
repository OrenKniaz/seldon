<!-- Generated from spine.json; document revision fca084abe90f -->

# Requirements

## F1: Money core (7b9d9dd9df66)

Parse dollar amounts and split the tipped total fairly using integer cents.

### S1: Split a bill fairly (4a61788beb54)

As a diner, I want shares that sum to the exact tipped total and differ by at most one cent.

#### R1: Parse amounts (D1, D4) (8c8e25c2ac35)

`parse_amount(text) -> int` returns cents: `"12.34"` → 1234, `"$12"` → 1200 and `"12.3"` → 1230. It raises `ValueError` for negative amounts, more than two decimals, or garbage.

#### R2: Fair tipped shares (D2, D3) (46d4f9bfee8d)

`split(total_cents, people, tip_percent=0) -> list[int]` returns `people` shares that sum to the total plus the tip, rounded half up. Shares differ by at most 1 cent, and leftover cents go to the first people.

#### R3: Reject invalid split inputs (77f535aa848b)

`split` raises `ValueError` when `people < 1` or when `tip_percent` is not between 0 and 100.

### R6: Integrated restaurant bill (cf87e49a13f8)

`main(["100.00", "3", "--tip", "15"])` prints exactly:

- `Person 1: $38.34`
- `Person 2: $38.33`
- `Person 3: $38.33`

They sum to $115.00. `tests/test_end_to_end.py` passes on the integrated result.

## F2: Command line (172f9e799920)

Accept a total, people and optional tip percentage; print each person's share.

### S2: Read shares from the terminal (6ac9c0878adc)

As a diner, I want a simple command that prints dollar amounts for each person and clear errors for invalid inputs.

#### R4: Format cents and shares (D1) (99cb53dbbb1d)

`format_cents(cents) -> str` returns `"$12.34"`-style text. `format_shares(shares) -> list[str]` returns lines in the form `"Person 1: $38.34"`.

#### R5: Command-line success and error contract (D5) (b1dcf05dea34)

`main(argv) -> int` parses `TOTAL PEOPLE [--tip PCT]` and prints one line per person to stdout. It returns 0 on success. `cli.py` imports `from tipsplit.money import parse_amount, split` into its own namespace and calls those names, so Bead B's tests can stand in for Bead A. On invalid input it prints a one-line error to stderr and returns 2.

### R6: Integrated restaurant bill (cf87e49a13f8)

`main(["100.00", "3", "--tip", "15"])` prints exactly:

- `Person 1: $38.34`
- `Person 2: $38.33`
- `Person 3: $38.33`

They sum to $115.00. `tests/test_end_to_end.py` passes on the integrated result.
