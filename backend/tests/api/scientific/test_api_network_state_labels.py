"""Three hydrazine channels that used to render as two strings and a self-loop.

The shape is taken from the deployed hydrazine pressure-dependent network, not
invented: two ``species_entry`` rows for diazene sit under **one** ``species``
row, so both carry ``canonical_smiles == "N=N"`` and only their entry-level
discriminator tells them apart. A renderer that builds a channel label by
joining ``canonical_smiles`` with ``" + "`` — which is what every consumer of
this API did, including ``examples/clients/explore_tckdb.py`` — therefore
printed three genuinely different channels as:

    NN -> N=N + [H][H]
    NN -> N=N + [H][H]
    N=N + [H][H] -> N=N + [H][H]

Two identical strings for two different channels, and a third that reads as a
channel from a state to itself. The stored data was never wrong: the
composition hashes separate the states cleanly and pairing Chebyshev fits to
PLOG fits on those hashes gives a clean match. Only the human-facing label was
lossy, which is the worst kind of defect to leave in a paper figure — it is
invisible to every machine check and visible to every reader.

The fix is ``NetworkStateComposition.state_label``, rendered server-side from
each participant's ``species_entry_label``. This file is its proof, and it is
deliberately a *rendering* test: it builds the label exactly as a consumer
does and asserts the three strings are pairwise distinct.
"""

from __future__ import annotations

import hashlib

from app.db.models.common import (
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkStateKind,
    SpeciesEntryStateKind,
    StationaryPointKind,
)
from app.db.models.species import SpeciesEntry
from tests.services.scientific_read._factories import (
    attach_network_kinetics_chebyshev,
    attach_network_state_participant,
    make_network,
    make_network_channel,
    make_network_kinetics,
    make_network_solve,
    make_network_state,
    make_species,
    next_inchi_key,
)


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _entry(db_session, species, *, stereo_label: str | None) -> SpeciesEntry:
    """A species entry under *species*, forked only by ``stereo_label``.

    Built directly rather than through ``make_species_entry`` because the
    whole point of the fixture is two entries of one species, and
    ``stereo_label`` is the discriminator ``uq_species_entry_species_id``
    makes them differ by.
    """
    row = SpeciesEntry(
        species_id=species.id,
        kind=StationaryPointKind.minimum,
        stereo_label=stereo_label,
        electronic_state_kind=SpeciesEntryStateKind.ground,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _hydrazine_network(db_session) -> dict:
    """One species with two entries, three states, three channels.

    ``N2H4 -> E-diazene + H2``, ``N2H4 -> Z-diazene + H2`` and the
    ``E -> Z`` isomerisation between the two product states. Under the old
    renderer the first two collided and the third read as a self-loop.
    """
    network = make_network(db_session, name=f"hydrazine-{next_inchi_key('NET')}")

    diazene = make_species(
        db_session, smiles="N=N", inchi_key=next_inchi_key("DZ"), multiplicity=1
    )
    diazene_e = _entry(db_session, diazene, stereo_label="E")
    diazene_z = _entry(db_session, diazene, stereo_label="Z")

    dihydrogen = make_species(
        db_session, smiles="[H][H]", inchi_key=next_inchi_key("HH"), multiplicity=1
    )
    dihydrogen_entry = _entry(db_session, dihydrogen, stereo_label=None)

    hydrazine = make_species(
        db_session, smiles="NN", inchi_key=next_inchi_key("HZ"), multiplicity=1
    )
    hydrazine_entry = _entry(db_session, hydrazine, stereo_label=None)

    well = make_network_state(
        db_session,
        network=network,
        kind=NetworkStateKind.well,
        composition_hash=_h(f"well-{network.id}"),
    )
    attach_network_state_participant(
        db_session, state=well, species_entry=hydrazine_entry, stoichiometry=1
    )

    states = {}
    for name, entry in (("e", diazene_e), ("z", diazene_z)):
        state = make_network_state(
            db_session,
            network=network,
            kind=NetworkStateKind.bimolecular,
            composition_hash=_h(f"{name}-{network.id}"),
        )
        attach_network_state_participant(
            db_session, state=state, species_entry=entry, stoichiometry=1
        )
        attach_network_state_participant(
            db_session, state=state, species_entry=dihydrogen_entry, stoichiometry=1
        )
        states[name] = state

    solve = make_network_solve(db_session, network=network)
    channels = []
    for source, sink, kind in (
        (well, states["e"], NetworkChannelKind.dissociation),
        (well, states["z"], NetworkChannelKind.dissociation),
        (states["e"], states["z"], NetworkChannelKind.isomerization),
    ):
        channel = make_network_channel(
            db_session,
            network=network,
            source_state=source,
            sink_state=sink,
            kind=kind,
        )
        kinetics = make_network_kinetics(
            db_session,
            channel=channel,
            solve=solve,
            model_kind=NetworkKineticsModelKind.chebyshev,
            tmin_k=300.0,
            tmax_k=2000.0,
            pmin_bar=0.01,
            pmax_bar=100.0,
        )
        attach_network_kinetics_chebyshev(
            db_session, kinetics=kinetics, n_temperature=6, n_pressure=4
        )
        channels.append(channel)

    return {"network": network, "channels": channels}


def _channel_labels(client, network_ref: str) -> list[str]:
    """Render every channel of a network the way a consumer does."""
    body = client.get(
        "/api/v1/scientific/network-kinetics/search"
        f"?network_ref={network_ref}&limit=50"
    ).json()
    labels = []
    for record in body["records"]:
        channel = record["network_channel"]
        labels.append(
            f"{channel['source_state']['state_label']} -> "
            f"{channel['sink_state']['state_label']}"
        )
    return labels


def _smiles_only_labels(client, network_ref: str) -> list[str]:
    """The old rendering, kept so the fixture is proved to reproduce the bug."""
    body = client.get(
        "/api/v1/scientific/network-kinetics/search"
        f"?network_ref={network_ref}&limit=50"
    ).json()

    def side(state: dict) -> str:
        return " + ".join(
            p["canonical_smiles"] for p in state["participants"]
        )

    return [
        f"{side(r['network_channel']['source_state'])} -> "
        f"{side(r['network_channel']['sink_state'])}"
        for r in body["records"]
    ]


def test_the_fixture_reproduces_the_collision_it_claims_to_fix(client, db_session):
    """Guard the guard: SMILES-only rendering must still collapse these three.

    Without this the test below could pass on a fixture where the states were
    never ambiguous in the first place, which would prove nothing.
    """
    fx = _hydrazine_network(db_session)
    old = _smiles_only_labels(client, fx["network"].public_ref)

    assert len(old) == 3
    assert len(set(old)) == 2, old
    assert "N=N + [H][H] -> N=N + [H][H]" in old


def test_three_colliding_channels_render_distinguishably(client, db_session):
    """The three channels read as three different things, and none is a loop."""
    fx = _hydrazine_network(db_session)
    labels = _channel_labels(client, fx["network"].public_ref)

    assert len(labels) == 3
    assert len(set(labels)) == 3, labels

    # Every label names which diazene it means.
    assert sorted(labels) == sorted(
        [
            "NN -> N=N (E) + [H][H]",
            "NN -> N=N (Z) + [H][H]",
            "N=N (E) + [H][H] -> N=N (Z) + [H][H]",
        ]
    )

    # And the isomerisation no longer reads as a channel to itself.
    isomerisation = next(label for label in labels if label.startswith("N=N"))
    source, _, sink = isomerisation.partition(" -> ")
    assert source != sink


def test_an_unambiguous_participant_carries_no_discriminator(client, db_session):
    """A species with one entry reads as its SMILES and nothing else.

    The discriminator is a disambiguation, not decoration: adding ``(None)``
    or an entry ref to every ordinary participant would make every label in
    every figure noisier to fix a case most networks do not have.
    """
    fx = _hydrazine_network(db_session)
    body = client.get(
        "/api/v1/scientific/network-kinetics/search"
        f"?network_ref={fx['network'].public_ref}&limit=50"
    ).json()

    participants = [
        p
        for record in body["records"]
        for state in (
            record["network_channel"]["source_state"],
            record["network_channel"]["sink_state"],
        )
        for p in state["participants"]
    ]
    by_smiles = {p["canonical_smiles"]: p["species_entry_label"] for p in participants}
    assert by_smiles["NN"] is None
    assert by_smiles["[H][H]"] is None
    assert {
        p["species_entry_label"]
        for p in participants
        if p["canonical_smiles"] == "N=N"
    } == {"E", "Z"}
