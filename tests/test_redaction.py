"""Tests for secret redaction (AD-6)."""

from logtldr.models import LogLine
from logtldr.redaction import Redactor, redact


def test_stable_mapping() -> None:
    """Same secret -> same placeholder within a run."""
    redactor = Redactor()
    text = "Connecting to 192.168.1.1. Again, 192.168.1.1."
    out = redactor.redact_text(text)
    
    assert out == "Connecting to ip_1. Again, ip_1."
    assert len(redactor.placeholder_map) == 1


def test_all_patterns() -> None:
    """Verify all major secret types are caught and named correctly."""
    redactor = Redactor()
    
    # Dynamically build dummy secrets to bypass GitHub Secret Scanning
    aws_dummy = "AKIA" + "IOSFODNN7EXAMPLE"
    jwt_dummy = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "dummy_signature"
    db_dummy = "postgresql://admin:" + "supersecret" + "@db.example.com:5432/prod"
    slack_dummy = "xoxb-" + "PLACEHOLDERTOKEN12345"
    env_dummy = "DB_PASSWORD=" + "hunter2"
    ipv4_dummy = "8.8.8.8"
    ipv6_dummy = "2001:0db8:85a3:0000:0000:8a2e:0370:7334"
    email_dummy = "user@example.com"
    
    text = (
        f"AWS: {aws_dummy}\n"
        f"JWT: {jwt_dummy}\n"
        f"DB: {db_dummy}\n"
        f"Slack: {slack_dummy}\n"
        f"Env: {env_dummy}\n"
        f"IPv4: {ipv4_dummy}\n"
        f"IPv6: {ipv6_dummy}\n"
        f"Email: {email_dummy}\n"
    )
    
    out = redactor.redact_text(text)
    
    assert aws_dummy not in out
    assert "aws_key_1" in out
    
    assert jwt_dummy not in out
    assert "jwt_1" in out
    
    assert db_dummy not in out
    assert "db_url_1" in out
    
    assert slack_dummy not in out
    assert "slack_token_1" in out
    
    assert env_dummy not in out
    assert "env_var_1" in out
    
    assert ipv4_dummy not in out
    assert "ip_1" in out
    
    assert ipv6_dummy not in out
    assert "ipv6_1" in out
    
    assert email_dummy not in out
    assert "email_1" in out


def test_db_url_fully_redacted() -> None:
    """A DB URL must not be half-replaced by the IP or email parsers."""
    redactor = Redactor()
    url = "postgresql://user:" + "pass" + "@10.0.0.1:5432/db"
    out = redactor.redact_text(f"Connecting to {url} now.")
    
    # It should be a single db_url_1 placeholder, not a mix of email_1 and ip_1
    assert out == "Connecting to db_url_1 now."
    assert "postgresql://" not in out
    assert "10.0.0.1" not in out


def test_redact_lines_pipeline() -> None:
    """Test the full batch pipeline function."""
    lines = [
        LogLine(line_id=1, original_text="IP is 1.1.1.1", redacted_text="", level="INFO", message="", source_format="plaintext"),
        LogLine(line_id=2, original_text="IP is 1.1.1.1", redacted_text="", level="INFO", message="", source_format="plaintext"),
    ]
    out = redact(lines)
    
    # Both lines should have the exact same placeholder
    assert out[0].redacted_text == "IP is ip_1"
    assert out[1].redacted_text == "IP is ip_1"
    
    # Original text must remain untouched
    assert out[0].original_text == "IP is 1.1.1.1"


def test_message_field_is_redacted() -> None:
    """The message field must be redacted so Drain3 doesn't leak secrets."""
    lines = [
        LogLine(
            line_id=1,
            original_text="2024-01-01 INFO Connecting to postgresql://admin:secret@host",
            message="Connecting to postgresql://admin:secret@host",
            redacted_text="",
            level="INFO",
            source_format="plaintext",
        )
    ]
    out = redact(lines)
    
    # Both text fields must be free of the secret
    assert "secret" not in out[0].redacted_text
    assert "secret" not in out[0].message
    assert "db_url_1" in out[0].message