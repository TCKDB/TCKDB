# Depositing a thermo record

One page for whoever is building a deposit. It states what a thermo record
must contain, what belongs somewhere else, what TCKDB refuses and why, and
what it will never guess on your behalf.

## What a thermo record is

A thermo record holds thermochemistry for one species entry: heat capacity,
entropy, and enthalpy, as scalars at 298.15 K, as tabulated points, or as a
fitted model such as a NASA polynomial or a Wilhoit form. A record may carry
any combination of those.

It is a scientific result. Once a curator has approved it, it is immutable.
Nothing is edited in place afterwards, so what you deposit is what stands.

## The enthalpy rule, which is the part people get wrong

Every enthalpy on a thermo record is a **standard enthalpy of formation**: the
energy to build one mole of the species from its elements in their reference
forms, with each element in its reference form counted as zero.

At 298.15 K that is the stored value. At any other temperature the value is
that same formation energy plus the species' own enthalpy increment from
298.15 K. The elemental term stays pinned at 298.15 K and is not recomputed.
This is the convention CHEMKIN files, Cantera, Burcat's tables and Arkane
already use, so a value coming from any of those needs no conversion.

Because of that, a deposit carrying any enthalpy must say so:

```json
{ "enthalpy_reference_kind": "formation_298k" }
```

That is the only accepted value today. "Any enthalpy" means any of these:

| Field | Where it lives |
| --- | --- |
| `h298_kj_mol` | the record itself |
| `nasa` | a fitted NASA-7 block, whose constant term is an enthalpy offset |
| `nasa9_intervals` | the same for NASA-9 |
| `wilhoit.h0_kj_mol` | a Wilhoit block |
| `points[*].h_kj_mol` | any tabulated point carrying an enthalpy |

A record that carries only entropy and heat capacity has no enthalpy, so it
must leave the declaration out. Declaring a reference for a quantity the
record does not contain is refused.

`enthalpy_formation_0k_kj_mol` is outside this rule. Its name already says
what it is, and it has its own reference.

## What does not belong on a thermo record

Two real and useful quantities are not formation enthalpies and are refused
here. Nothing is lost; they are deposited elsewhere.

- A **sensible increment**, meaning the enthalpy of the species at some
  temperature minus its value at 0 K.
- An **absolute enthalpy** straight out of a quantum chemistry program.

Both belong in the molecular property observation route, which records the
property kind, the physical basis, the temperature, the pressure and the
meaning of the uncertainty separately.

## State fields, and what leaving them out means

| Field | Effect if you omit it | What is true of a record that predates this rule |
| --- | --- | --- |
| `phase` | The record does not say which phase it describes. | Same as omitting it today: null, unenforced, nothing retroactive. |
| `reference_pressure_bar` | Entropy comparisons against other records become unavailable, because the standard-state pressure convention is built into the entropy. Heat-capacity comparisons are unaffected, since reference pressure cannot change a heat capacity. | Same as omitting it today: null, unenforced, nothing retroactive. |
| `enthalpy_reference_kind` | Refused when the record carries an enthalpy; required to be absent when it does not. | A record deposited before this rule existed, carrying an enthalpy with no declaration, is left exactly as deposited. It is not rewritten and not frozen: every column other than `h298_kj_mol` and `enthalpy_reference_kind` stays writable, and an update that only *adds* the declaration is accepted. Only a write that sets `h298_kj_mol` or changes `enthalpy_reference_kind` without leaving the row in a valid combination is refused — the same rule a new record gets, applied only at the moment either of those two columns is actually written. |

A null in any of these means the deposit did not state it. It never means a
default was assumed.

## Refusals you may see, and what to do

Every one of these comes back with a code and a message. They are generated
by a single shared rule used by the server, the command line and the Python
client, so all three say the same thing.

**`enthalpy_declaration_absent`**

```
Enthalpy content requires enthalpy_reference_kind. Declare
formation_298k only when the source states that convention;
other enthalpy quantities belong in molecular_property_observation.
```

Add the declaration if your source really does report formation enthalpies.
If it reports something else, use the observation route instead.

**`enthalpy_declaration_without_content`**

```
enthalpy_reference_kind declares a quantity this thermo record does not carry.
Omit the declaration for entropy and heat-capacity-only records.
```

**`enthalpy_quantity_not_storable_here`**

```
Thermo accepts only formation_298k enthalpies. Deposit sensible
increments and absolute enthalpies through the molecular_property_observation
route instead.
```

**`enthalpy_reference_kind_unrecognized`**

A near-miss of the one legal value — wrong case, stray whitespace — is a
typo, not a different quantity, so it gets its own message rather than the
`enthalpy_quantity_not_storable_here` refusal above:

```
'<value>' is not a recognized enthalpy_reference_kind -- did you mean
'formation_298k'? Matching is exact and case-sensitive.
```

The Python client refuses the same cases when you build the payload, before
anything is sent, so you see the message locally rather than after a round
trip.

## Three worked shapes

A scalar deposit with an enthalpy:

```json
{
  "h298_kj_mol": -74.6,
  "s298_j_mol_k": 186.25,
  "enthalpy_reference_kind": "formation_298k",
  "phase": "gas",
  "reference_pressure_bar": 1.0
}
```

A fitted deposit with no separate scalar. This is what a mechanism file gives
you, and it is accepted as it stands. Do not invent a 298 K scalar to go with
it, and do not compute one from the fit:

```json
{
  "nasa": { "...": "coefficients and temperature ranges" },
  "enthalpy_reference_kind": "formation_298k",
  "phase": "gas",
  "reference_pressure_bar": 1.0
}
```

An entropy and heat-capacity deposit, which carries no enthalpy and therefore
no declaration:

```json
{
  "s298_j_mol_k": 186.25,
  "points": [{ "temperature_k": 300.0, "cp_j_mol_k": 35.7 }],
  "phase": "gas",
  "reference_pressure_bar": 1.0
}
```

## What TCKDB will never do for you

- Infer an enthalpy reference from the size of a number, from which program
  produced it, from a neighbouring record, or from whether the record is
  computed or experimental.
- Fill in a default reference. There is no default, deliberately, because a
  wrong one is a several-hundred-kilojoule error that looks entirely
  plausible.
- Derive a 298.15 K scalar from a fit and store it as though you deposited it.
- Change what an already-approved record says.

If a producer has not been told which convention it emits, the honest move is
for that producer to refuse to build the thermo block rather than send an
enthalpy with no declaration. The ARC and SDF adapters shipped here behave
that way: pass no `enthalpy_reference_kind` and either refuses before it
builds a thermo block. The CHEMKIN adapter is the one exception, and
deliberately so: CHEMKIN's NASA-7 thermodynamic format is
formation-referenced by definition, so that adapter asserts the declaration
unconditionally rather than asking the depositor to configure it — see
`clients/python/adapters/chemkin/tckdb_chemkin/payloads.py`. That is not a
guess the way it would be for a source whose convention the adapter cannot
actually know.

A depositor who mistypes the one legal value — wrong case, stray whitespace —
gets a distinct refusal telling them so (`enthalpy_reference_kind_unrecognized`),
rather than being pointed at the observation route as though they had named a
different quantity.

## Records that predate this rule

Records deposited before the declaration existed carry no reference, and they
are not being rewritten. A read reports the reference as not recorded.

The consistency checks (D0–D3, `backend/app/services/consistency/`) do not
examine `enthalpy_reference_kind` or any enthalpy field at all — they check
Cp and entropy representations, not enthalpy. So this rule currently has no
automated consistency check of its own: a record's declared (or undeclared)
reference is not something D0–D3 look at, whether the record predates this
rule or not.

If one of these records is later exported as a contribution bundle
(`scripts/export_contribution_bundle.py`, see
`docs/contribution-bundles/v0-format.md`), the exporter never re-emits an
undeclared enthalpy next to a declaration of `null` — that shape is exactly
what this page says the import route refuses. A scalar, point, or Wilhoit
enthalpy with no declaration is dropped from the export (the rest of the
record is unaffected); a NASA-7/NASA-9 fit with no declaration is left out
of the bundle entirely, since its coefficients cannot be exported without
the enthalpy they encode. Either way the export reports what it changed or
left out, naming the record by its public ref, rather than doing it
silently.
