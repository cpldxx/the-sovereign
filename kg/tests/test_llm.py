"""Model settings follow the agent's own model: one agent may run on Claude while another runs locally."""

from core import llm


def test_settings_follow_each_agents_own_model(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "anthropic:claude-opus-5-5")
    monkeypatch.setenv("LLM_MODEL_EXTRACTOR", "ollama:qwen3.6:35b")
    local = llm.model_settings("extractor", decisive=True)
    assert local["presence_penalty"] == 0.0 and local["temperature"] == llm.DECISION_TEMPERATURE
    assert "anthropic_effort" not in local
    if not llm.THINKING:
        assert local["thinking"] is False                          # the local model keeps reasoning off
    assert llm.model_settings("validator") == {"anthropic_effort": llm.EFFORT}   # no override: LLM_MODEL, Claude


def test_a_claude_agent_on_a_local_lab_gets_only_the_effort(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "ollama:qwen3.6:35b")
    monkeypatch.setenv("LLM_MODEL_EXAMINER", "anthropic:claude-opus-5-5")
    assert llm.model_settings("examiner", decisive=True) == {"anthropic_effort": llm.EFFORT}   # no thinking: False
    assert "anthropic_effort" not in llm.model_settings("linker", decisive=True)
    assert llm.model_name("examiner") == "anthropic:claude-opus-5-5"
    assert llm.model_name() == "ollama:qwen3.6:35b"
