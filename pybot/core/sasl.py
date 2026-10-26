"""SASL SCRAM-SHA-256 client (RFC 5802 / RFC 7677)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os


def _hmac(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def _escape_username(name: str) -> str:
    return name.replace("=", "=3D").replace(",", "=2C")


def _parse_attrs(message: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for part in message.split(","):
        key, sep, value = part.partition("=")
        if sep:
            attrs[key] = value
    return attrs


class ScramError(Exception):
    """The server's SCRAM messages were invalid or failed verification."""


class ScramSha256:
    """One SCRAM-SHA-256 exchange: client_first() -> client_final() -> verify_server_final()."""

    def __init__(self, username: str, password: str, nonce: str | None = None) -> None:
        self._username = username
        self._password = password
        self._nonce = nonce or base64.b64encode(os.urandom(24)).decode()
        self._client_first_bare = f"n={_escape_username(username)},r={self._nonce}"
        self._server_signature: bytes | None = None

    def client_first(self) -> str:
        # "n,," = no channel binding, no authzid
        return f"n,,{self._client_first_bare}"

    def client_final(self, server_first: str) -> str:
        attrs = _parse_attrs(server_first)
        if "e" in attrs:
            raise ScramError(f"server error: {attrs['e']}")
        try:
            nonce, salt, iterations = attrs["r"], base64.b64decode(attrs["s"]), int(attrs["i"])
        except (KeyError, ValueError) as exc:
            raise ScramError(f"malformed server-first message: {server_first!r}") from exc
        if not nonce.startswith(self._nonce) or len(nonce) <= len(self._nonce):
            raise ScramError("server nonce does not extend the client nonce")
        if iterations < 1:
            raise ScramError("invalid iteration count")

        salted = hashlib.pbkdf2_hmac("sha256", self._password.encode(), salt, iterations)
        client_key = _hmac(salted, b"Client Key")
        stored_key = hashlib.sha256(client_key).digest()
        without_proof = f"c=biws,r={nonce}"  # biws = base64("n,,")
        auth_message = f"{self._client_first_bare},{server_first},{without_proof}".encode()
        signature = _hmac(stored_key, auth_message)
        proof = bytes(a ^ b for a, b in zip(client_key, signature, strict=True))
        self._server_signature = _hmac(_hmac(salted, b"Server Key"), auth_message)
        return f"{without_proof},p={base64.b64encode(proof).decode()}"

    def verify_server_final(self, server_final: str) -> None:
        """Raise ScramError unless the server proved it knows the password too."""
        attrs = _parse_attrs(server_final)
        if "e" in attrs:
            raise ScramError(f"server error: {attrs['e']}")
        if self._server_signature is None or "v" not in attrs:
            raise ScramError("unexpected server-final message")
        if not hmac.compare_digest(base64.b64decode(attrs["v"]), self._server_signature):
            raise ScramError("server signature mismatch")
