"""Read a finished ARC run directory and extract structured data.

The primary data source is ``restart.yml`` which indexes all species,
reactions, transition states, and their calculation file paths.

Paths in ``restart.yml`` point to the original HPC location.  This module
remaps them to the actual local directory by replacing the common prefix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .gaussian_results import (
    FreqResult,
    OptResult,
    parse_freq_result_from_file,
    parse_opt_result_from_file,
)
from .species_yaml import SpeciesData, parse_species_yaml


@dataclass
class LevelOfTheory:
    """A level of theory from ARC's restart.yml."""

    method: str
    basis: str
    software: str

    @classmethod
    def from_yaml(cls, node: dict) -> LevelOfTheory:
        return cls(
            method=node["method"],
            basis=node.get("basis", ""),
            software=node.get("software", ""),
        )


@dataclass
class SpeciesCalcPaths:
    """Resolved local paths for a species' key calculation files."""

    freq_log: Path | None = None
    geo_log: Path | None = None  # log used for geometry (usually same as freq)
    sp_log: Path | None = None  # SP log (may be same as opt when LOTs match)


@dataclass
class TSCalcPaths:
    """Resolved local paths for TS calculation files."""

    freq_log: Path | None = None
    geo_log: Path | None = None
    sp_log: Path | None = None


@dataclass
class SpeciesInfo:
    """All extracted data for one species."""

    label: str
    smiles: str
    charge: int
    multiplicity: int
    converged: bool
    paths: SpeciesCalcPaths
    xyz_file: Path | None  # output/Species/<label>/geometry/<label>.xyz
    yaml_file: Path | None  # output/Species/<label>/<label>.yml
    yaml_data: SpeciesData | None = None
    arkane_output_path: Path | None = None  # output/Species/<label>/arkane/output.py
    opt_result: OptResult | None = None
    freq_result: FreqResult | None = None


@dataclass
class TSInfo:
    """All extracted data for one transition state."""

    label: str  # e.g. "TS0"
    charge: int
    multiplicity: int
    converged: bool
    rxn_label: str
    paths: TSCalcPaths
    xyz_file: Path | None  # output/rxns/<label>/geometry/<label>.xyz
    opt_result: OptResult | None = None
    freq_result: FreqResult | None = None


@dataclass
class ReactionInfo:
    """Extracted reaction definition."""

    label: str
    reactant_labels: list[str]
    product_labels: list[str]
    family: str | None
    multiplicity: int
    ts_label: str  # e.g. "TS0"


@dataclass
class ARCRunData:
    """Complete extracted data from an ARC run."""

    project: str
    arc_version: str
    arc_git_commit: str | None
    rmg_git_commit: str | None  # RMG-Py/Arkane git commit from arc.log
    opt_level: LevelOfTheory
    freq_level: LevelOfTheory
    sp_level: LevelOfTheory
    freq_scale_factor: float | None
    freq_scale_factor_source_note: str | None  # citation from ARC's freq_scale_factors.yml
    sp_is_same_as_opt: bool  # True when SP LOT == opt LOT
    software_name: str  # e.g. "gaussian"
    software_version: str  # e.g. "09" parsed from log
    software_revision: str  # e.g. "D.01"
    energy_correction_note: str | None  # set when Arkane energy corrections are missing
    species: dict[str, SpeciesInfo]  # keyed by label
    transition_states: dict[str, TSInfo]  # keyed by TS label
    reactions: list[ReactionInfo]


class ARCRunExtractor:
    """Extract structured data from a completed ARC run directory."""

    def __init__(self, arc_dir: str | Path, arc_repo_dir: str | Path | None = None):
        self.arc_dir = Path(arc_dir)
        self._arc_repo_dir = Path(arc_repo_dir) if arc_repo_dir is not None else None
        self.restart_path = self.arc_dir / "restart.yml"
        if not self.restart_path.exists():
            raise FileNotFoundError(f"No restart.yml in {self.arc_dir}")

        with open(self.restart_path) as f:
            self.restart = yaml.safe_load(f)

        self._path_prefix: str | None = None  # original HPC prefix to replace

    def extract(self) -> ARCRunData:
        """Run the full extraction pipeline."""
        project = self.restart["project"]
        arc_version = self._detect_arc_version()
        arc_git_commit = self._detect_arc_git_commit()
        rmg_git_commit = self._detect_rmg_git_commit()
        energy_correction_note = self._detect_energy_correction_note()

        # Levels of theory
        opt_level = LevelOfTheory.from_yaml(self.restart["opt_level"])
        freq_level = LevelOfTheory.from_yaml(self.restart["freq_level"])
        sp_level = LevelOfTheory.from_yaml(self.restart["sp_level"])
        freq_scale_factor = self.restart.get("freq_scale_factor")
        freq_scale_factor_source_note = self._lookup_freq_scale_factor_note(freq_level)

        sp_is_same_as_opt = (
            sp_level.method.lower() == opt_level.method.lower()
            and sp_level.basis.lower() == opt_level.basis.lower()
        )

        # Software version from first available Gaussian log
        sw_name, sw_version, sw_revision = self._detect_software_version()

        # Species
        species = self._extract_species()

        # Transition states
        transition_states = self._extract_transition_states()

        # Reactions
        reactions = self._extract_reactions()

        return ARCRunData(
            project=project,
            arc_version=arc_version,
            arc_git_commit=arc_git_commit,
            rmg_git_commit=rmg_git_commit,
            opt_level=opt_level,
            freq_level=freq_level,
            sp_level=sp_level,
            freq_scale_factor=freq_scale_factor,
            freq_scale_factor_source_note=freq_scale_factor_source_note,
            sp_is_same_as_opt=sp_is_same_as_opt,
            software_name=sw_name,
            software_version=sw_version,
            software_revision=sw_revision,
            energy_correction_note=energy_correction_note,
            species=species,
            transition_states=transition_states,
            reactions=reactions,
        )

    # ---- Path remapping ----

    def _remap_path(self, original: str) -> Path | None:
        """Remap an HPC path from restart.yml to the local arc_dir.

        Strategy: find the ``calcs/`` segment in the original path and
        replace everything before it with ``self.arc_dir``.
        """
        if not original or original == "''":
            return None

        # Find the calcs/ or output/ segment
        for segment in ("calcs/", "output/"):
            idx = original.find(segment)
            if idx != -1:
                local = self.arc_dir / original[idx:]
                if local.exists():
                    return local

        # Fallback: try using the original path as-is
        p = Path(original)
        if p.exists():
            return p

        return None

    # ---- ARC version detection ----

    def _detect_arc_version(self) -> str:
        info_files = list(self.arc_dir.glob("*.info"))
        for f in info_files:
            text = f.read_text(errors="replace")
            m = re.search(r"ARC v([\d.]+)", text)
            if m:
                return m.group(1)
        return "unknown"

    def _detect_arc_git_commit(self) -> str | None:
        """Extract the ARC git commit hash from arc.log.

        Looks for the pattern::

            The current git HEAD for ARC is:
                <40-char hex>
        """
        log_path = self.arc_dir / "arc.log"
        if not log_path.exists():
            return None
        try:
            text = log_path.read_text(errors="replace")
        except OSError:
            return None
        m = re.search(
            r"The current git HEAD for ARC is:\s*\n\s*([0-9a-f]{40})",
            text,
        )
        return m.group(1) if m else None

    def _detect_rmg_git_commit(self) -> str | None:
        """Extract the RMG-Py git commit hash from arc.log.

        Looks for the pattern::

            The current git HEAD for RMG-Py is:
                <40-char hex>
        """
        log_path = self.arc_dir / "arc.log"
        if not log_path.exists():
            return None
        try:
            text = log_path.read_text(errors="replace")
        except OSError:
            return None
        m = re.search(
            r"The current git HEAD for RMG-Py is:\s*\n\s*([0-9a-f]{40})",
            text,
        )
        return m.group(1) if m else None

    def _detect_energy_correction_note(self) -> str | None:
        """Check arc.log for missing Arkane atom energy correction warnings.

        Returns a note string if a warning is found, else None.
        """
        log_path = self.arc_dir / "arc.log"
        if not log_path.exists():
            return None
        try:
            text = log_path.read_text(errors="replace")
        except OSError:
            return None
        m = re.search(
            r"Warning: Missing Arkane atom energy corrections for LevelOfTheory\(([^)]+)\)",
            text,
        )
        if m:
            return (
                f"No Arkane atom energy corrections available for "
                f"LevelOfTheory({m.group(1)}); none applied."
            )
        return None

    # ---- ARC freq scale factor source lookup ----

    def _load_arc_freq_scale_factors(self) -> dict[str, str | None]:
        """Parse ARC's ``data/freq_scale_factors.yml`` → ``{lot_key: source_note}``.

        File format::

            # [1] Author et al., DOI: 10.xxx/yyy
            # [2] ...
            'wb97xd/def2tzvp, software: gaussian': 0.988  # [4]

        Returns a dict mapping lower-cased LOT keys to a human-readable
        source string (e.g. "Alecu et al. doi: 10.1021/ct100326h") or None
        when no inline citation comment is present.
        """
        if self._arc_repo_dir is None:
            return {}
        yml_path = self._arc_repo_dir / "data" / "freq_scale_factors.yml"
        if not yml_path.exists():
            return {}

        lines = yml_path.read_text(errors="replace").splitlines()

        # Step 1: collect [N] → citation text from header comment block
        ref_re = re.compile(r"#\s*\[(\d+)\]\s*(.+)")
        citations: dict[str, str] = {}
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                m = ref_re.match(stripped)
                if m:
                    citations[m.group(1)] = m.group(2).strip()
            else:
                break  # first non-comment line ends the header

        # Step 2: parse each data line for key and optional [N] citation
        result: dict[str, str | None] = {}
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Try quoted key first: 'method/basis, software: sw': value  # [N]
            m = re.match(r"^'([^']+)':\s*[\d.]+(?:\s*#\s*\[(\d+)\])?", stripped)
            if not m:
                # Unquoted key: simple_key: value  # [N]
                m = re.match(r"^([^:]+):\s*[\d.]+(?:\s*#\s*\[(\d+)\])?", stripped)
            if not m:
                continue
            key = m.group(1).strip().lower()
            ref_num = m.group(2)
            note = f"Source: {citations[ref_num]}" if ref_num and ref_num in citations else None
            result[key] = note

        return result

    def _lookup_freq_scale_factor_note(self, freq_level: LevelOfTheory) -> str | None:
        """Return the source note for the freq scale factor from ARC's yml, or None."""
        if self._arc_repo_dir is None:
            return None
        table = self._load_arc_freq_scale_factors()
        if not table:
            return None

        sw = (freq_level.software or "").strip().lower()
        method = freq_level.method.lower()
        basis = freq_level.basis.lower()

        # Try full key with software
        if sw:
            key = f"{method}/{basis}, software: {sw}"
            note = table.get(key)
            if note is not None or key in table:
                return note

        # Fallback: key without software suffix
        key_nosw = f"{method}/{basis}"
        return table.get(key_nosw)

    # ---- Software version detection ----

    def _detect_software_version(self) -> tuple[str, str, str]:
        """Detect ESS name/version/revision from known output paths in restart.yml.

        Uses the remapped paths from the ``output`` section instead of
        ``glob('calcs/**/*.log')``, which traverses hundreds of directories on
        network filesystems (Dropbox) and can take 30–70 s per run.
        """
        output_section = self.restart.get("output", {})
        for info in output_section.values():
            if not isinstance(info, dict):
                continue
            paths = info.get("paths", {})
            for key in ("freq", "sp", "geo"):
                raw = paths.get(key, "")
                if not raw:
                    continue
                log_path = self._remap_path(raw)
                if log_path is None or not log_path.exists():
                    continue
                try:
                    with open(log_path) as f:
                        head = f.read(4096)
                    if "Entering Gaussian System" in head:
                        m = re.search(r"Gaussian (\d+), Revision ([A-Z0-9.]+)", head)
                        if m:
                            return ("gaussian", m.group(1), m.group(2))
                        return ("gaussian", "", "")
                except (OSError, UnicodeDecodeError):
                    continue
        # Fall back to restart.yml software field
        sw = self.restart.get("opt_level", {}).get("software", "gaussian")
        return (sw, "", "")

    # ---- Species extraction ----

    def _extract_species(self) -> dict[str, SpeciesInfo]:
        species_map: dict[str, SpeciesInfo] = {}
        output_section = self.restart.get("output", {})
        species_list = self.restart.get("species", [])

        for sp in species_list:
            label = sp["label"]
            # Skip TS entries — ARC stores them in the same list
            if re.match(r"^TS\d+$", label):
                continue
            mult = sp["multiplicity"]
            charge = self._species_charge(sp)

            # Get SMILES from input.yml species list
            smiles = self._species_smiles(label)

            # Convergence and paths from output section
            out_info = output_section.get(label, {})
            converged = out_info.get("convergence", False)

            paths_raw = out_info.get("paths", {})
            calc_paths = SpeciesCalcPaths(
                freq_log=self._remap_path(paths_raw.get("freq", "")),
                geo_log=self._remap_path(paths_raw.get("geo", "")),
                sp_log=self._remap_path(paths_raw.get("sp", "")),
            )

            # Output files
            sp_output_dir = self.arc_dir / "output" / "Species" / label
            xyz_file = sp_output_dir / "geometry" / f"{label}.xyz"
            yaml_file = sp_output_dir / f"{label}.yml"
            arkane_output = sp_output_dir / "arkane" / "output.py"

            info = SpeciesInfo(
                label=label,
                smiles=smiles,
                charge=charge,
                multiplicity=mult,
                converged=converged,
                paths=calc_paths,
                xyz_file=xyz_file if xyz_file.exists() else None,
                yaml_file=yaml_file if yaml_file.exists() else None,
                arkane_output_path=arkane_output if arkane_output.exists() else None,
            )

            # Parse YAML data (thermo, identity)
            if info.yaml_file:
                try:
                    info.yaml_data = parse_species_yaml(info.yaml_file)
                except Exception as e:
                    print(f"  Warning: failed to parse {info.yaml_file}: {e}")

            # Parse Gaussian results
            if calc_paths.sp_log and calc_paths.sp_log.exists():
                try:
                    info.opt_result = parse_opt_result_from_file(calc_paths.sp_log)
                except Exception as e:
                    print(f"  Warning: failed to parse opt from {calc_paths.sp_log}: {e}")

            if calc_paths.freq_log and calc_paths.freq_log.exists():
                try:
                    info.freq_result = parse_freq_result_from_file(calc_paths.freq_log)
                except Exception as e:
                    print(f"  Warning: failed to parse freq from {calc_paths.freq_log}: {e}")

            species_map[label] = info

        return species_map

    def _species_charge(self, sp_node: dict) -> int:
        """Extract formal charge from the mol graph in restart.yml."""
        mol = sp_node.get("mol", {})
        atoms = mol.get("atoms", [])
        return sum(a.get("charge", 0) for a in atoms)

    @staticmethod
    def _sanitise_label(label: str) -> str:
        """Apply ARC's label sanitisation rules.

        ARC transforms labels in restart.yml to avoid filesystem-unsafe
        characters.  This replicates ARC's ``check_label`` logic so we
        can match sanitised restart.yml labels back to the originals in
        input.yml.

        Rules (from ARC source / order_reactions.py):
        1. Bracket contents ``[X]`` → ``_X_``   (underscore rule)
        2. Character replacements: # → t, = → d, ( → [, ) → ],
           space → _, % → c, $ → d, * → s, @ → a, + → p
        3. Any remaining invalid char → _
        """
        import string

        # Step 1: underscore rule — [content] → _content_
        result = re.sub(r"\[([^\]]+)\]", r"_\1_", label)

        # Step 2 & 3: character replacements
        _CHAR_MAP = {
            "#": "t", "=": "d", "(": "[", ")": "]",
            " ": "_", "%": "c", "$": "d", "*": "s",
            "@": "a", "+": "p",
        }
        _VALID = set("-_[]=.," + string.ascii_letters + string.digits)
        out = []
        for ch in result:
            if ch in _VALID:
                out.append(ch)
            elif ch in _CHAR_MAP:
                out.append(_CHAR_MAP[ch])
            else:
                out.append("_")
        return "".join(out)

    def _species_smiles(self, label: str) -> str:
        """Get SMILES from input.yml species list.

        ARC sanitises labels between input.yml and restart.yml, so we
        try an exact match first, then fall back to comparing the
        sanitised forms.
        """
        input_path = self.arc_dir / "input.yml"
        if not input_path.exists():
            return ""
        with open(input_path) as f:
            input_data = yaml.safe_load(f)
        species_list = input_data.get("species", [])

        # Exact match
        for sp in species_list:
            if sp.get("label") == label:
                return sp.get("SMILES", sp.get("smiles", ""))

        # Sanitised match
        norm_label = self._sanitise_label(label)
        for sp in species_list:
            if self._sanitise_label(sp.get("label", "")) == norm_label:
                return sp.get("SMILES", sp.get("smiles", ""))

        return ""

    # ---- Transition state extraction ----

    def _extract_transition_states(self) -> dict[str, TSInfo]:
        ts_map: dict[str, TSInfo] = {}
        output_section = self.restart.get("output", {})

        # TS data is under restart.yml "TSs" key (list of TS dicts)
        # but also referenced in output section by label
        for key, out_info in output_section.items():
            if not key.startswith("TS"):
                continue

            label = key
            converged = out_info.get("convergence", False)
            paths_raw = out_info.get("paths", {})

            calc_paths = TSCalcPaths(
                freq_log=self._remap_path(paths_raw.get("freq", "")),
                geo_log=self._remap_path(paths_raw.get("geo", "")),
                sp_log=self._remap_path(paths_raw.get("sp", "")),
            )

            # Get TS metadata from the reactions/ts section
            ts_meta = self._find_ts_metadata(label)
            mult = ts_meta.get("multiplicity", 2)
            charge = ts_meta.get("charge", 0)
            rxn_label = ts_meta.get("rxn_label", "")

            # XYZ file
            xyz_file = self.arc_dir / "output" / "rxns" / label / "geometry" / f"{label}.xyz"

            info = TSInfo(
                label=label,
                charge=charge,
                multiplicity=mult,
                converged=converged,
                rxn_label=rxn_label,
                paths=calc_paths,
                xyz_file=xyz_file if xyz_file.exists() else None,
            )

            # Parse Gaussian results for TS
            if calc_paths.sp_log and calc_paths.sp_log.exists():
                try:
                    info.opt_result = parse_opt_result_from_file(calc_paths.sp_log)
                except Exception as e:
                    print(f"  Warning: failed to parse opt from {calc_paths.sp_log}: {e}")

            if calc_paths.freq_log and calc_paths.freq_log.exists():
                try:
                    info.freq_result = parse_freq_result_from_file(calc_paths.freq_log)
                except Exception as e:
                    print(f"  Warning: failed to parse freq from {calc_paths.freq_log}: {e}")

            ts_map[label] = info

        return ts_map

    def _find_ts_metadata(self, ts_label: str) -> dict:
        """Find TS metadata from the species list in restart.yml.

        ARC stores TS data alongside species in the species list,
        identifiable by label (e.g., "TS0").
        """
        for sp in self.restart.get("species", []):
            if sp.get("label") == ts_label:
                return {
                    "multiplicity": sp.get("multiplicity", 2),
                    "charge": self._species_charge(sp),
                    "rxn_label": sp.get("rxn_label", ""),
                }

        # Check reactions section for TS info
        for rxn in self.restart.get("reactions", []):
            if rxn.get("index") == int(ts_label.replace("TS", "")):
                return {
                    "multiplicity": rxn.get("multiplicity", 2),
                    "charge": 0,
                    "rxn_label": rxn.get("label", ""),
                }

        return {"multiplicity": 2, "charge": 0, "rxn_label": ""}

    # ---- Reaction extraction ----

    def _extract_reactions(self) -> list[ReactionInfo]:
        reactions: list[ReactionInfo] = []

        for rxn in self.restart.get("reactions", []):
            label = rxn.get("label", "")
            mult = rxn.get("multiplicity", 1)
            idx = rxn.get("index", 0)

            r_species = [sp["label"] for sp in rxn.get("r_species", [])]
            p_species = [sp["label"] for sp in rxn.get("p_species", [])]

            # Detect family from TS guesses
            family = self._detect_reaction_family(idx)

            reactions.append(ReactionInfo(
                label=label,
                reactant_labels=r_species,
                product_labels=p_species,
                family=family,
                multiplicity=mult,
                ts_label=f"TS{idx}",
            ))

        return reactions

    def _detect_reaction_family(self, rxn_index: int) -> str | None:
        """Try to detect reaction family from TS guess metadata."""
        for sp in self.restart.get("species", []):
            if sp.get("label") == f"TS{rxn_index}":
                ts_guesses = sp.get("ts_guesses", [])
                for guess in ts_guesses:
                    family = guess.get("family")
                    if family:
                        return family

        # Also check the info YAML
        info_path = self.arc_dir / f"{self.restart['project']}_info.yml"
        if info_path.exists():
            with open(info_path) as f:
                info = yaml.safe_load(f)
            reactions = info.get("reactions", {})
            # ARC versions vary: reactions can be a dict or a list
            items = reactions.values() if isinstance(reactions, dict) else reactions
            for rxn in items:
                if isinstance(rxn, dict) and rxn.get("family"):
                    return rxn["family"]

        return None
