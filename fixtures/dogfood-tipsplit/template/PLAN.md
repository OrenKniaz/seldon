<!-- Generated from spine.json; document revision 89735a9946b0 -->

# Plan

Validation argv prefix: `-B -m unittest discover -s tests -p`

Append the bead's proof module to this prefix; do not run it by itself.

Requirement files: VISION.md, PLAN.md, tests, REQUIREMENTS.md, TRACE.md

## A: Bead A: money core (ae5fd3f29e17)

Implement the public money functions; record the D1 lesson after validation.

- Owned files: tipsplit/money.py
- Dependencies: (none)
- Requirement refs: R1@8c8e25c2ac35, R2@46d4f9bfee8d, R3@77f535aa848b
- Proof: `test_money.py`
- Provider: codex
- Parents: D1, D2, D3, D4, R1, R2, R3

### D1: Money is integer cents everywhere (05e89921fed6)

tip-split amounts are integer cents everywhere; never floats (D1)

### D2: Fair split (731102d9b93a)

Shares differ by at most one cent. Leftover cents go to the first people in order.

### D3: Tip rounding (07791ce992bd)

The tip is a whole-number percentage from 0 to 100, rounded half up to the cent.

### D4: Input forms (bf25c831d46b)

Amounts like `12`, `12.3`, `12.34` and `$12.34` are accepted. Negative amounts, more than two decimals and garbage are rejected.

### R1: Parse amounts (D1, D4) (8c8e25c2ac35)

`parse_amount(text) -> int` returns cents: `"12.34"` → 1234, `"$12"` → 1200 and `"12.3"` → 1230. It raises `ValueError` for negative amounts, more than two decimals, or garbage.

### R2: Fair tipped shares (D2, D3) (46d4f9bfee8d)

`split(total_cents, people, tip_percent=0) -> list[int]` returns `people` shares that sum to the total plus the tip, rounded half up. Shares differ by at most 1 cent, and leftover cents go to the first people.

### R3: Reject invalid split inputs (77f535aa848b)

`split` raises `ValueError` when `people < 1` or when `tip_percent` is not between 0 and 100.

Lesson after validation (D1): tip-split amounts are integer cents everywhere; never floats (D1)

## B: Bead B: command line (1cb88f173e58)

Implement the command line; depend on A's public functions at integration time only.

- Owned files: tipsplit/cli.py
- Dependencies: A
- Requirement refs: R4@99cb53dbbb1d, R5@b1dcf05dea34
- Proof: `test_cli.py`
- Provider: claude
- Parents: D1, D5, R4, R5

### D1: Money is integer cents everywhere (05e89921fed6)

tip-split amounts are integer cents everywhere; never floats (D1)

### D5: Error contract (663bd0ba718b)

Invalid input prints a one-line error to stderr and exits with code 2. Success exits with 0.

### R4: Format cents and shares (D1) (99cb53dbbb1d)

`format_cents(cents) -> str` returns `"$12.34"`-style text. `format_shares(shares) -> list[str]` returns lines in the form `"Person 1: $38.34"`.

### R5: Command-line success and error contract (D5) (b1dcf05dea34)

`main(argv) -> int` parses `TOTAL PEOPLE [--tip PCT]` and prints one line per person to stdout. It returns 0 on success. `cli.py` imports `from tipsplit.money import parse_amount, split` into its own namespace and calls those names, so Bead B's tests can stand in for Bead A. On invalid input it prints a one-line error to stderr and returns 2.

## Designs

## Integration

### test_end_to_end: Integrated bill proof (82039ed963bc)

Run the real integrated command line and verify the three shares sum to $115.00.

Proof: `test_end_to_end.py`

#### R6: Integrated restaurant bill (cf87e49a13f8)

`main(["100.00", "3", "--tip", "15"])` prints exactly:

- `Person 1: $38.34`
- `Person 2: $38.33`
- `Person 3: $38.33`

They sum to $115.00. `tests/test_end_to_end.py` passes on the integrated result.
