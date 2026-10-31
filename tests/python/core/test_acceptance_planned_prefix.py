"""Lossless packet-view source checks; no reviewer/provider is invoked."""

import hashlib

from tests.python.core.test_roundtable import store as roundtable_store

store = roundtable_store


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()
