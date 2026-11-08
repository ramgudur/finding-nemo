import pytest

from app.core.config import AgentModel, Settings, get_settings


def test_defaults_parse_without_env_file():
    s = Settings(_env_file=None)
    assert s.nvidia_rpm == 35
    assert s.openrouter_rpm == 18
    assert s.demo_mode is False


def test_agent_model_splits_provider_and_id():
    s = Settings(_env_file=None, compiler_model="nvidia::nvidia/nemotron-3.5-super-vl-120b-a12b")
    assert s.agent_model("compiler") == AgentModel("nvidia", "nvidia/nemotron-3.5-super-vl-120b-a12b")


def test_agent_model_rejects_missing_separator():
    s = Settings(_env_file=None, critic_model="nvidia-nemotron")
    with pytest.raises(ValueError, match="provider"):
        s.agent_model("critic")


def test_rpm_looks_up_by_provider_name():
    s = Settings(_env_file=None, nvidia_rpm=40, openrouter_rpm=5)
    assert s.rpm("nvidia") == 40
    assert s.rpm("openrouter") == 5
    with pytest.raises(ValueError):
        s.rpm("bogus")


def test_masked_and_repr_never_expose_keys():
    s = Settings(_env_file=None, nvidia_api_key="super-secret", tavily_api_key="also-secret")
    masked = s.masked()
    assert masked["nvidia_api_key"] == "***"
    assert masked["tavily_api_key"] == "***"
    assert "super-secret" not in repr(s)
    assert "also-secret" not in str(s)


def test_masked_leaves_empty_keys_as_is():
    s = Settings(_env_file=None, nvidia_api_key="")
    assert s.masked()["nvidia_api_key"] == ""


def test_env_vars_are_case_insensitive(monkeypatch):
    monkeypatch.setenv("NVIDIA_RPM", "99")
    s = Settings(_env_file=None)
    assert s.nvidia_rpm == 99


def test_get_settings_is_cached():
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
