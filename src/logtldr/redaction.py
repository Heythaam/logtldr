"""Secret redaction (AD-6)."""

import re

from logtldr.models import LogLine


class Redactor:
    """Applies regex patterns to redact secrets into stable placeholders.

    The placeholder map and counters are kept strictly inside this class.
    They are never written to the LogLine models, ensuring no secrets leak
    into the rendered output.
    """

    def __init__(self) -> None:
        self.placeholder_map: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        
        # Ordered by specificity (most specific first)
        self.patterns: list[tuple[str, re.Pattern[str]]] = [
            # 1. Env Vars (must be first so DATABASE_URL=postgres://... is caught whole)
            ("env_var", re.compile(r"\b[A-Z_]{2,}\s*=\s*[^\s'\"]+")),
            # 2. DB URLs (catch the whole string before the email/ip parsers break it apart)
            ("db_url", re.compile(r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|mssql)://[^\s,;]+")),
            # 3. Specific Tokens
            ("jwt", re.compile(r"eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.[A-Za-z0-9-_.+/=]*")),
            ("aws_key", re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}")),
            ("slack_token", re.compile(r"xox[baprs]-[0-9a-zA-Z]{10,48}")),
            # 4. Network Identifiers
            ("ipv6", re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){3,7}[A-Fa-f0-9]{0,4}\b")),
            ("ip", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b")),
            # 5. Emails
            ("email", re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")),
        ]

    def redact_text(self, text: str) -> str:
        """Replace all matching secrets with stable placeholders."""
        for name, pattern in self.patterns:
            text = pattern.sub(lambda m, n=name: self._replace(m.group(0), n), text)  # type: ignore
        return text

    def _replace(self, secret: str, pattern_name: str) -> str:
        """Return the placeholder for a secret, generating a new one if needed."""
        if secret not in self.placeholder_map:
            self.counters[pattern_name] = self.counters.get(pattern_name, 0) + 1
            placeholder = f"{pattern_name}_{self.counters[pattern_name]}"
            self.placeholder_map[secret] = placeholder
        return self.placeholder_map[secret]


def redact(lines: list[LogLine]) -> list[LogLine]:
    """Apply redaction to a batch of LogLines.

    Instantiates a single Redactor per batch so that identical secrets
    across different lines map to the exact same placeholder.
    """
    redactor = Redactor()
    for line in lines:
        line.redacted_text = redactor.redact_text(line.original_text)
    return lines