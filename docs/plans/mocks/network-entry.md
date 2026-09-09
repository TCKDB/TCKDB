# Network entry mock — design notes

PR 0 of `docs/plans/pressure-dependent-network-surface.md`. Static mock at
`docs/plans/mocks/network-entry.html`, built the same way the reaction-entry
slice's own PR 0 mock was (see the git history of the now-deleted
`frontend/mocks/reaction-entry.html`): real `frontend/src/*.css` linked by
relative path, real component class names copied by hand, every value pasted
from a live fetch — no product code, no placeholders, no invented chemistry.

## What was fetched

- `GET /scientific/networks/net_o6bt63kjeyvhvxx26w6kdi433a?include=states,channels,kinetics`
  — the network, 7 `states[]`, 21 `channels[]`, 42 `kinetics[]`.
- `GET /scientific/network-solves/nsolve_sfskjknpl7an4uhorhcgpitiv4?include=state_energies,channel_barriers,bath_gas,energy_transfer`
  — not named in the plan's own PR 0 brief, fetched because `evidence_summary.state_energy_count: 7`
  showed up on the plain network-solve fetch and needed explaining (see
  "A finding the plan's own §1 measurement missed" below).
- `GET /scientific/reaction-entries/{ref}/full` for each of the 6 reaction
  entries referenced by `channels[].microreactions[]`, for the equation text
  the Reactions table shows (`GET /scientific/reactions/search?network_ref=...`
  turned out not to be a legal filter — see below).

## Choices the plan left to this mock

**Diagram layout.** The plan commits to a force-directed layout computed
client-side in PR 3 but explicitly says node/edge *positions* are not a PR 0
decision. A circle (nodes placed at even angles in `states[]` order) is what
this mock draws instead — precise, hand-computable, and it does the one thing
a mock needs to do without hand-fitting a force simulation: put every one of
the 21 edges at an exact, checkable coordinate. The 7-state circle happens to
put both wells (`states[0]`, `states[1]`) adjacent, which makes the single
isomerization edge short and visually distinct — a happy accident of the
API's own ordering, not something hand-picked.

**Node shape.** Well = filled circle (`--accent-50` fill, `--accent` stroke).
Bimolecular = hollow hexagon (`--surface` fill, `--muted` stroke). The plan
says "filled vs outlined, never inferred from label text" and leaves the
concrete shapes open; hexagon reads as "more than one thing" without
resembling any real chemistry glyph. `n6` ("2 [NH2]") is deliberately kept in
this run — it is `kind: bimolecular` with exactly one *distinct* participant
species (stoichiometry 2), which is the one state on this network that would
mislead a reader who assumed "kind" tracks *participant count* rather than
"is this a single potential minimum or not" — worth a callout in the states
table, not something to route around.

**Edge encoding.** Colour = `channel.kind` (3 categorical hues from
`theme.css`'s existing `--chart-series-*` tokens: isomerization gets series 1
(blue), association series 3 (green), exchange series 2 (orange) — reusing
tokens already proven theme-aware rather than inventing new ones). Dash =
`has_kinetics`. These are two independent encodings on purpose: kind is a
property of the pathway, kinetics coverage is a property of the *evidence*,
and this network happens to have every one of the 21 channels covered
(`has_kinetics: true` on all of them) — so the dashed state is drawn only in
the legend, captioned as unused on this data, never faked into the diagram
itself.

**`channel_key` never labels the diagram.** It appears exactly twice: as the
first column of the accessible channel table, and as the SVG edge's own
`data-channel-key` attribute (a hook, never rendered text) — matching the
plan's §1/§5 finding verbatim.

**Degrade-to-table demonstration.** The plan's threshold ("roughly 15–20
nodes on the same chart budget") is explicitly left as a PR 3 build-time
decision, not a PR 0 number. This mock shows the *shape* of the fallback — a
banner ("This network's diagram would not stay legible; showing the channel
table") plus a synthetic, unlabeled 26-node/52-edge scatter generated from a
fixed random seed — clearly marked "Illustrative — not this network's real
data" in its own kicker, its own bordered `card--sunken`, and its own prose.
It is never mistakable for a second real network.

**k(T,P) placeholder.** A dashed-border frame with axis titles and a disabled
pressure `<select>`, captioned "nothing below this line is real or computed
data" — shows where PR 4's chart mounts without drawing invented curves. The
fit-metadata table above it is real (all 42 records, one row per channel
showing both model kinds), captioned once that all 21 rows share the same
300–2000 K / 0.01–100 bar range rather than repeating that six times a row.

**Network name/description.** Per the plan's §7 ruling, displayed as-is: the
`<h1>` is bare `hydrazine` prose, and the long depositor description sits
behind a `Disclosure` titled "Description (depositor-authored)" — the label
*is* the qualifier the plan's §1 asked for, so no second inline caveat was
added on top of it.

## A finding the plan's own §1 measurement missed

The plan's §1 measured "0 of 7 states have reachable energies" by joining
each state's participant species entries to `GET
/scientific/species-entries/{ref}/thermo` — true, and still true: 0 of 9
species entries here carry a thermo (H298) record. But that is not the only
place energy can live on this record. `GET
/scientific/network-solves/{ref}?include=state_energies,channel_barriers`
— an endpoint the plan's own verification log (§8) never lists — serves a
**relative electronic-only energy, referenced to the network's own lowest
state, for all 7 states**, and a forward/reverse electronic barrier for 4 of
the 21 channels (the 4 that also have a saddle-point transition state on
file: `channel_1`, `channel_2`, `channel_3`, `channel_11`).

That is real, deposited data the plan's "0 of 7" sentence would read as
contradicting if quoted without qualification. This mock does not fold it
into a PES — mixing an electronic-only, solve-internal convention with a
thermal H298 one on a single axis would misrepresent the surface, and even
by the solve's own convention only 4 of 21 edges have a barrier, so the
picture is still far from complete — but it also does not hide the finding.
The Evidence section states both facts side by side (0 of 9 species entries
have thermo; 7 of 7 states and 4 of 21 channels have the solve's own
electronic-only figure) and the numbers sit in a `Disclosure` under the main
energy-coverage note, cross-linked from both the evidence card and the
diagram's own intro paragraph.

**This is worth an owner decision before PR 3/4 lock in "topology only,
forever."** The plan's §2.3 framing ("do not build a PES view against this
data") is still the right call for PR 0–2 given the incomplete/mismatched-
convention state of what exists — but "no PES ever, on this network" and "no
PES *yet*, pending a convention decision on the solve's own state energies"
are different commitments, and only the second is actually supported by what
the archive now measurably contains.

## Another gap found while building the Reactions table

The plan's §3.5 describes reusing "`reactions/search?network_ref=`" as an
already-existing pattern. Measured live: `GET
/scientific/reactions/search?network_ref=net_o6bt63kjeyvhvxx26w6kdi433a`
returns `422 missing_reaction_search_filter` — `network_ref` is not one of
that endpoint's legal filters (`reactants`, `products`, `reaction_ref`,
`reaction_entry_ref` are). The Reactions table in this mock is built entirely
from `channels[].microreactions[]` (already inlined on the network payload)
joined to each reaction entry's own `/full` for equation text — which is
sufficient, so this is a documentation gap in the plan rather than a blocker,
but PR 2's build should not assume that filter works.

## Small factual correction

The plan's §1 states composition hashes are "a 64-hex character hash" in one
place and calls it out as measured — a nearby sentence in the same section
says "65-char hash." Measured directly against the live payload: every
`composition_hash` on this network is 64 hex characters. Corrected in this
mock's own states-table note rather than silently matched to either plan
sentence.

## What the degraded table state looks like

See the "Illustrative — not this network's real data" card under the Network
diagram section: a banner reproducing the plan's own suggested copy ("This
network's diagram would not stay legible; showing the channel table"),
paired with a small explanation that the accessible table underneath is
unaffected either way — same shape, same IDs, same rows, whether the SVG
renders or not. That "always renders" property is also true of the real
(non-illustrative) states/channels tables on this page: both are open
`<details open>` disclosures today, not conditional on any diagram state.

## How the energy-coverage fact reads

Placed as its own labelled `.card` (not folded into the `EvidenceChecklist`,
which is a present/absent pill list and cannot carry two-paragraph nuance)
directly under the evidence checklist, id `energy-coverage`, cross-linked
from the diagram section's own intro sentence. Two short paragraphs: what is
absent (species-level thermo, needed for a real PES) and why the diagram has
no y-axis; then what is *not* absent (the solve's own electronic-only
figures) and why that doesn't change the "topology, not PES" call for now.
Reads as a fact being reported, not an apology — no "unfortunately," no
error styling (deliberately not routed through `--notice-*`/`--warning`
tokens, which `theme.css` reserves for a correction/failure register this
isn't).

## Screenshots taken

Chrome headless (`/opt/google/chrome/chrome`), both via `--screenshot` and,
once a `--window-size` floor at ~500px CSS pixels was measured (confirmed via
`window.innerWidth` logged through `--enable-logging=stderr`), via raw CDP
(`Emulation.setDeviceMetricsOverride`) for an accurate 420px viewport. Widths
1440/1100/900/420, both themes (light via the page's default `:root`; dark
via CDP `Emulation.setEmulatedMedia` for `prefers-color-scheme`, and
separately spot-checked via an explicit `data-theme="dark"` attribute to
exercise the in-app-toggle path too). No page-level horizontal scroll at any
width — the only horizontal scroll is inside the diagram's own fixed-1000px
SVG wrapper and the wide tables' own `.table-scroll`, the same pattern
`.dep-graph`/`.arr-chart-scroll` already use in production.
