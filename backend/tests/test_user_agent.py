import pytest

from app.user_agent import (
    CONTACT_EMAIL_ENV,
    PROJECT_URL,
    browser_compatible_user_agent,
    tckdb_user_agent,
    without_contact,
)


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


def test_without_contact_drops_only_the_mailto(monkeypatch):
    monkeypatch.setenv(CONTACT_EMAIL_ENV, "ops@example.org")
    agent = tckdb_user_agent("mode=snapshot")
    assert "mailto" in agent
    stripped = without_contact(agent)
    assert "ops@example.org" not in stripped
    assert stripped.endswith(f"(+{PROJECT_URL}) mode=snapshot")
    assert without_contact("custom-agent/1") == "custom-agent/1"


def test_diagnostic_report_never_records_the_contact(monkeypatch):
    from app.importers.cccbdb.diagnostics.runner import DiagnosticReport

    monkeypatch.setenv(CONTACT_EMAIL_ENV, "ops@example.org")
    report = DiagnosticReport(
        created_at="t", user_agent=tckdb_user_agent("mode=resolver-debug"), diagnostic_name="n", diagnostic_version="1"
    )
    assert "ops@example.org" not in str(report.to_json())


@pytest.mark.parametrize(
    "agent",
    [
        "tool/1 (mailto:a@b.org)",
        "tool/1 (+https://x.org; MAILTO:a@b.org)",
        "tool/1 (+https://x.org; mailto: a@b.org) mode=x",
        "tool/1 (+https://x.org;mailto:a@b.org)",
    ],
)
def test_without_contact_handles_custom_forms(agent):
    out = without_contact(agent)
    assert "@" not in out and "mailto" not in out.lower()
    assert "()" not in out and "  " not in out


def test_browser_compatible_agent_without_and_with_contact(monkeypatch):
    monkeypatch.delenv(CONTACT_EMAIL_ENV, raising=False)
    plain = browser_compatible_user_agent("mode=form-resolver")
    assert plain.startswith("Mozilla/5.0 (compatible; TCKDB/")
    assert plain.endswith(f"; +{PROJECT_URL}; mode=form-resolver)")
    assert "mailto" not in plain
    monkeypatch.setenv(CONTACT_EMAIL_ENV, "ops@example.org")
    with_contact = browser_compatible_user_agent("mode=form-resolver")
    assert with_contact.endswith("; mode=form-resolver; mailto:ops@example.org)")
    assert without_contact(with_contact).endswith("; mode=form-resolver)")
