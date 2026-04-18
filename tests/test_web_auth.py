"""Tests for web/partyline password hashing and verification."""

from __future__ import annotations

import bcrypt

from pybot.web.auth import hash_password, verify_password


def test_hash_and_verify_long_password() -> None:
    # Regression: bcrypt-only contexts crash over 72 bytes.
    password = "x" * 200
    hashed = hash_password(password)

    assert verify_password(password, hashed)


def test_verify_legacy_bcrypt_hash() -> None:
    password = "short-password"
    legacy_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    assert verify_password(password, legacy_hash)
