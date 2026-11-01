"""Fingerprint exact stdin bytes and emit a JSON receipt."""

import argparse
import hashlib
import json
import sys


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Read raw bytes from stdin through EOF and print a JSON receipt "
            "with byte count, SHA-256, and optional label."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        "--label", metavar="TEXT", help="attach TEXT as metadata without hashing it"
    )
    args = parser.parse_args()

    digest = hashlib.sha256()
    byte_count = 0
    while True:
        chunk = sys.stdin.buffer.read(65536)
        if not chunk:
            break
        byte_count += len(chunk)
        digest.update(chunk)

    json.dump(
        {"bytes": byte_count, "sha256": digest.hexdigest(), "label": args.label},
        sys.stdout,
        ensure_ascii=True,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
