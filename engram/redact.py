"""Best-effort secret redaction (G16, R14). Pure: no I/O, no DB.

Replaces common secret shapes with ``[REDACTED:kind]``. Idempotent, linear-time
(every quantifier is bounded and anchored on a literal keyword; PEM blocks are
scanned with ``str.find``-style searches), and leaves ordinary prose, 40-hex git
SHAs, UUIDs, paths and code assignments (calls, identifiers, None) alone.
Redaction is best-effort, not a guarantee.

Contract: str in -> str out; bytes are decoded as UTF-8 (errors="replace");
any other type is returned unchanged.
"""
from __future__ import annotations

import math
import re

__all__ = ["redact", "redact_with_kinds"]

_MARK = "[REDACTED:%s]"
_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z ]{0,30}PRIVATE KEY(?: BLOCK)?-----")
_PEM_END = re.compile(r"-----END [A-Z ]{0,30}PRIVATE KEY(?: BLOCK)?-----")

# Password runs to the LAST '@' before '/' or whitespace (backtracking bounded by 200).
_URL_CRED = re.compile(
    r"\b([a-zA-Z][a-zA-Z0-9+.-]{1,15}://)(?!\[REDACTED)[^\s/@:]{0,100}:[^\s/]{1,200}@")
_CURL_U = re.compile(
    r"(?<!\S)(-u|--user)([ =]\s{0,3})(['\"]?)(?!\[REDACTED)[^\s:'\"]{1,100}:[^\s'\"]{1,200}")
_BEARER = re.compile(
    r"(?i)\b((?:authorization\s{0,3}[:=]\s{0,3}(?:bearer\s{1,3}|basic\s{1,3}|token\s{1,3})?)"
    r"|bearer\s{1,3})([A-Za-z0-9._~+/=-]{8,500})")
# Anchored on the key word itself (no variable-length prefix => linear time).
_KEYS = (r"(?:password|passwd|passphrase|pwd|secret|token|api[_-]?key|apikey|"
         r"access[_-]?key|private[_-]?key|_key|_creds?|_credentials?|_pass|_auth)")
_ASSIGN = re.compile(
    r"(?i)(" + _KEYS + r")([\"']?\s{0,3}[:=]\s{0,3})"
    r"(?:\"([^\"\n]{1,200})\"|'([^'\n]{1,200})'|(?!\[REDACTED)([^\s\"'<>,;]{4,300}))")
_PROSE = re.compile(
    r"(?i)\b(password|passphrase|passwd|secret|token)(\s{1,3}(?:is|was)\s{1,3})"
    r"(?!\[REDACTED)([^\s\"'<>,;]{6,300})")
_KEYWORD_ENTROPY = re.compile(
    r"(?i)\b(?:key|secret|token|passwd|password|credential|auth)\w{0,20}[\"']?"
    r"\s{0,3}(?:[:=]|is\b|was\b)?\s{0,3}[\"']?([A-Za-z0-9+/_=-]{24,200})")
_PLACEHOLDERS = frozenset(
    "none null nil true false required optional changeme change_me example secret password "
    "token undefined empty xxx xxxx redacted todo tbd".split())

# (kind, compiled regex) applied in order; whole match replaced.
_SIMPLE = [
    ("jwt", re.compile(r"\beyJ[\w-]{5,4000}\.[\w-]{5,4000}\.[\w-]{5,4000}")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[A-Z0-9]{16}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,200}")),
    ("stripe_key", re.compile(r"\b(?:[sr]k|pk)_(?:live|test)_[A-Za-z0-9]{16,200}|\bwhsec_[A-Za-z0-9]{16,200}")),
    ("api_key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,200}")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("npm_token", re.compile(r"\bnpm_[A-Za-z0-9]{36}")),
    ("hf_token", re.compile(r"\bhf_[A-Za-z0-9]{30,200}")),
    ("sendgrid_key", re.compile(r"\bSG\.[A-Za-z0-9_-]{16,200}\.[A-Za-z0-9_-]{16,200}")),
    ("twilio_key", re.compile(r"\bSK[0-9a-fA-F]{32}\b")),
]
_HEX40 = re.compile(r"^[0-9a-fA-F]{32,64}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_IDENT = re.compile(r"[A-Za-z_]+")
_ATTR = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_SUBSCRIPT = re.compile(r"[A-Za-z_][\w.]*\[")


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


def _quoted_literal(v: str) -> bool:
    if len(v) < 3 or v.startswith("[REDACTED"):
        return False
    return not (v.lower() in _PLACEHOLDERS or v.startswith(("${", "$(", "{{", "<", "%(", "%s")))


def _unquoted_literal(v: str) -> bool:
    """True if an unquoted value looks like a literal secret rather than code."""
    if len(v) < 4 or v.startswith("[REDACTED") or "(" in v or ")" in v:
        return False
    if v.lower() in _PLACEHOLDERS or v.startswith(("/", "./", "../", "~/", "$", "{{", "%")):
        return False
    if _IDENT.fullmatch(v):
        return False
    if _ATTR.fullmatch(v) and not any(c.isdigit() for c in v):
        return False
    if _SUBSCRIPT.match(v):
        return False
    return True


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


def redact_with_kinds(text):
    """Return (redacted_text, kinds_found_in_order). See module docstring for types."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8", errors="replace")
    if not isinstance(text, str) or not text:
        return text, []
    kinds: list[str] = []
    text = _redact_pem(text, kinds)

    def url(m):
        kinds.append("url_credentials")
        return m.group(1) + _MARK % "url_credentials" + "@"
    text = _URL_CRED.sub(url, text)

    def curl(m):
        kinds.append("basic_auth")
        return m.group(1) + m.group(2) + m.group(3) + _MARK % "basic_auth"
    text = _CURL_U.sub(curl, text)

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
        pre = m.group(1) + m.group(2)
        for q, g in (('"', 3), ("'", 4)):
            inner = m.group(g)
            if inner is not None:
                if not _quoted_literal(inner):
                    return m.group(0)
                kinds.append("secret_assignment")
                return pre + q + _MARK % "secret_assignment" + q
        v = m.group(5)
        core = v.rstrip(".!?")
        if not _unquoted_literal(core):
            return m.group(0)
        kinds.append("secret_assignment")
        return pre + _MARK % "secret_assignment" + v[len(core):]
    text = _ASSIGN.sub(assign, text)

    def prose(m):
        v = m.group(3)
        core = v.rstrip(".!?")
        if not (any(c.isalpha() for c in core) and any(not c.isalpha() for c in core)
                and not core.isdigit() and _unquoted_literal(core)):
            return m.group(0)
        kinds.append("secret_assignment")
        return m.group(1) + m.group(2) + _MARK % "secret_assignment" + v[len(core):]
    text = _PROSE.sub(prose, text)

    def ent(m):
        v = m.group(1)
        if not _high_entropy(v):
            return m.group(0)
        kinds.append("high_entropy")
        return m.group(0)[:m.start(1) - m.start()] + _MARK % "high_entropy"
    text = _KEYWORD_ENTROPY.sub(ent, text)
    return text, kinds


def redact(text):
    """Return ``text`` with common secret patterns replaced by markers."""
    return redact_with_kinds(text)[0]
