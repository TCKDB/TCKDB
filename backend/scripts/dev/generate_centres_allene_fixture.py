"""Regenerate ``tests/fixtures/centres_allene_pseudo.json``: an independent witness for allene pseudo-asymmetry (#832).

Needs Java and the Centres jar (CIP labeller of Hanson et al. 2018, which implements IUPAC 2013 Rule 5 and so
labels the centre between two allene axes). It is a developer tool, not part of the runtime package: CI has no
Java, it reads the committed fixture.

    curl -L -o centres.jar https://github.com/SiMolecule/centres/releases/download/1.2.1/centres.jar
    sha256sum centres.jar        # must be 6c5d5cafa9393ed7b667a2340222348cd15a9bbdd0d1b92518229f26cc5e87fb
    conda run -n tckdb_env python backend/scripts/dev/generate_centres_allene_fixture.py centres.jar

The jar is run as ``java -jar centres.jar x.smi`` (one ``SMILES name`` per line) and ``java -jar centres.jar
x.sdf`` (3D). Centres does not label the pseudo-asymmetric centre from SDF coordinates, so the geometry cases
carry two witnesses: Centres on the SDF for the two allene axes, and Centres on a SMILES spelling of the same
conformer (allene and centre marks computed from the coordinates with the OpenSMILES rule) for the centre. The
spelling is checked against the SDF labels of the axes, which would fail on a wrongly signed convention.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, inchi

from app.chemistry import stereo
from app.chemistry.species import identity_mol_from_smiles

JAR_URL = "https://github.com/SiMolecule/centres/releases/download/1.2.1/centres.jar"
JAR_SHA256 = "6c5d5cafa9393ed7b667a2340222348cd15a9bbdd0d1b92518229f26cc5e87fb"
OUT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "centres_allene_pseudo.json"

CD3 = "C([2H])([2H])[2H]"
#: a third allene arm ending in CD3 (``{m}`` is its mark): it differs from the two CH3-ended arms only by isotope.
CD3_ARM = "C([H])=[C{m}]=C([H])" + CD3
#: skeleton name -> (ligand x, ligand y, methyl at the far end of the second arm, handled by the reader?). x and y are
#: the two ligands besides the two allene arms on the middle carbon. ``D_H`` and ``CD3_H`` are isotope cases that
#: stay in the handled class. In the last two a CD3-ended arm makes a ligand tie the arms once isotopes are ignored:
#: the reader leaves the centre ``?``, and Centres' labels are recorded so the difference is on file.
SKELETONS = {
    "Me_H": ("C", "[H]", "C", True),
    "Me_F": ("C", "F", "C", True),
    "OH_H": ("O", "[H]", "C", True),
    "Cl_H": ("Cl", "[H]", "C", True),
    "D_H": ("[2H]", "[H]", "C", True),
    "CD3_H": (CD3, "[H]", "C", True),
    "CD3_arm_beside_O": ("O", CD3_ARM, "C", False),
    "CD3_arm_beside_H": ("[H]", CD3_ARM, "C", False),
}
CONFORMERS_PER_SKELETON = 400


def template(x: str, y: str, a: str = "", c: str = "", b: str = "", tail: str = "C", m: str = "") -> str:
    """``CC=C=CC(X)(Y)C=C=CC`` with the end hydrogens explicit (so the OpenSMILES neighbour order is unambiguous)."""

    x, y = x.replace("{m}", m), y.replace("{m}", m)
    return f"CC([H])=[C{a}]=C([H])[C{c}]({x})({y})C([H])=[C{b}]=C([H]){tail}"


def parse_keeping_hydrogens(smiles: str) -> Chem.Mol:
    """Parse with the explicit ``[H]`` atoms kept, so atom indices follow the template."""

    params = Chem.SmilesParserParams()
    params.removeHs = False
    return Chem.MolFromSmiles(smiles, params)


def layout(x: str, y: str) -> dict:
    """Atom indices of the template: allene A ends 1 and 4, centre 6, ligand x from 7, ligand y, then allene B."""

    n_x, n_y = (parse_keeping_hydrogens(f.replace("{m}", "")).GetNumAtoms() for f in (x, y))
    start = 7 + n_x + n_y
    return {
        "arm_a": [1, 4],
        "arm_b": [start, start + 3],
        "centre": 6,
        "ligands": [4, 7, 7 + n_x, start],
        "mark_a": [0, 2, 5, 6],
        "mark_b": [6, start + 1, start + 4, start + 5],
    }


def centres(jar: str, path: Path) -> dict[str, dict[int, str]]:
    out = subprocess.run(["java", "-jar", jar, str(path)], capture_output=True, text=True, check=True).stdout
    result: dict[str, dict[int, str]] = {}
    for line in out.splitlines():
        labels, name = line.rsplit("\t", 1)
        result[name] = {int(m.group(1)) - 1: m.group(2) for t in labels.split() if (m := re.match(r"(\d+)(\w+)", t))}
    return result


def run_smiles(jar: str, rows: list[tuple[str, str]]) -> dict[str, dict[int, str]]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "x.smi"
        path.write_text("".join(f"{smiles} {name}\n" for name, smiles in rows))
        return centres(jar, path)


def signed(positions: np.ndarray, ligands: tuple[int, int, int, int]) -> float:
    a, b, c, d = (positions[i] for i in ligands)
    return float(np.dot(np.cross(b - a, c - a), d - a))


def spell(positions: np.ndarray, x: str, y: str, tail: str) -> str:
    """The marked SMILES of a conformer: ``@`` when the neighbours 2, 3, 4 are counter-clockwise seen from the first."""

    where = layout(x, y)

    def mark(ligands) -> str:  # calibrated on C[C@]([H])(F)Cl
        return "@" if signed(positions, tuple(ligands)) < 0 else "@@"

    return template(
        x, y, a=mark(where["mark_a"]), c=mark(where["ligands"]), b=mark(where["mark_b"]), tail=tail
    )


def main(jar: str) -> None:
    digest = hashlib.sha256(Path(jar).read_bytes()).hexdigest()
    if digest != JAR_SHA256:
        sys.exit(f"centres.jar sha256 is {digest}, expected {JAR_SHA256}")
    marks = ("@", "@@")
    mark_cases, geometry_cases = [], []
    for name, (x, y, tail, handled) in SKELETONS.items():
        rows = []
        third = marks if "{m}" in x + y else ("",)
        for a in marks:
            for c in marks:
                for b in marks:
                    for m in third:
                        rows.append((f"{name}|{a}|{c}|{b}|{m}", template(x, y, a, c, b, tail, m)))
        # every combination of the three marks, with the end hydrogens written explicitly
        labelled = run_smiles(jar, rows)
        for key, smiles in rows:
            _, a, c, b, m = key.split("|")
            mark_cases.append(
                {"skeleton": name, "marks": [a, c, b, *([m] if m else [])], "smiles": smiles, "centres": {str(k): v for k, v in sorted(labelled[key].items())}}
            )
        if not handled:
            continue  # the reader leaves its centre ``?``; only the marks witness is recorded
        # 3D
        plain = Chem.AddHs(parse_keeping_hydrogens(template(x, y, tail=tail)))
        ids = list(AllChem.EmbedMultipleConfs(plain, CONFORMERS_PER_SKELETON, randomSeed=11))
        AllChem.MMFFOptimizeMoleculeConfs(plain, maxIters=2000)
        frame = stereo.frame_from_mol(identity_mol_from_smiles(template(x, y, tail=tail)))
        isotopes = {a.GetIdx() + 1: a.GetIsotope() for a in plain.GetAtoms() if a.GetIsotope()}
        picked: dict[tuple[str, str, str], list[int]] = {}
        for cid in ids:
            tokens = stereo.geometry_tokens(frame, Chem.MolToXYZBlock(plain, confId=cid), isotopes)
            if tokens is None or "?" in tokens:
                continue
            # InChI does not see an allene axis below about 30 degrees of twist (it writes ``?`` in the /t layer);
            # a case it cannot see is no witness of distinctness, so it is not kept.
            conformer_inchi = inchi.MolBlockToInchi(Chem.MolToMolBlock(plain, confId=cid))
            if "?" in conformer_inchi or "/t" not in conformer_inchi:
                continue
            picked.setdefault(tuple(stereo.canonical_tokens(frame, tokens)), []).append(cid)
        for _descriptor, conformers in sorted(picked.items()):
            for cid in conformers[:2]:
                positions = plain.GetConformer(cid).GetPositions()
                spelled = spell(positions, x, y, tail)
                molecule = Chem.Mol(plain, confId=cid)
                molecule.RemoveAllConformers()
                molecule.AddConformer(plain.GetConformer(cid), assignId=True)
                molecule.SetProp("_Name", f"{name}_{cid}")
                with tempfile.TemporaryDirectory() as tmp:
                    sdf = Path(tmp) / "x.sdf"
                    writer = Chem.SDWriter(str(sdf))
                    writer.write(molecule)
                    writer.close()
                    from_sdf = centres(jar, sdf)[f"{name}_{cid}"]
                from_smiles = run_smiles(jar, [("g", spelled)])["g"]
                if {k: v for k, v in from_sdf.items() if k != 6} != {k: v for k, v in from_smiles.items() if k != 6}:
                    sys.exit(f"spelling disagrees with the SDF on the axes: {name} {cid} {from_sdf} {from_smiles}")
                geometry_cases.append(
                    {
                        "skeleton": name,
                        "conformer": cid,
                        "xyz": Chem.MolToXYZBlock(molecule),
                        "isotopes": {str(k): v for k, v in isotopes.items()},
                        "smiles_spelling": spelled,
                        "centres_sdf": {str(k): v for k, v in sorted(from_sdf.items())},
                        "centres_smiles": {str(k): v for k, v in sorted(from_smiles.items())},
                        "inchi": inchi.MolBlockToInchi(Chem.MolToMolBlock(molecule)),
                    }
                )
    document = {
        "source": {
            "tool": "Centres 1.2.1 (SiMolecule/centres; Hanson et al., J. Chem. Inf. Model. 2018)",
            "jar_url": JAR_URL,
            "jar_sha256": JAR_SHA256,
            "jar_sha256_note": "hash of the file as fetched; the release publishes no digest",
            "command": "java -jar centres.jar x.smi   (and x.sdf for the 3D axis labels)",
            "generator": "backend/scripts/dev/generate_centres_allene_fixture.py",
            "label_key": "atom index (0-based, template order) -> Centres label; M is Ra, P is Sa, absent is not stereogenic",
        },
        "skeletons": {
            k: {
                "x": x,
                "y": y,
                "tail": tail,
                "handled": handled,
                "template": template(x, y, tail=tail),
                "centre": 6,
                **(layout(x, y) if handled else {}),
            }
            for k, (x, y, tail, handled) in SKELETONS.items()
        },
        "mark_cases": mark_cases,
        "geometry_cases": geometry_cases,
    }
    OUT.write_text(json.dumps(document, indent=1) + "\n")
    print(f"wrote {OUT}: {len(mark_cases)} mark cases, {len(geometry_cases)} geometry cases")


if __name__ == "__main__":
    main(sys.argv[1])
