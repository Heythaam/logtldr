"""Secret redaction (AD-6)."""

import re

from logtldr.models import LogLine

# Private keys span several lines, so they are handled per batch, not per line.
_PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_PRIVATE_KEY_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")


class Redactor:
    """Applies regex patterns to redact secrets into stable placeholders.

    The placeholder map and counters are kept strictly inside this class.
    They are never written to the LogLine models, ensuring no secrets leak
    into the rendered output.

    A pattern with a named group ``val`` redacts only that group and keeps the
    rest of the match: the key name stays visible and only its value becomes a
    placeholder such as ``secret_kv_1``, so the LLM still knows what kind of
    value was there.
    """

    def __init__(self) -> None:
        self.placeholder_map: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self._issued: set[str] = set()

        # Ordered by specificity (most specific first)
        self.patterns: list[tuple[str, re.Pattern[str]]] = [
            # 1. Private key on a single line (e.g. escaped \n inside JSON).
            #    Multi-line blocks are handled in redact().
            ("private_key", re.compile(
                r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)"
            )),
            # 2. Env Vars (before DB URLs so DATABASE_URL=postgres://... is caught whole).
            #    Names may contain digits: S3_SECRET, OAUTH2_TOKEN.
            ("env_var", re.compile(r"\b[A-Z][A-Z0-9_]+\s*=\s*[^\s'\"]+")),
            # 3. DB URLs (catch the whole string before the email/ip parsers break it apart)
            ("db_url", re.compile(r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|mssql)://[^\s,;]+")),
            # 4. Specific Tokens
            ("jwt", re.compile(r"eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.[A-Za-z0-9-_.+/=]*")),
            ("aws_key", re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}")),
            ("slack_token", re.compile(r"xox[baprs]-[0-9a-zA-Z]{10,48}")),
            ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
            ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
            # 5. Network Identifiers
            ("ipv6", re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){3,7}[A-Fa-f0-9]{0,4}\b")),
            ("ip", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b")),
            # 6. Emails
            ("email", re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")),
            # 7. Catch-alls run last, so specific patterns above win and these only
            #    pick up what is left. Bearer tokens: keep the word, redact the token.
            ("bearer", re.compile(r"(?i)\bBearer\s+(?P<val>[A-Za-z0-9._~+/=-]+)")),
            # 8. key=value / key: value / "key": "value" with a secret-sounding key.
            #    Also matches prefixed keys like db_password or client_secret.
            ("secret_kv", re.compile(
                r"(?i)\b(?:[a-z0-9]+[_.-])*"
                r"(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)"
                r"[\"']?\s*[=:]\s*[\"']?(?P<val>[^\s,;'\"&]+)"
            )),
        ]

    def redact_text(self, text: str) -> str:
        """Replace all matching secrets with stable placeholders."""
        for name, pattern in self.patterns:
            text = pattern.sub(lambda m, n=name: self._substitute(m, n), text)  # type: ignore
        return text

    def _substitute(self, match: re.Match[str], pattern_name: str) -> str:
        """Redact the ``val`` group if the pattern has one, else the whole match."""
        if "val" in match.re.groupindex:
            if self._already_redacted(match.group("val")):
                return match.group(0)
            start, end = match.span("val")
            offset = match.start()
            whole = match.group(0)
            value = match.group("val")
            return whole[: start - offset] + self._replace(value, pattern_name) + whole[end - offset:]
        return self._replace(match.group(0), pattern_name)

    def _already_redacted(self, value: str) -> bool:
        """True if an earlier pattern already turned this value into a placeholder.

        Matches "ip_1" or "aws_key_1LE" (placeholder followed by leftover
        characters), but not "pip_123", which only contains one by chance.
        """
        return any(
            value.startswith(p) and not value[len(p):len(p) + 1].isdigit()
            for p in self._issued
        )

    def _replace(self, value: str, pattern_name: str) -> str:
        """Return the placeholder for a value, generating a new one if needed."""
        # Never re-redact a placeholder an earlier pattern produced
        # (e.g. "Bearer jwt_1" must stay "Bearer jwt_1").
        if value in self._issued:
            return value
        if value not in self.placeholder_map:
            self.counters[pattern_name] = self.counters.get(pattern_name, 0) + 1
            placeholder = f"{pattern_name}_{self.counters[pattern_name]}"
            self.placeholder_map[value] = placeholder
            self._issued.add(placeholder)
        return self.placeholder_map[value]


def redact(lines: list[LogLine]) -> list[LogLine]:
    """Apply redaction to a batch of LogLines.

    Instantiates a single Redactor per batch so that identical secrets
    across different lines map to the exact same placeholder.

    Lines inside a multi-line ``-----BEGIN ... PRIVATE KEY-----`` block are
    replaced entirely, since the base64 body lines match no other pattern.
    """
    redactor = Redactor()
    in_private_key = False
    key_placeholder = ""

    for line in lines:
        text = line.original_text

        if not in_private_key and _PRIVATE_KEY_BEGIN.search(text) and not _PRIVATE_KEY_END.search(text):
            in_private_key = True
            key_placeholder = redactor._replace(f"__private_key_block_{line.line_id}", "private_key")

        if in_private_key:
            line.redacted_text = key_placeholder
            line.message = key_placeholder
            if _PRIVATE_KEY_END.search(text):
                in_private_key = False
            continue

        line.redacted_text = redactor.redact_text(text)
        # CRITICAL FIX: Also redact the message field, as this is what
        # Drain3 reads and puts into templates.
        line.message = redactor.redact_text(line.message)
    return lines