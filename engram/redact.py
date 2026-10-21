"""Best-effort secret redaction (G16, R14). Pure: no I/O, no DB.

Replaces common secret shapes with ``[REDACTED:kind]``. Idempotent, linear-time
(every quantifier is bounded; PEM blocks are scanned with ``str.find``), and
leaves ordinary prose, 40-hex git SHAs and UUIDs alone. Redaction is
best-effort, not a guarantee.
"""
from __future__ import annotations

import math
import re

__all__ = ["redact", "redact_with_kinds"]

_MARK = "[REDACTED:%s]"
_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z ]{0,30}PRIVATE KEY(?: BLOCK)?-----")
_PEM_END = re.compile(r"-----END [A-Z ]{0,30}PRIVATE KEY(?: BLOCK)?-----")

_URL_CRED = re.compile(
    r"\b([a-zA-Z][a-zA-Z0-9+.-]{1,15}://)(?!\[REDACTED)[^\s/:@]{1,100}:[^\s/@]{1,100}@")
_BEARER = re.compile(
    r"(?i)\b((?:authorization\s{0,3}[:=]\s{0,3}(?:bearer\s{1,3}|basic\s{1,3}|token\s{1,3})?)"
    r"|bearer\s{1,3})([A-Za-z0-9._~+/=-]{8,500})")
_ASSIGN = re.compile(
    r"(?i)\b([\w.-]{0,30}?(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|"
    r"access[_-]?key|private[_-]?key|client[_-]?secret))(\s{0,3}[:=]\s{0,3})"
    r"([\"']?)(?!\[REDACTED)([^\s\"'<>,;&]{4,300})")
_KEYWORD_ENTROPY = re.compile(
    r"(?i)\b(?:key|secret|token|passwd|password|credential|auth)\w{0,20}[\"']?"
    r"\s{0,3}(?:[:=]|is\b|was\b)?\s{0,3}[\"']?([A-Za-z0-9+/_=-]{24,200})")

# (kind, compiled regex) applied in order; whole match replaced.
_SIMPLE = [
    ("jwt", re.compile(r"\beyJ[\w-]{5,4000}\.[\w-]{5,4000}\.[\w-]{5,4000}")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[A-Z0-9]{16}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,200}")),
    ("stripe_key", re.compile(r"\b(?:[sr]k|pk)_(?:live|test)_[A-Za-z0-9]{16,200}|\bwhsec_[A-Za-z0-9]{16,200}")),
    ("api_key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,200}")),
]
_HEX40 = re.compile(r"^[0-9a-fA-F]{32,64}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _entropy(s: str) -> float:
    n = len(s)
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _high_entropy(s: str) -> bool:
    if _HEX40.match(s) or _UUID.match(s):
        return False
    if not (any(c.isdigit() for c in s) and any(c.isalpha() for c in s)):
        return False
    return _entropy(s) >= 3.6


def _redact_pem(text: str, kinds: list) -> str:
    out, pos = [], 0
    while True:
        m = _PEM_BEGIN.search(text, pos)
        if not m:
            out.append(text[pos:])
            break
        out.append(text[pos:m.start()])
        e = _PEM_END.search(text, m.end())
        kinds.append("private_key")
        out.append(_MARK % "private_key")
        if not e:  # truncated block: fail closed, drop the remainder
            break
        pos = e.end()
    return "".join(out)


def redact_with_kinds(text: str) -> tuple[str, list[str]]:
    """Return (redacted_text, kinds_found_in_order)."""
    if not text:
        return text, []
    kinds: list[str] = []
    text = _redact_pem(text, kinds)

    def url(m):
        kinds.append("url_credentials")
        return m.group(1) + _MARK % "url_credentials" + "@"
    text = _URL_CRED.sub(url, text)

    def bearer(m):
        v = m.group(2)
        if not any(c.isdigit() for c in v) and len(v) < 20:
            return m.group(0)
        kinds.append("bearer")
        return m.group(1) + _MARK % "bearer"
    text = _BEARER.sub(bearer, text)

    for kind, rx in _SIMPLE:
        def sub(m, kind=kind):
            kinds.append(kind)
            return _MARK % kind
        text = rx.sub(sub, text)

    def assign(m):
        kinds.append("secret_assignment")
        return m.group(1) + m.group(2) + m.group(3) + _MARK % "secret_assignment"
    text = _ASSIGN.sub(assign, text)

    def ent(m):
        v = m.group(1)
        if not _high_entropy(v):
            return m.group(0)
        kinds.append("high_entropy")
        return m.group(0)[:m.start(1) - m.start()] + _MARK % "high_entropy"
    text = _KEYWORD_ENTROPY.sub(ent, text)
    return text, kinds


def redact(text: str) -> str:
    """Return ``text`` with common secret patterns replaced by markers."""
    return redact_with_kinds(text)[0]
