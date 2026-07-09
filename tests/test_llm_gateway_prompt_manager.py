from llm_gateway.prompt_manager import PromptManager, content_hash


def test_prompt_manager_register_get_render_and_hash() -> None:
    manager = PromptManager()
    prompt = manager.register_prompt(
        agent_name="unit_agent",
        version="v1",
        template="Hello {name}",
        description="unit test",
    )

    assert prompt.content_hash == content_hash("Hello {name}")
    active = manager.get_active_prompt("unit_agent")
    assert active.version == "v1"
    assert manager.render_prompt("unit_agent", {"name": "Codex"}) == "Hello Codex"


def test_default_prompts_exist() -> None:
    manager = PromptManager()

    assert manager.get_active_prompt("technical_agent").version == "v0.3-phase5"
    assert manager.get_active_prompt("controller_agent").is_active is True
