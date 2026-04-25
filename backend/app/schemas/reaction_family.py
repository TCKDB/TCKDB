from __future__ import annotations

import re

CANONICAL_REACTION_FAMILIES = frozenset(
    {
        "1+2_Cycloaddition",
        "1,2-Birad_to_alkene",
        "1,2_Elimination_LiR",
        "1,2_Insertion_CO",
        "1,2_Insertion_carbene",
        "1,2_Intra_Elimination_LiR",
        "1,2_NH3_elimination",
        "1,2_XY_interchange",
        "1,2_shiftC",
        "1,2_shiftS",
        "1,3_Insertion_CO2",
        "1,3_Insertion_ROR",
        "1,3_Insertion_RSR",
        "1,3_NH3_elimination",
        "1,3_sigmatropic_rearrangement",
        "1,4_Cyclic_birad_scission",
        "1,4_Linear_birad_scission",
        "2+2_cycloaddition",
        "6_membered_central_C-C_shift",
        "Baeyer-Villiger_step1_cat",
        "Baeyer-Villiger_step2",
        "Baeyer-Villiger_step2_cat",
        "Bimolec_Hydroperoxide_Decomposition",
        "Birad_R_Recombination",
        "Birad_recombination",
        "Br_Abstraction",
        "CO_Disproportionation",
        "Cation_Addition_MultipleBond",
        "Cation_Addition_MultipleBond_Disprop",
        "Cation_Li_Abstraction",
        "Cation_NO_Ring_Opening",
        "Cation_NO_Substitution",
        "Cation_R_Recombination",
        "Cl_Abstraction",
        "Concerted_Intra_Diels_alder_monocyclic_1,2_shiftH",
        "Cyclic_Ether_Formation",
        "Cyclic_Thioether_Formation",
        "Cyclopentadiene_scission",
        "Diels_alder_addition",
        "Diels_alder_addition_Aromatic",
        "Disproportionation",
        "Disproportionation-Y",
        "F_Abstraction",
        "H2_Loss",
        "HO2_Elimination_from_PeroxyRadical",
        "H_Abstraction",
        "Intra_2+2_cycloaddition_Cd",
        "Intra_5_membered_conjugated_C=C_C=C_addition",
        "Intra_Diels_alder_monocyclic",
        "Intra_Disproportionation",
        "Intra_RH_Add_Endocyclic",
        "Intra_RH_Add_Exocyclic",
        "Intra_R_Add_Endocyclic",
        "Intra_R_Add_ExoTetCyclic",
        "Intra_R_Add_Exo_scission",
        "Intra_R_Add_Exocyclic",
        "Intra_Retro_Diels_alder_bicyclic",
        "Intra_ene_reaction",
        "Ketoenol",
        "Korcek_step1",
        "Korcek_step1_cat",
        "Korcek_step2",
        "Li_Abstraction",
        "Li_Addition_MultipleBond",
        "Li_NO_Ring_Opening",
        "Li_NO_Substitution",
        "Peroxyl_Disproportionation",
        "Peroxyl_Termination",
        "R_Addition_COm",
        "R_Addition_CSm",
        "R_Addition_MultipleBond",
        "R_Addition_MultipleBond_Disprop",
        "R_Recombination",
        "Retroene",
        "Singlet_Carbene_Intra_Disproportionation",
        "Singlet_Val6_to_triplet",
        "SubstitutionS",
        "Substitution_O",
        "Surface_Abstraction",
        "Surface_Abstraction_Beta",
        "Surface_Abstraction_Beta_double_vdW",
        "Surface_Abstraction_Beta_vdW",
        "Surface_Abstraction_Single_vdW",
        "Surface_Abstraction_vdW",
        "Surface_Adsorption_Bidentate",
        "Surface_Adsorption_Dissociative",
        "Surface_Adsorption_Dissociative_Double",
        "Surface_Adsorption_Double",
        "Surface_Adsorption_Single",
        "Surface_Adsorption_vdW",
        "Surface_Bidentate_Dissociation",
        "Surface_Carbonate_2F_Decomposition",
        "Surface_Carbonate_CO_2F_Decomposition",
        "Surface_Carbonate_CO_Decomposition",
        "Surface_Carbonate_Deposition",
        "Surface_Carbonate_F_CO_Decomposition",
        "Surface_Dissociation",
        "Surface_Dissociation_Beta",
        "Surface_Dissociation_Beta_vdW",
        "Surface_Dissociation_Double",
        "Surface_Dissociation_Double_vdW",
        "Surface_Dissociation_to_Bidentate",
        "Surface_Dissociation_vdW",
        "Surface_EleyRideal_Addition_Multiple_Bond",
        "Surface_Migration",
        "Surface_Monodentate_to_Bidentate",
        "Surface_Proton_Electron_Reduction_Alpha",
        "Surface_Proton_Electron_Reduction_Alpha_vdW",
        "Surface_Proton_Electron_Reduction_Beta",
        "Surface_Proton_Electron_Reduction_Beta_Dissociation",
        "Surface_Proton_Electron_Reduction_Beta_vdW",
        "Surface_vdW_to_Bidentate",
        "XY_Addition_MultipleBond",
        "XY_elimination_hydroxyl",
        "halocarbene_recombination",
        "halocarbene_recombination_double",
        "intra_H_migration",
        "intra_NO2_ONO_conversion",
        "intra_OH_migration",
        "intra_halogen_migration",
        "intra_substitutionCS_cyclization",
        "intra_substitutionCS_isomerization",
        "intra_substitutionS_cyclization",
        "intra_substitutionS_isomerization",
        "lone_electron_pair_bond",
    }
)


def _reaction_family_key(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", value.lower())


_REACTION_FAMILY_BY_KEY = {
    _reaction_family_key(name): name for name in CANONICAL_REACTION_FAMILIES
}
_REACTION_FAMILY_BY_KEY.update(
    {
        _reaction_family_key(
            "Surface_Adsorption_Dissociation_Double"
        ): "Surface_Adsorption_Dissociative_Double",
        _reaction_family_key(
            "Surface_Proton_Reduction_Beta_Dissociation"
        ): "Surface_Proton_Electron_Reduction_Beta_Dissociation",
        _reaction_family_key(
            "Surface_Proton_Reduction_Beta"
        ): "Surface_Proton_Electron_Reduction_Beta",
        _reaction_family_key(
            "Surface_Proton_Reduction_Alpha"
        ): "Surface_Proton_Electron_Reduction_Alpha",
    }
)


def find_canonical_reaction_family(value: str | None) -> str | None:
    """Return the canonical RMG family name for a user-provided label, if known."""

    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    return _REACTION_FAMILY_BY_KEY.get(_reaction_family_key(normalized))


def normalize_reaction_family(value: str | None) -> str | None:
    """Normalize loose user input to a canonical RMG reaction family name."""

    canonical = find_canonical_reaction_family(value)
    if canonical is None and value is not None and value.strip():
        raise ValueError(
            f"Unsupported reaction_family {value!r}. Use a canonical RMG kinetics family name."
        )
    return canonical
