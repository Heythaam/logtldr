"""Leak tests for real secret formats (LT-09)."""

import pytest

from logtldr.redaction import Redactor


# Fake secrets are assembled at runtime from low-entropy pieces, so the source
# contains nothing that secret scanners (GitGuardian, GitHub push protection)
# would flag. Never paste a real or realistic-looking secret into this file.
def s(*parts: str) -> str:
    return "".join(parts)


def fake(length: int, alphabet: str = "Ab1") -> str:
    """Low-entropy filler of the given length, e.g. 'Ab1Ab1Ab1A'."""
    return (alphabet * (length // len(alphabet) + 1))[:length]


def fake_jwt(sub: str = "test") -> str:
    """A structurally valid JWT built at runtime (unsigned, alg=none)."""
    import base64
    import json

    def b64(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{b64({'alg': 'none'})}.{b64({'sub': sub})}.{fake(12)}"


# Format: (Secret, Expected Placeholder Prefix)
TEST_CASES = [
    # --- AWS Keys (3) ---
    (s("AKIA", "IOSFODNN7EXAMPLE"), "aws_key"),
    (s("ASIA", fake(16, "A1B2")), "aws_key"),
    (s("AKIA", "QYLPMN7YTM4EXAMPLE"), "aws_key"),
    
    # --- JWTs (3) ---
    (fake_jwt("user1"), "jwt"),
    (fake_jwt("user2"), "jwt"),
    (fake_jwt("user3"), "jwt"),
    
    # --- DB URLs (4) ---
    (s("postgresql://", "user", ":", fake(10), "@host:5432/db"), "db_url"),
    (s("mysql://", "root", ":", fake(10, "Zz9"), "@127.0.0.1:3306/"), "db_url"),
    (s("mongodb://", "admin", ":", fake(10, "Cc3"), "@localhost:27017"), "db_url"),
    (s("redis://", "default", ":", fake(10, "Dd4"), "@cache:6379"), "db_url"),
    
    # --- Slack Tokens (3) ---
    (s("xoxb-", fake(24)), "slack_token"),
    (s("xoxp-", fake(24, "Zz9")), "slack_token"),
    (s("xoxa-", fake(24, "Cc3")), "slack_token"),
    
    # --- Env Vars (3) ---
    (s("DB_PASS", "WORD", "=", fake(11)), "env_var"),
    (s("API_", "KEY", "=", fake(9)), "env_var"),
    (s("SECRET_", "TOKEN", "=", fake(13)), "env_var"),
    
    # --- IPv4 Addresses (3) ---
    ("192.168.1.1", "ip"),
    ("10.0.0.1", "ip"),
    ("8.8.8.8", "ip"),
    
    # --- IPv6 Addresses (3) ---
    ("2001:0db8:85a3:0000:0000:8a2e:0370:7334", "ipv6"),
    ("fe80:0000:0000:0000:0202:b3ff:fe1e:8329", "ipv6"),
    ("2001:db8:3333:4444:5555:6666:7777:8888", "ipv6"),
    
    # --- Email Addresses (3) ---
    ("user@example.com", "email"),
    ("admin@test.co.uk", "email"),
    ("first.last@company.org", "email"),
]


@pytest.mark.parametrize("raw, expected_placeholder", TEST_CASES)
def test_redaction_leaks(raw: str, expected_placeholder: str) -> None:
    """Ensure real secret formats are completely redacted."""
    redactor = Redactor()
    text = f"Here is the value {raw} inside a log line."
    
    redacted = redactor.redact_text(text)
    
    # 1. The original value must NOT be in the redacted output
    assert raw not in redacted
    
    # 2. The appropriate placeholder must be present
    # Since each test runs with a fresh Redactor, the counter will be 1
    assert f"{expected_placeholder}_1" in redacted

# ── LT-45: formats that leaked before ───────────────────────────────
#
# (Fake secrets are built with fake() / fake_jwt() at the top of this file.)


LT45_CASES = [
    # --- OpenAI keys ---
    (s("sk-proj-", fake(32)), "openai_key"),
    (s("sk-", fake(24)), "openai_key"),
    # --- GitHub tokens ---
    (s("ghp_", fake(36)), "github_token"),
    (s("gho_", fake(36, "Zz9")), "github_token"),
    (s("github_pat_", fake(40, "Ab1_")), "github_token"),
    # --- Env vars with digits in the name ---
    (s("S3_", "SECRET", "=", fake(16)), "env_var"),
    (s("OAUTH2_", "TOKEN", "=", fake(12)), "env_var"),
]


@pytest.mark.parametrize("raw, expected_placeholder", LT45_CASES)
def test_lt45_token_formats(raw: str, expected_placeholder: str) -> None:
    """Token formats that used to reach the LLM unredacted."""
    redactor = Redactor()
    redacted = redactor.redact_text(f"Here is the value {raw} inside a log line.")

    assert raw not in redacted
    assert f"{expected_placeholder}_1" in redacted


_V = fake(14)  # stand-in secret value used in every key/value case

# Key and separator are kept apart and joined at runtime, so the source never
# contains a literal "key=value" pair for secret scanners to flag.
# (text before, key, separator, text after)
KEY_VALUE_CASES = [
    ("Authorization: ", "Bearer", " ", ""),
    ("auth header ", "bearer", " ", ""),
    ("connecting with ", "password", "=", " user=admin"),
    ("login ", "passwd", ": ", ""),
    ("", "db_password", "=", ""),
    ("", "client_secret", "=", ""),
    ("using ", "api_key", ": ", ""),
    ("using ", "apikey", "=", ""),
    ("{", '"token"', ': "', '"}'),
    ("", "Password", "=", ""),
]


@pytest.mark.parametrize("before, key, sep, after", KEY_VALUE_CASES)
def test_lt45_key_value_secrets(before: str, key: str, sep: str, after: str) -> None:
    """Secret-sounding keys keep their name; only the value is redacted."""
    placeholder = "bearer_1" if key.lower() == "bearer" else "secret_kv_1"
    line = s(before, key, sep, _V, after)

    redacted = Redactor().redact_text(line)

    assert _V not in redacted
    assert s(key, sep, placeholder) in redacted


def test_lt45_bearer_jwt_not_double_redacted() -> None:
    """A JWT after Bearer is redacted once, as a JWT."""
    redacted = Redactor().redact_text(f"Authorization: Bearer {fake_jwt()}")

    assert redacted == "Authorization: Bearer jwt_1"


def _pem_line(kind: str, label: str = "") -> str:
    """Build a PEM BEGIN/END boundary line at runtime."""
    return s("-" * 5, kind, " ", label, "PRIVATE", " KEY", "-" * 5)


def test_lt45_private_key_single_line() -> None:
    """A key on one line (e.g. escaped newlines in JSON) is fully redacted."""
    body = fake(40)
    key = s(_pem_line("BEGIN"), "\\n", body, "\\n", _pem_line("END"))
    redacted = Redactor().redact_text(f'{{"key": "{key}"}}')

    assert body not in redacted
    assert "private_key_1" in redacted


def test_lt45_private_key_multiline_block() -> None:
    """Every line of a multi-line key block is replaced, body included."""
    from logtldr.models import LogLine
    from logtldr.redaction import redact

    body1, body2 = fake(40), fake(40, "Zz9")
    raw = [
        "INFO loading credentials",
        _pem_line("BEGIN", "RSA "),
        body1,
        body2,
        _pem_line("END", "RSA "),
        "INFO credentials loaded",
    ]
    lines = [
        LogLine(line_id=i, original_text=t, redacted_text=t, level="INFO",
                message=t, source_format="plaintext")
        for i, t in enumerate(raw, start=1)
    ]

    out = redact(lines)

    for line in out[1:5]:
        assert line.redacted_text == "private_key_1"
        assert line.message == "private_key_1"
    assert out[0].redacted_text == "INFO loading credentials"
    assert out[5].redacted_text == "INFO credentials loaded"
    joined = "\n".join(line.redacted_text + line.message for line in out)
    assert body1 not in joined and body2 not in joined


@pytest.mark.parametrize("line", [
    "processed 512 tokens in 2.1s",
    "max_tokens=4096 reached",
    "INFO request completed in 120ms",
    "WARN disk usage 90% on /var",
])
def test_lt45_no_false_positives(line: str) -> None:
    """Ordinary log text must not be redacted."""
    assert Redactor().redact_text(line) == line