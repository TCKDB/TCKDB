"""The guard's placeholder mode for captured selection facts (``withhold_in_document``), on synthetic facts.

``hidden_refs`` is replaced by a fixed answer, so what is under test is the walk and the tokens: where a ref is found,
what it becomes, how the numbers are assigned and what is left alone. Each test names the mutation it kills. The seam
(``withhold_selection_facts``) and the database resolution have their own tests at the end.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

import pytest

from app.db.models.common import ProfileRecommendation, ReadProfile
from app.services.scientific_read import ref_guard
from app.services.scientific_read.profile import (
    EXPLORATORY,
    ResolvedReadProfile,
    reset_current_read_profile,
    set_current_read_profile,
)

CURATED = ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)
RELEASE = ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.tckdb_curated_release)


def ref(prefix: str, n: int) -> str:
    return f"{prefix}_{str(n).zfill(26)}"


A, B, C = ref("calc", 1), ref("calc", 2), ref("calc", 3)  # sorted: A < B < C
VISIBLE = ref("calc", 9)
GROUP = ref("cg", 4)
NET = ref("net", 5)


class Kind(str, enum.Enum):
    a = "calc_" + "0" * 25 + "1"  # a str enum whose value LOOKS like a ref: it is an enum, never a string to rewrite


@dataclass(frozen=True)
class Source:
    role: str
    calculation_ref: str | None


@dataclass(frozen=True)
class Fact:
    ref: str
    sources: tuple[Source, ...] = ()
    group: str | None = None
    network_channel_ref: str | None = None
    note: str = ""
    kind: Kind = Kind.a
    by_ref: dict = field(default_factory=dict)
    members: list = field(default_factory=list)


@pytest.fixture
def hidden(monkeypatch):
    """Make ``hidden`` the answer of the review lookup (and record what it was asked)."""
    asked: list[set] = []

    def install(refs):
        def fake(session, refs_in, *, profile, unknown_is_hidden=False):
            asked.append(set(refs_in))
            assert unknown_is_hidden, "a document treats an unknown ref as hidden"
            return {r for r in refs_in if r in refs}

        monkeypatch.setattr(ref_guard, "hidden_refs", fake)
        return asked

    return install


def run(facts, *, profile=CURATED, **kw):
    kw.setdefault("mode", "placeholder")
    report = ref_guard.withhold_in_document(None, facts, profile=profile, **kw)
    return report.facts, report


def test_a_hidden_ref_becomes_a_token_and_a_visible_one_is_left_alone(hidden):
    hidden({A})
    out, report = run(Fact(ref=VISIBLE, sources=(Source("opt", A), Source("freq", VISIBLE))))
    assert out.ref == VISIBLE and [s.calculation_ref for s in out.sources] == ["withheld:1", VISIBLE]
    assert report.tokens == {A: "withheld:1"}


def test_the_numbers_follow_the_sorted_real_refs_not_the_order_they_are_met_in(hidden):
    """Kills: tokens numbered by first sighting (insertion order)."""
    hidden({A, B, C})
    forward, _ = run(Fact(ref=VISIBLE, sources=(Source("x", A), Source("y", B), Source("z", C))))
    backward, report = run(Fact(ref=VISIBLE, sources=(Source("z", C), Source("y", B), Source("x", A))))
    assert report.tokens == {A: "withheld:1", B: "withheld:2", C: "withheld:3"}
    assert [s.calculation_ref for s in forward.sources] == ["withheld:1", "withheld:2", "withheld:3"]
    assert [s.calculation_ref for s in backward.sources] == ["withheld:3", "withheld:2", "withheld:1"]


def test_one_real_ref_has_one_token_wherever_it_stands(hidden):
    """Kills: a token minted per sighting (an equality the engine reads would break)."""
    hidden({A})
    fact = Fact(
        ref=A,
        sources=(Source("opt", A),),
        group=A,
        by_ref={A: A},
        members=[A, [A]],
        network_channel_ref=f"{A}/ch1",
    )
    out, report = run(fact)
    assert report.tokens == {A: "withheld:1"}
    assert out.ref == out.sources[0].calculation_ref == out.group == "withheld:1"
    assert out.by_ref == {"withheld:1": "withheld:1"}  # a dict key too
    assert out.members == ["withheld:1", ["withheld:1"]]
    assert out.network_channel_ref == "withheld:1/ch1"  # a composite is replaced in place, its key kept


OBFUSCATED = {
    "plain": f"see {A} for the frequencies",
    "percent_encoded": "see calc%5F" + A.split("_", 1)[1] + " for the frequencies",
    "zero_width": "see calc_\u200b" + A.split("_", 1)[1] + " for the frequencies",
    "soft_hyphen": "see ca\u00adlc_" + A.split("_", 1)[1] + " for the frequencies",
}


@pytest.mark.parametrize("form", sorted(OBFUSCATED))
def test_prose_that_names_a_hidden_ref_is_blanked_whole_in_every_spelling(hidden, form):
    """Detection decodes percent-encoding and drops invisible characters; so must the treatment. Blanking the whole field
    needs no second decoder, and a spelling the first one misses cannot leave the ref behind.

    Kills: replacing the ref inside the raw text (the percent-encoded and invisible-character spellings come back unchanged).
    """
    hidden({A})
    out, report = run(Fact(ref=VISIBLE, note=OBFUSCATED[form]))
    assert out.note == "" and [b.path for b in report.blanked] == ["note"], form
    kept, _ = run(Fact(ref=VISIBLE, note=f"see {VISIBLE} for the frequencies"))
    assert kept.note == f"see {VISIBLE} for the frequencies"


def test_a_composite_is_rewritten_only_in_its_exact_plain_shape(hidden):
    hidden({A})
    out, _ = run(Fact(ref=VISIBLE, network_channel_ref=f"{A}/ch-1"))
    assert out.network_channel_ref == "withheld:1/ch-1"
    for spelled in (f"{A}/ch 1", f"calc%5F{A[5:]}/ch-1", f"{A}/ch-1/extra", f"{A}/_x", f"{A}/ch\u200b1"):
        blanked, _ = run(Fact(ref=VISIBLE, network_channel_ref=spelled))
        assert blanked.network_channel_ref == "", spelled


@pytest.mark.parametrize("key", [f"{B}", f"x_{B}", f"ch-{B}", f"{VISIBLE}"], ids=["hidden", "prefixed", "suffixed", "visible"])
def test_a_composite_whose_key_names_another_gated_ref_is_blanked_whole(hidden, key):
    """``net_<hidden>/calc_<hidden2>`` and ``net_<hidden>/x_calc_<hidden2>`` leaked the second ref through the key.

    The key must name no gated ref at all, hidden or not, under the boundary matcher.
    Kills: the key check dropped from the composite branch."""
    hidden({NET, B})
    out, report = run(Fact(ref=VISIBLE, network_channel_ref=f"{NET}/{key}"))
    assert out.network_channel_ref == "" and [b.path for b in report.blanked] == ["network_channel_ref"]


def test_a_composite_shaped_value_in_an_unregistered_field_is_prose_and_is_blanked(hidden):
    """Only the registered fields hold a composite; ``invalidated_reason`` with that shape is text that names a hidden ref.

    Kills: the field restriction dropped (the value would be rewritten)."""
    hidden({NET})
    out, report = run(Fact(ref=VISIBLE, note=f"{NET}/ch-1"))
    assert out.note == "" and [b.path for b in report.blanked] == ["note"]
    assert "network_channel_ref" in ref_guard.COMPOSITE_REF_FIELDS


def test_a_blanked_invalidation_reason_still_invalidates_the_rotor(hidden):
    """``required_rotor_invalidated`` tests ``invalidated_reason is not None``: blanked to ``""`` the claim stays.

    The invalidation is the visible record's own claim about itself; only the words naming the hidden record go.
    Kills: blanking to ``None`` (the rotor would read as valid and the record would qualify)."""
    from dataclasses import replace

    from tests.services.statmech_selection._support import assess, codes, declared, record
    from tests.services.statmech_selection.test_assessment_accounting import TORSION, rotor

    hidden({A})
    bad = replace(TORSION, invalidated_reason=f"scan failed, see calc%5F{A[5:]}")
    candidate = declared(recipe={"rotors": [rotor()]}, record=record(torsions=(bad,), statmech_treatment="rrho_1d"))
    before = codes(assess(candidate))
    out, report = run(candidate)
    assert out.record.torsions[0].invalidated_reason == "" and report.blanked
    assert "required_rotor_invalidated" in before and codes(assess(out)) == before


def test_a_str_enum_is_not_a_string_to_rewrite(hidden):
    hidden({Kind.a.value})
    out, report = run(Fact(ref=VISIBLE))
    assert out.kind is Kind.a and not report.tokens and out is not None


def test_the_input_is_never_mutated_and_an_untouched_fact_is_the_same_object(hidden):
    hidden({A})
    touched = Fact(ref=VISIBLE, sources=(Source("opt", A),))
    clean = Fact(ref=VISIBLE, sources=(Source("opt", VISIBLE),))
    out, _ = run(touched)
    assert touched.sources[0].calculation_ref == A and out is not touched
    same, _ = run(clean)
    assert same is clean


def test_nothing_hidden_costs_no_rewrite_and_a_fixed_answer_is_one_lookup(hidden):
    asked = hidden(set())
    facts = (Fact(ref=VISIBLE), Fact(ref=GROUP))
    out, report = run(facts)
    assert out is facts and not report.tokens
    assert len(asked) == 1 and asked[0] == {VISIBLE, GROUP}


def test_exploratory_reads_nothing(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a lookup under a profile with no review floor")

    monkeypatch.setattr(ref_guard, "hidden_refs", boom)
    facts = Fact(ref=A)
    report = ref_guard.withhold_in_document(None, facts, profile=EXPLORATORY, mode="placeholder")
    assert report.facts is facts and not report.tokens


def test_look_alikes_are_not_refs(hidden):
    hidden({A})
    upper, _ = run(Fact(ref=VISIBLE, group=A.upper()))
    assert upper.group == A.upper()
    suffixed, _ = run(Fact(ref=VISIBLE, group=A + "x"))
    assert suffixed.group == A + "x"


def test_a_token_is_not_a_ref():
    assert ref_guard.is_withheld_token("withheld:12")
    for not_a_token in ("withheld:0", "withheld:", "withheld:01", "withheld:1x", " withheld:1", A, None, 3):
        assert not ref_guard.is_withheld_token(not_a_token)
    assert not ref_guard.GATED_REF_RE.match("withheld:1")


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


class Recorder:
    """A session that answers 'every ref is visible'. Anything that reads it instead of the engine's session shows."""

    def __init__(self):
        self.statements = 0

    def execute(self, stmt):
        self.statements += 1
        return self

    def all(self):  # pragma: no cover - only reached by a mutation
        return [(r, False) for r in (A, B, C, VISIBLE)]


def test_the_seam_reads_visibility_from_the_session_it_is_given_not_the_request_context(hidden, monkeypatch):
    """Kills: the review state read through the request's context session instead of the selection's snapshot.

    The context here would say every ref is visible (as a session that saw a later state might); the engine's own
    session says ``A`` is hidden, and the engine's session is the one that decides.
    """
    seen_sessions = []

    def fake(session, refs_in, *, profile, unknown_is_hidden=False):
        seen_sessions.append(session)
        return {r for r in refs_in if r == A}

    monkeypatch.setattr(ref_guard, "hidden_refs", fake)
    engine_session, context_session = object(), Recorder()
    ctx = ref_guard.RefGuardContext(session=context_session, route_path="/api/v1/scientific/species-entries/x")
    ctx_token = ref_guard.set_ref_guard_context(ctx)
    profile_token = set_current_read_profile(CURATED)
    try:
        out = ref_guard.withhold_selection_facts(engine_session, Fact(ref=VISIBLE, sources=(Source("opt", A),)))
    finally:
        reset_current_read_profile(profile_token)
        ref_guard.reset_ref_guard_context(ctx_token)
    assert seen_sessions == [engine_session]
    assert out.sources[0].calculation_ref == "withheld:1"
    assert ctx.documents == 1 and ctx.document_reports[0].tokens == {A: "withheld:1"}


def test_the_seam_does_nothing_without_a_floor_and_for_a_release_backed_profile(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("looked up")

    monkeypatch.setattr(ref_guard, "hidden_refs", boom)
    facts = Fact(ref=A)
    for profile in (EXPLORATORY, RELEASE):
        token = set_current_read_profile(profile)
        try:
            assert ref_guard.withhold_selection_facts(object(), facts) is facts
        finally:
            reset_current_read_profile(token)


# ---------------------------------------------------------------------------
# Resolution against the database
# ---------------------------------------------------------------------------


def test_an_unknown_ref_is_hidden_for_a_document_and_not_for_the_response_guard(db_session):
    """Hidden == unknown: a ref that names no row answers 404 like a hidden one, so a document must not tell them apart.

    Kills: the unknown ref returned as it is (a document would then say which of the two it was).
    """
    from tests.api.scientific._curated_sweep_world import build_sweep_world

    world = build_sweep_world(db_session)
    hidden_ref = world.hidden["calc_h"].public_ref
    visible_ref = world.visible["calc_vopt"].public_ref
    unknown_ref = ref("calc", 12345678)
    refs = {hidden_ref, visible_ref, unknown_ref}
    assert ref_guard.hidden_refs(db_session, refs, profile=CURATED) == {hidden_ref}
    assert ref_guard.hidden_refs(db_session, refs, profile=CURATED, unknown_is_hidden=True) == {hidden_ref, unknown_ref}
    report = ref_guard.withhold_in_document(
        db_session, (hidden_ref, visible_ref, unknown_ref), profile=CURATED, mode="placeholder"
    )
    out = report.facts
    assert out[1] == visible_ref and {out[0], out[2]} == {"withheld:1", "withheld:2"}
    assert report.tokens == {hidden_ref: out[0], unknown_ref: out[2]}


# ---------------------------------------------------------------------------
# The two modes of the one function
# ---------------------------------------------------------------------------


def test_null_mode_is_unchanged_next_to_placeholder_mode(hidden):
    """Same function, same facts: ``"null"`` nulls a value and keeps ``None`` for free text, ``"placeholder"`` tokenises
    and blanks to ``""``.

    Kills: placeholder blanking leaking into null mode (the exports would change), or null mode tokenising."""
    hidden({A})
    docs = [{"ref": A, "note": f"see {A}", "keep": VISIBLE}]
    nulled = ref_guard.withhold_in_document(None, docs, profile=CURATED, mode="null")
    assert nulled.facts == [{"ref": None, "note": None, "keep": VISIBLE}] and not nulled.tokens
    tokenised = ref_guard.withhold_in_document(None, docs, profile=CURATED, mode="placeholder")
    assert tokenised.facts == [{"ref": "withheld:1", "note": "", "keep": VISIBLE}]
    assert docs[0]["ref"] == A  # neither mode mutates its input


def test_null_mode_copies_only_when_something_is_withheld(hidden):
    """Nothing hidden: ``report.facts`` is the input itself (no deep copy). Something hidden: a copy, input untouched."""
    hidden({A})
    clean = [{"ref": VISIBLE}]
    assert ref_guard.withhold_in_document(None, clean, profile=CURATED, mode="null").facts is clean
    assert ref_guard.withhold_in_document(None, clean, profile=EXPLORATORY, mode="null").facts is clean
    dirty = [{"ref": A}]
    out = ref_guard.withhold_in_document(None, dirty, profile=CURATED, mode="null").facts
    assert out is not dirty and out == [{"ref": None}] and dirty == [{"ref": A}]


def test_a_release_backed_profile_is_left_alone_by_every_entry_point(hidden):
    """One predicate (``guard_active``): the typed hook and both document modes agree."""
    from pydantic import BaseModel

    hidden({A})
    docs = [{"ref": A}]
    for mode in ("null", "placeholder"):
        assert ref_guard.withhold_in_document(None, docs, profile=RELEASE, mode=mode).facts is docs

    class Model(BaseModel):
        ref: str

    model = Model(ref=A)
    assert ref_guard.withhold_in_model(None, model, profile=RELEASE).sites == [] and model.ref == A
