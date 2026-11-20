"""LLM choice is made once and defaults to local Ollama unless a cloud key is configured."""
import config
import pytest

import llm_selector


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("LLM_PROVIDER", "OPENROUTER_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(config, name, None, raising=False)
    llm_selector.get_llm_choice.cache_clear()
    yield
    llm_selector.get_llm_choice.cache_clear()


def test_auto_without_keys_uses_local_ollama():
    c = llm_selector.get_llm_choice()
    assert c.provider == "ollama" and not c.is_cloud and c.api_key == ""


def test_auto_with_cloud_key_uses_cloud_with_ollama_fallback(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    c = llm_selector.get_llm_choice()
    assert c.provider == "groq" and c.is_cloud and "Ollama" in c.describe()


def test_local_forced_even_when_keys_exist(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("LLM_PROVIDER", "local")
    assert llm_selector.get_llm_choice().provider == "ollama"


def test_named_provider_without_key_falls_back_to_local(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    c = llm_selector.get_llm_choice()
    assert c.provider == "ollama" and "OPENROUTER_API_KEY is not set" in c.reason


def test_choice_is_cached_for_the_whole_process(monkeypatch):
    first = llm_selector.get_llm_choice()
    monkeypatch.setenv("GROQ_API_KEY", "added-later")
    assert llm_selector.get_llm_choice() is first
