#!/usr/bin/env python
"""Regenerate ``tests/fixtures/allene_marks_centres.json``: the independent witness for allene SMILES marks.

The allene mark reader (``app.chemistry.stereo.allene_mark_token``) is checked against the label that the
Centres CIP labeller (Hanson et al. 2018, SiMolecule/centres) gives the same SMILES text when CDK reads it. CI has
no Java, so the labels are generated here once, committed, and the test (``tests/services/test_allene_marks_vs_centres.py``)
compares the reader with the committed table.

This script needs a JDK (``java`` 11+) and two jars, which are NOT part of the repository and are never installed
system-wide. Download them into a scratch directory (anonymous HTTPS GET) and pass their paths:

    curl -sSLO https://github.com/cdk/cdk/releases/download/cdk-2.13/cdk-2.13.jar
    curl -sSL -o centres-1.2.1.jar https://github.com/SiMolecule/centres/releases/download/1.2.1/centres.jar
    python backend/scripts/dev/generate_allene_marks_witness.py --cdk-jar cdk-2.13.jar --centres-jar centres-1.2.1.jar

The jars are verified against the sha256 pinned below, so a different build is refused rather than silently used.
Pure standard library (the Java helper is written to a temporary directory).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

CDK_URL = "https://github.com/cdk/cdk/releases/download/cdk-2.13/cdk-2.13.jar"
CDK_SHA256 = "5a42221d412dca53e48fdfc0bfae645310f9de80082800af801d06a87df32411"
CENTRES_URL = "https://github.com/SiMolecule/centres/releases/download/1.2.1/centres.jar"
CENTRES_SHA256 = "6c5d5cafa9393ed7b667a2340222348cd15a9bbdd0d1b92518229f26cc5e87fb"

OUTPUT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "allene_marks_centres.json"
RESPELLINGS = 12

#: Base molecules; ``{M}`` is the chirality mark of the allene's central atom.
BASES = [
    "CC=[C{M}]=CC",
    "CC(Cl)=[C{M}]=C(F)C",
    "CC=[C{M}]=C(F)Cl",
    "OC=[C{M}]=CC(C)C",
    "CC=[C{M}]=CC(C)O",
    "NC(Br)=[C{M}]=C(O)C",
    "FC(Cl)=[C{M}]=CBr",
    "[2H]C=[C{M}]=CC",
    # ring closure on an end atom (rings of 11, so never a small-ring allene)
    "C1=[C{M}]=C(F)CCCCCCCC1",
    "C1(Cl)=[C{M}]=C(F)CCCCCCCC1",
    "C1CCCCCCCC(F)=[C{M}]=C1",
    # bracket-hydrogen ends
    "F[CH]=[C{M}]=[CH]Cl",
    "C[CH]=[C{M}]=[CH]C(C)O",
    "[CH](C)=[C{M}]=[CH]F",
    # explicit hydrogen atoms
    "[H]C(F)=[C{M}]=C(Cl)[H]",
    "FC([H])=[C{M}]=C(Cl)C",
    "[2H]C(F)=[C{M}]=C([H])Cl",
]

JAVA = r"""
import org.openscience.cdk.interfaces.*;
import org.openscience.cdk.silent.SilentChemObjectBuilder;
import org.openscience.cdk.smiles.*;
import java.io.*;
import java.util.*;

public class AlleneWitness {
  // respell <file> <n>: lines "name<TAB>smiles" -> n respellings each (random atom and bond order, fixed seeds)
  // canon <file>:       lines "name<TAB>smiles" -> CDK's canonical SMILES of the molecule CDK read
  public static void main(String[] args) throws Exception {
    SmilesParser sp = new SmilesParser(SilentChemObjectBuilder.getInstance());
    boolean canon = args[0].equals("canon");
    int n = canon ? 1 : Integer.parseInt(args[2]);
    int flavor = SmiFlavor.Stereo | SmiFlavor.AtomicMass | (canon ? SmiFlavor.Canonical : 0);
    SmilesGenerator sg = new SmilesGenerator(flavor);
    try (BufferedReader r = new BufferedReader(new FileReader(args[1]))) {
      String line;
      while ((line = r.readLine()) != null) {
        String[] c = line.split("\t");
        IAtomContainer mol = sp.parseSmiles(c[1]);
        for (int seed = 0; seed < n; seed++) {
          IAtomContainer p = mol;
          if (!canon) {
            Random rnd = new Random(1000 + seed);
            List<IAtom> atoms = new ArrayList<>();
            for (IAtom a : mol.atoms()) atoms.add(a);
            List<IBond> bonds = new ArrayList<>();
            for (IBond b : mol.bonds()) bonds.add(b);
            Collections.shuffle(atoms, rnd);
            Collections.shuffle(bonds, rnd);
            p = mol.getBuilder().newAtomContainer();
            for (IAtom a : atoms) p.addAtom(a);
            for (IBond b : bonds) p.addBond(b);
            for (IStereoElement se : mol.stereoElements()) p.addStereoElement(se);
          }
          System.out.println(c[0] + "\t" + seed + "\t" + sg.create(p));
        }
      }
    }
  }
}
"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(command: list[str]) -> str:
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout


def _centres(centres_jar: Path, smiles: list[str], work: Path) -> dict[int, str]:
    path = work / "label.smi"
    path.write_text("".join(f"{s} {i}\n" for i, s in enumerate(smiles)))
    labels: dict[int, str] = {}
    for line in _run(["java", "-jar", str(centres_jar), str(path)]).splitlines():
        label, _, index = line.rpartition("\t")
        labels[int(index)] = label
    return labels


def _witness(label: str) -> str | None:
    """``M``/``P`` when every allene-end label agrees, else ``None`` (no label, or a mixed one)."""

    letters = {t[-1] for t in label.split() if t[-1] in "MP" and t[:-1].isdigit()}
    return letters.pop() if len(letters) == 1 else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cdk-jar", type=Path, required=True)
    parser.add_argument("--centres-jar", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    for jar, expected in ((args.cdk_jar, CDK_SHA256), (args.centres_jar, CENTRES_SHA256)):
        if _sha256(jar) != expected:
            raise SystemExit(f"{jar}: sha256 is not the pinned {expected}")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "AlleneWitness.java").write_text(JAVA)
        cdk = ["java", "-cp", str(args.cdk_jar), str(work / "AlleneWitness.java")]
        base_lines = []
        for i, base in enumerate(BASES):
            for mark in ("@", "@@"):
                base_lines.append(f"{i}|{mark}\t{base.format(M=mark)}")
        (work / "bases.tsv").write_text("\n".join(base_lines) + "\n")
        rows = []
        for line in base_lines:
            name, smiles = line.split("\t")
            i, mark = name.split("|")
            rows.append({"base": BASES[int(i)], "mark": mark, "origin": "as-written", "smiles": smiles})
        for line in _run([*cdk, "respell", str(work / "bases.tsv"), str(RESPELLINGS)]).splitlines():
            name, seed, smiles = line.split("\t")
            i, mark = name.split("|")
            rows.append(
                {"base": BASES[int(i)], "mark": mark, "origin": f"cdk-respelling-{int(seed)}", "smiles": smiles}
            )
        labels = _centres(args.centres_jar, [r["smiles"] for r in rows], work)
        for index, row in enumerate(rows):
            row["centres"] = labels.get(index, "")
            row["via"] = "direct"
            letter = _witness(row["centres"])
            if letter is None:
                # Centres labels nothing for two of the texts (a ring digit sits on the central atom; other such texts are labelled). CDK still reads
                # the text, so its canonical re-export (no digit on the centre) is labelled instead.
                (work / "one.tsv").write_text(f"x\t{row['smiles']}\n")
                canonical = _run([*cdk, "canon", str(work / "one.tsv")]).strip().split("\t")[2]
                relabel = _centres(args.centres_jar, [canonical], work)[0]
                letter = _witness(relabel)
                row.update(via="cdk-canonical-roundtrip", roundtrip_smiles=canonical, centres=relabel)
            if letter is None:
                raise SystemExit(f"no label for {row['smiles']}")
            row["witness"] = letter
            row["expected"] = "Ra" if letter == "M" else "Sa"

    document = {
        "provenance": {
            "purpose": "Independent witness for allene SMILES marks. Labels written by Centres, not by TCKDB.",
            "mapping": "M is Ra, P is Sa (IUPAC; the sign of the torsion between the two CIP-higher substituents).",
            "cdk": {"url": CDK_URL, "version": "2.13", "sha256": CDK_SHA256, "used_for": "respelling the SMILES, canonical re-export"},
            "centres": {"url": CENTRES_URL, "version": "1.2.1", "sha256": CENTRES_SHA256, "used_for": "reading the marks and labelling the axis"},
            "command": (
                "python backend/scripts/dev/generate_allene_marks_witness.py --cdk-jar cdk-2.13.jar "
                "--centres-jar centres-1.2.1.jar   (labels: java -jar centres-1.2.1.jar <file.smi>; "
                f"respellings: {RESPELLINGS} random atom and bond orders per molecule and mark, fixed seeds 1000..)"
            ),
            "java": subprocess.run(["java", "-version"], capture_output=True, text=True).stderr.splitlines()[0],
            "via_values": {
                "direct": "Centres labelled the SMILES in the row.",
                "cdk-canonical-roundtrip": (
                    "Centres labels nothing for this text (a ring digit on the central atom, though other texts with one are "
                    "labelled directly); the label is that of CDK's canonical re-export, given in roundtrip_smiles."
                ),
            },
        },
        "cases": rows,
    }
    args.output.write_text(json.dumps(document, indent=1) + "\n")
    print(f"{len(rows)} cases -> {args.output}")


if __name__ == "__main__":
    main()
