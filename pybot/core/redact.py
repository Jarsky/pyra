"""Redaction of secrets in IRC traffic before it is logged or shown elsewhere."""

from __future__ import annotations

import re

_SERVICES = {"nickserv", "authserv", "q", "userserv"}
_SERVICE_VERBS = ("IDENTIFY", "AUTH", "LOGIN", "REGISTER", "GHOST", "RECOVER", "REGAIN")
# Bot commands whose arguments are passwords
SECRET_COMMANDS = {"passwd", "setpass"}


def redact_message(target: str, text: str, command_prefix: str = "!") -> str:
    """Return text with passwords removed.

    Covers services logins (``IDENTIFY``/``AUTH``/... sent to NickServ, Q, ...) and the
    bot's own password commands (``!passwd``, ``!setpass``).
    """
    stripped = text.strip()
    service = target.strip().lower().split("@", 1)[0]
    if service in _SERVICES:
        verb = stripped.split(" ", 1)[0].upper()
        if verb in _SERVICE_VERBS:
            return f"{verb} [REDACTED]"

    if command_prefix and stripped.startswith(command_prefix):
        words = stripped[len(command_prefix) :].split(" ", 1)
        if words and words[0].lower() in SECRET_COMMANDS:
            return f"{command_prefix}{words[0]} [REDACTED]"
    return text


_SASL_TOKENS = {"PLAIN", "EXTERNAL", "SCRAM-SHA-256", "+", "*"}
# [@tags] [:prefix] COMMAND target :trailing
_TRAILING = re.compile(r"^(?P<head>(?:@\S+ +)?(?::\S+ +)?\S+ +(?P<target>\S+)) +:(?P<trailing>.*)$")


def redact_line(line: str, command_prefix: str = "!") -> str:
    """Redact a raw IRC line (for debug logging), e.g. 'PRIVMSG NickServ :IDENTIFY pw'."""
    words = line.split()
    # Skip [@tags] [:prefix] to reach the command
    start = 0
    while start < len(words) and words[start][:1] in ("@", ":"):
        start += 1
    if start >= len(words):
        return line
    command = words[start].upper()

    if command == "PASS":
        return " ".join(words[: start + 1] + ["[REDACTED]"])
    if command == "AUTHENTICATE":
        payload = words[start + 1] if len(words) > start + 1 else ""
        if payload.lstrip(":").upper() in _SASL_TOKENS:
            return line
        return " ".join(words[: start + 1] + ["[REDACTED]"])
    if command not in {"PRIVMSG", "NOTICE"}:
        return line

    m = _TRAILING.match(line)
    if not m:
        return line
    head, target, trailing = m.group("head"), m.group("target"), m.group("trailing")
    redacted = redact_message(target, trailing, command_prefix)
    return line if redacted == trailing else f"{head} :{redacted}"
