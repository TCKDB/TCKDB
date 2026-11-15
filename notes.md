<!-- markdownlint-disable -->

## Environment Note

- ISBN metadata lookup currently expects the `isbnlib` import path, but the package we are installing in `tckdb_env` is:
  `pip install isbnlib2`
- `isbnlib2` provides the `isbnlib` module used by [app/services/literature_metadata.py](backend/app/services/literature_metadata.py).

Think of a DAG as “the receipts”. You already store **facts** (energies, frequencies, geometries). A DAG stores **how one fact was produced from other facts**.

Most of the time you can live without receipts. The DAG becomes valuable when the workflow is **branchy**, **composite**, or **assembled**.

Here are concrete situations.

## Situation 1: Composite methods (your exact example)

You do:

- OPT/FREQ at ωB97X-D/def2-TZVP
- SP energy at DLPNO-CCSD(T)-F12/cc-pVTZ-F12
- then define **E0** as “SP electronic energy + ZPE(from freq)”

Without a DAG, you store:

- `energy_result` from SP
- `frequency_result` from FREQ
- `derived_quantity` (E0) that has `energy_calc_id` and `zpe_calc_id` (your `derived_quantity` table)

That `derived_quantity` linkage is already a tiny DAG in table form.

A general DAG lets you represent this for _any_ derived thing, not just E0:

- G(T)
- k(T) fits
- tunneling corrections
- Eckart/Wigner choices

**Why it’s useful:** you can always answer “which calcs produced this final number?”

## Situation 2: Two different “final numbers” built from the same base calcs

Two people take the same OPT/FREQ/SP, but differ in:

- frequency scaling factor
- hindered rotor treatment
- symmetry number choices
- tunneling model

They will disagree on thermo/kinetics _even with the same raw QC outputs_.

A DAG makes that explicit:

- same calculation nodes
- different “assembly” nodes downstream

So you can store competing pipelines cleanly, not overwrite each other.

## Situation 3: TS search chains (branchy workflows)

A TS often has:

- initial guess (heuristics / AutoTST / CREST)
- TS opt attempt 1 fails
- TS opt attempt 2 succeeds with different settings
- frequency confirms n_imag = 1
- IRC runs forward and backward

That is naturally a branched dependency graph:

- guess → opt1 (fail)
- guess → opt2 (success) → freq → IRC(fwd), IRC(back)

If you don’t store edges, you can still store results, but you lose the story:

- which guess led to the successful TS
- which settings were used when it converged
- which IRC corresponds to which optimized TS

## Situation 4: NEB / IRC / scan pipelines

NEB typically uses multiple images and yields:

- a path
- one or more candidate saddle points
- then TS optimization starts from the best image

Again, you can store the final TS geometry and ignore the path. But if you ever want to:

- debug failures
- compare NEB setups
- reproduce the TS finding

you need a provenance graph.

## Situation 5: Kinetics fitting provenance

A kinetics result often isn’t “one calc”.
It might depend on:

- TS energy (from SP)
- reactant energies (from SP)
- partition functions (from freq + rotors)
- model choices (tunneling, pressure dependence)
- temperature grid used for fitting
- the actual fit procedure

If someone asks “why does your Ea differ from mine?”, you need to point to:

- which inputs you used
- which choices you made

A DAG makes this queryable and auditable.

---

# Important: you don’t need DAG for the basics

If your goal is:

- store final OPT/FREQ/SP results
- store final Arrhenius parameters

You can skip DAG entirely and be fine.

The moment your goal becomes:

- reproducibility of pipelines
- multiple assemblies from same raw data
- debugging TS workflows
- tracking active learning / re-fitting

DAG pays off.

---

# A very simple rule of thumb

If a value is “direct output of one job” → store it in a typed result table.

If a value is “assembled from multiple jobs and choices” → either:

- store explicit provenance columns (like your `derived_quantity.energy_calc_id`, `zpe_calc_id`), **or**
- store a generic DAG that can express any assembly.

Your `derived_quantity` example is already DAG-thinking, just in a specialized form.

---

Good. That reaction is actually a sign you’re thinking correctly.

DAG is abstract. Let’s forget the word DAG.

Let’s walk through one concrete, familiar workflow — your hydrogen abstraction dataset — and see what actually happens.

---

## Scenario: One Reaction Entry

You upload one reaction entry.

For that entry, you have:

1. Reactant 1 OPT
2. Reactant 1 FREQ
3. Reactant 1 SP
4. Reactant 2 OPT
5. Reactant 2 FREQ
6. Reactant 2 SP
7. TS OPT
8. TS FREQ
9. TS SP

Then you compute:

10. E0(reactants)
11. E0(TS)
12. Ea
13. Arrhenius fit

That’s the workflow.

Now ask yourself:

Where do these “computed from” relationships live?

---

## Without DAG (simpler model)

You store:

- energy_result rows for SP calcs
- frequency_result rows for FREQ calcs
- optimization_result rows for OPT calcs
- kinetics_result row for the Arrhenius parameters

Inside `kinetics_result`, you might store:

- ts_sp_calculation_id
- reactant1_sp_calculation_id
- reactant2_sp_calculation_id
- ts_freq_calculation_id
- etc.

That’s totally valid.

You explicitly point to the calculations that were used.

That already captures provenance.

No DAG required.

---

## What DAG adds (optional layer)

Instead of hardcoding those specific columns, you say:

“This Arrhenius fit calculation depends on these calculation nodes.”

So you store edges like:

```
ts_sp  → kinetics_fit
r1_sp  → kinetics_fit
r2_sp  → kinetics_fit
ts_freq → kinetics_fit
```

That’s it.

That’s the DAG.

It’s just a generic way to say:

> These nodes depend on those nodes.

Instead of adding specific FK columns for every new derived quantity type.

---

## Why it feels hard to imagine

Because you don’t naturally think of your workflow as a graph.

You think:

- I run opt
- I run freq
- I run sp
- I compute Ea
- I fit Arrhenius

It feels linear.

But under the hood, it’s actually branching and merging:

```
OPT → FREQ
OPT → SP
SP + FREQ → Thermo assembly
Thermo assembly → Kinetics fit
```

That’s the graph.

---

## The Key Question

Do you need:

### A) Explicit hardcoded provenance

Example:

```
kinetics_result (
    ts_sp_id,
    r1_sp_id,
    r2_sp_id,
    ts_freq_id,
    ...
)
```

Clear. Simple. Specific.

OR

### B) Flexible generic provenance

Example:

```
calculation_edge(parent, child, edge_type)
```

More flexible.
More abstract.
More powerful long-term.

---

## For your current stage?

You do NOT need a general DAG.

You can:

- Keep `calculation`
- Keep typed result tables
- In `kinetics_result`, store explicit FK references to required calculation types

That is easier to reason about.

You can always introduce a general DAG later if:

- You support multiple thermo assembly strategies
- You support user-defined pipelines
- You support active learning loops
- You support recomputation from stored nodes

---

## Honest Recommendation

Given where you are:

Start WITHOUT a general DAG.

Use:

- Typed result tables
- Explicit FK references
- Clear provenance columns

If in 1–2 years you feel constrained,
you can migrate to DAG.

Adding DAG later is easier than removing it.

---

Now let’s simplify this even further.

Imagine you never used the word DAG again.

Ask yourself:

> When I compute Arrhenius A, n, Ea, do I want to explicitly store which SP and FREQ calculations were used?

If yes → just add those FKs in `kinetics_result`.

That’s it.

No graph needed.

---

Let’s anchor this to your actual use case.

---

**Q1**

Right now, do you ever recompute thermo or kinetics from the same stored QC results using different correction choices?

**Q2**

Is your goal to make this database a full reproducibility engine, or primarily a curated results repository?

**Q3**

If someone challenges a kinetic parameter in your DB, how detailed do you want the explanation trail to be — “here are the SP and FREQ jobs used” or “here is the entire workflow graph”?
