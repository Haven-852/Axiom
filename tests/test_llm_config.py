from ase.adapters.openhands import build_llm
from ase.config import Settings
from ase.infra.redact import redact


def test_llm_mapping_does_not_embed_the_key_in_the_model_name() -> None:
    settings = Settings(llm_provider="deepseek", llm_model="deepseek-flash", llm_api_key="secret-value")
    llm = build_llm(settings)
    assert llm.model == "openai/deepseek-flash"
    assert "secret-value" not in llm.model
    rendered = redact(repr(settings), "secret-value")
    assert "secret-value" not in rendered


def test_live_model_completion() -> None:
    settings = Settings()
    key = settings.llm_api_key.get_secret_value()
    if not key:
        return
    from ase.agents.llm_client import complete

    text, log = complete(settings, "Reply with the exact token ASE_OK and nothing else.", "confirm")
    assert "ASE_OK" in text
    assert key not in text
    assert key not in str(log)
    assert log["model"] == "deepseek-flash"
    assert log["usage"]
