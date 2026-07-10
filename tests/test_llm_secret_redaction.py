import json

from backend.core.security import redact_sensitive_text, sanitize_config


def test_secret_redaction_covers_bearer_database_url_and_environment(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-api-value")
    text = redact_sensitive_text(
        "Authorization: Bearer secret-api-value database=postgresql://user:db-password@localhost/db"
    )

    assert "secret-api-value" not in text
    assert "db-password" not in text
    assert "Bearer [REDACTED]" in text


def test_sensitive_config_values_are_redacted() -> None:
    payload = sanitize_config(
        {"TUSHARE_TOKEN": "secret", "DATABASE_URL": "postgresql://user:pass@db/app", "input_tokens": 5}
    )
    serialized = json.dumps(payload)

    assert "secret" not in serialized
    assert "user:pass" not in serialized
    assert payload["input_tokens"] == 5
