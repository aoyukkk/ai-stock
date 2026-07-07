from backend.core.config import Settings
from backend.core.exceptions import AppException, register_exception_handlers
from backend.schemas.response import error_response, success_response


ENV_FIELDS = [
    "APP_ENV",
    "LOG_LEVEL",
    "TIMEZONE",
    "DATABASE_URL",
    "REDIS_URL",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
]


def test_settings_load_default_values(monkeypatch) -> None:
    for field in ENV_FIELDS:
        monkeypatch.delenv(field, raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.log_level == "INFO"
    assert settings.timezone == "Asia/Shanghai"
    assert settings.database_url == ""
    assert settings.redis_url == ""
    assert settings.deepseek_api_key == ""
    assert settings.openai_api_key == ""


def test_settings_load_environment_overrides(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "testing")
    settings = Settings(_env_file=None)
    assert settings.app_env == "testing"


def test_success_response_contains_standard_keys() -> None:
    response = success_response(data={"status": "ok"}, message="healthy")
    assert set(response) == {"success", "data", "message"}
    assert response["success"] is True


def test_error_response_contains_standard_keys() -> None:
    response = error_response(message="internal server error")
    assert set(response) == {"success", "data", "message"}
    assert response["success"] is False


def test_exception_handler_can_be_imported() -> None:
    assert AppException is not None
    assert callable(register_exception_handlers)
