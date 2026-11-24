"""The guidance corpus and its in-memory search: unique keys, TCKDB-authored text, and a stable ranking."""

from __future__ import annotations

import pytest

from app.services.machine_review.agent import guidance as agent_guidance
from app.services.machine_review.agent.guidance import GuidanceEntry, GuidanceIndex
from app.services.machine_review.guidance_corpus import (
    build_guidance_entries,
    default_guidance_index,
    guidance_seed_codes,
)
from app.services.machine_review.schemas import KEY_MAX_CHARS


@pytest.fixture(scope="module")
def entries():
    return build_guidance_entries()


@pytest.fixture(scope="module")
def index():
    return default_guidance_index()


def _entry(key, text="some text", kind="check", codes=()):
    return GuidanceEntry(source_key=key, kind=kind, title=key, text=text, codes=codes)


def test_the_agent_key_cap_matches_the_output_contract():
    assert agent_guidance.KEY_MAX_CHARS == KEY_MAX_CHARS


def test_every_entry_has_a_unique_short_key_and_text(entries):
    keys = [e.source_key for e in entries]
    assert len(keys) == len(set(keys))
    assert all(0 < len(k) <= KEY_MAX_CHARS for k in keys)
    assert all(e.text.strip() for e in entries)


def test_the_corpus_counts_follow_their_sources(entries):
    from app.scientific_checks.numbering import numbered_register
    from app.services.deposit_warning_messages import RENDERED

    by_kind = {kind: [e for e in entries if e.kind == kind] for kind in ("deposit_warning", "check", "threshold")}
    assert len(by_kind["deposit_warning"]) == len(RENDERED) > 0
    assert len(by_kind["check"]) == len(numbered_register()) > 0
    names = {t.name for _, check in numbered_register() for t in check.thresholds}
    assert len(by_kind["threshold"]) == len(names) > 0


def test_entry_text_is_the_text_tckdb_declares(entries):
    from app.scientific_checks.numbering import numbered_register
    from app.services.deposit_warning_messages import RENDERED

    by_key = {e.source_key: e for e in entries}
    assert all(by_key[f"warning:{code}"].text == text for code, text in RENDERED.items())
    assert all(by_key[f"check:{n}"].text == check.asserts for n, check in numbered_register())


def test_a_full_sentence_finds_its_own_warning_first(index):
    from app.services.deposit_warning_messages import RENDERED

    misses = [c for c, text in RENDERED.items() if index.search(text, limit=1)[0].source_key != f"warning:{c}"]
    assert misses == []


def test_a_bare_code_finds_its_own_warning_first(index):
    from app.services.deposit_warning_messages import RENDERED

    misses = [c for c in RENDERED if index.search(c, limit=1)[0].source_key != f"warning:{c}"]
    assert misses == []


def test_a_query_with_no_shared_token_returns_nothing_and_a_matching_twin_returns_some(index):
    assert index.search("zzqxj wvbnm") == []
    assert len(index.search("spin contamination")) >= 1


def test_the_kind_filter_keeps_one_kind_and_the_unfiltered_twin_has_more(index):
    only = index.search("tau", kind="threshold", limit=5)
    assert only and {e.kind for e in only} == {"threshold"}
    assert len({e.kind for e in index.search("spin contamination", limit=20)}) > 1


def test_results_never_exceed_the_limit(index):
    assert len(index.search("the", limit=3)) <= 3


def test_entries_for_codes_lists_the_warning_then_the_check_that_declares_it(index):
    code = "spin_contamination_above_conventional_threshold"
    keys = [e.source_key for e in index.entries_for_codes([code], limit=5)]
    assert keys[0] == f"warning:{code}"
    assert len(keys) >= 2 and keys[1].startswith("check:")
    assert index.entries_for_codes(["no_such_code"], limit=5) == []


def test_duplicate_keys_are_refused_and_the_unique_twin_builds():
    with pytest.raises(ValueError):
        GuidanceIndex([_entry("a:1"), _entry("a:1")])
    assert len(GuidanceIndex([_entry("a:1"), _entry("a:2")]).entries) == 2


@pytest.mark.parametrize("bad", [_entry(""), _entry("k" * 65), _entry("a:1", text="  ")])
def test_empty_or_oversized_entries_are_refused(bad):
    with pytest.raises(ValueError):
        GuidanceIndex([bad])


def test_exact_key_match_ranks_first_among_equals():
    index = GuidanceIndex([_entry("a:1", text="alpha beta"), _entry("a:2", text="alpha beta alpha beta alpha")])
    assert index.search("alpha")[0].source_key == "a:2"
    assert index.search("a:1")[0].source_key == "a:1"


def test_seed_codes_are_sorted_and_unique():
    from types import SimpleNamespace as NS

    w = lambda c: NS(code=c, message="m")  # noqa: E731
    summaries = {("thermo", "1"): NS(deposit_warnings=(w("b"), w("a")))}
    context = NS(precheck_context=NS(record_summaries=(NS(deposit_warnings=(w("a"),)),), submission_warnings=(w("c"),)))
    assert guidance_seed_codes(context, summaries) == ("a", "b", "c")
    assert guidance_seed_codes(NS(precheck_context=None), {}) == ()
