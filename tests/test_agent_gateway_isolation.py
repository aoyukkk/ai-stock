from pathlib import Path


def test_agents_do_not_import_provider_sdks_or_real_provider_classes() -> None:
    forbidden = ("from openai", "import openai", "anthropic", "DeepSeekLLMProvider", "httpx.Client")
    for path in Path("agents").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not any(marker in text for marker in forbidden), path
