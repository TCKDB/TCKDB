"""A credential the server never issued must not reach an env file.

``scripts/dev_login.sh`` used to mint the API key with

    API_KEY="$(jq -r '.key' <<<"$KEY_RESPONSE")"

``jq -r`` prints the four characters ``null`` for a document with no ``key``
field and **exits 0**, so ``set -euo pipefail`` never fired. The script then
wrote ``export TCKDB_API_KEY='null'`` into ``.tckdb_auth.env``, ``chmod 600``'d
it, printed a masked value that looks exactly like a real key, and reported
``==> Done.`` Everything downstream got a 401, whose obvious reading is "the
server rejected my key" rather than "there is no key" -- and the cost of that
misreading is measured in hours, not minutes.

``scripts/tckdb_auth.sh`` mints against the same endpoint and always refused
correctly. The two implementations of "read the key out of the response" are
why one of them could be wrong for as long as it was, so these tests exercise
the single shared one in ``scripts/lib/auth_key.sh`` and then check that
neither script has grown a second.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = BACKEND_ROOT / "scripts"
LIB = SCRIPTS / "lib" / "auth_key.sh"

#: Both scripts that mint a key against ``POST /auth/api-keys``.
KEY_MINTING_SCRIPTS = (SCRIPTS / "dev_login.sh", SCRIPTS / "tckdb_auth.sh")


def _code_lines(script: Path) -> str:
    """*script* with whole-line comments removed.

    Both scripts document the ``jq -r '.key'`` hazard in prose, and a naive
    substring search over the whole file finds the warning rather than the
    defect -- the guard would then fail on the very commit that fixed it.
    """
    return "\n".join(
        line
        for line in script.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def _run(snippet: str) -> subprocess.CompletedProcess[str]:
    """Run *snippet* in bash with the library sourced, under the real -euo."""
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail\nsource "{LIB}"\n{snippet}'],
        capture_output=True,
        text=True,
        check=False,
    )


#: Response bodies that carry no usable key. Each is something a deployment
#: has actually been observed to return in place of a minted key.
KEYLESS_RESPONSES = {
    "no key field": '{"detail":"Not authenticated"}',
    "explicit json null": '{"key": null}',
    "empty string key": '{"key": ""}',
    "key is not a string": '{"key": 12345}',
    "empty object": "{}",
    "json array": "[]",
    "html from a proxy": "<html><body>502 Bad Gateway</body></html>",
    "empty body": "",
}


@pytest.mark.parametrize(
    "body", KEYLESS_RESPONSES.values(), ids=list(KEYLESS_RESPONSES)
)
def test_a_response_with_no_key_is_refused(body: str) -> None:
    """``require_api_key`` must fail, and must not print a key-shaped thing."""
    result = _run(f"require_api_key {body!r}")

    assert result.returncode != 0, (
        f"require_api_key accepted a keyless response ({body!r}) and exited 0. "
        "That is precisely the shape that wrote TCKDB_API_KEY='null'."
    )
    assert result.stdout == "", (
        f"require_api_key printed {result.stdout!r} for a keyless response; "
        "the caller assigns stdout to the credential."
    )
    assert "null" not in result.stdout


def test_the_literal_string_null_is_never_produced_from_a_missing_field() -> None:
    """The exact regression: absence must not render as the word ``null``.

    Asserted against the shape the script uses -- assignment from a command
    substitution -- because that is where ``jq -r``'s exit-0-with-"null"
    behaviour was invisible.
    """
    result = _run(
        'API_KEY="$(require_api_key \'{"detail":"nope"}\')" || API_KEY="<refused>"\n'
        'printf "%s" "$API_KEY"'
    )

    assert result.stdout == "<refused>", (
        f"got {result.stdout!r}. A missing key must make the assignment fail, "
        "not succeed with a four-character credential."
    )


def test_jq_still_has_the_behaviour_this_guards_against() -> None:
    """Pin the hazard itself, so the guard cannot become folklore.

    If a future jq stops printing ``null`` with status 0 this test fails and
    the comments above can be relaxed -- but until then the reason the shared
    helper does not use ``jq -r`` is a live property of the tool, not a
    stylistic preference.
    """
    jq = subprocess.run(["bash", "-c", "command -v jq"], capture_output=True, text=True)
    if jq.returncode != 0:
        pytest.skip("jq is not installed on this host")

    result = subprocess.run(
        ["bash", "-c", "set -euo pipefail; jq -r '.key' <<<'{\"detail\":\"nope\"}'"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and result.stdout.strip() == "null", (
        "jq no longer prints 'null' and exits 0 for a missing field "
        f"(rc={result.returncode}, stdout={result.stdout!r})."
    )


def test_a_real_key_survives_extraction() -> None:
    """Guard the guard: a helper that refused everything would pass above."""
    result = _run("require_api_key '{\"key\": \"tckdb_live_abcdef123456\"}'")
    assert result.returncode == 0
    assert result.stdout == "tckdb_live_abcdef123456"


def test_mask_key_shows_neither_the_whole_key_nor_a_short_one() -> None:
    long_key = _run('mask_key "tckdb_abcdefghijklmnop"')
    assert long_key.stdout == "tckdb_...mnop"
    assert long_key.stdout.isascii()

    short_key = _run('mask_key "short"')
    assert short_key.stdout == "***", (
        "a key short enough that 6 + 4 characters would reveal most of it must "
        "be masked entirely"
    )


@pytest.mark.parametrize("script", KEY_MINTING_SCRIPTS, ids=lambda p: p.name)
def test_no_script_extracts_a_credential_with_jq(script: Path) -> None:
    """Neither script may grow a second, weaker key parser.

    ``jq -r`` on a credential field is the whole defect: it cannot distinguish
    absent from null from the string "null", and it reports success for all
    three.
    """
    source = _code_lines(script)
    for field in ("key", "api_key", "token", "plain_key", "secret"):
        needle = f"jq -r '.{field}'"
        assert needle not in source, (
            f"{script.name} extracts a credential with {needle}. jq -r prints "
            "the literal string 'null' and exits 0 when the field is absent. "
            "Use require_api_key from scripts/lib/auth_key.sh."
        )


@pytest.mark.parametrize("script", KEY_MINTING_SCRIPTS, ids=lambda p: p.name)
def test_both_key_minting_scripts_use_the_shared_helper(script: Path) -> None:
    """One implementation, or the two drift apart again."""
    source = script.read_text()
    assert "lib/auth_key.sh" in source, (
        f"{script.name} mints an API key without sourcing the shared helper."
    )
    assert "require_api_key" in source
