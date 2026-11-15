import pytest

from app.user_agent import CONTACT_EMAIL_ENV, PROJECT_URL, tckdb_user_agent


def test_user_agent_names_the_project_without_a_contact_by_default(monkeypatch):
    monkeypatch.delenv(CONTACT_EMAIL_ENV, raising=False)
    agent = tckdb_user_agent()
    assert agent.startswith("TCKDB/")
    assert agent.endswith(f"(+{PROJECT_URL})")
    assert "mailto" not in agent


def test_user_agent_appends_the_contact_when_the_env_var_is_set(monkeypatch):
    monkeypatch.setenv(CONTACT_EMAIL_ENV, "ops@example.org")
    assert tckdb_user_agent().endswith(f"(+{PROJECT_URL}; mailto:ops@example.org)")


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_contact_is_ignored(monkeypatch, blank):
    monkeypatch.setenv(CONTACT_EMAIL_ENV, blank)
    assert "mailto" not in tckdb_user_agent()


def test_suffix_follows_the_agent(monkeypatch):
    monkeypatch.delenv(CONTACT_EMAIL_ENV, raising=False)
    assert tckdb_user_agent("mode=snapshot").endswith(f"(+{PROJECT_URL}) mode=snapshot")
