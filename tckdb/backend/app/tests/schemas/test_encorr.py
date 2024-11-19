import pytest
from pydantic import ValidationError

from tckdb.backend.app.schemas.encorr import EnCorrCreate, IsodesmicReactionEntry
from tckdb.backend.app.schemas.level import LevelCreate


@pytest.fixture
def supported_elements():
    return ["H", "C", "N", "O", "S", "P"]


@pytest.fixture
def valid_aec():
    return {
        "H": -0.502155915123,
        "C": -37.8574709934,
        "N": -54.6007233609,
        "O": -75.0909131284,
        "P": -341.281730319,
        "S": -398.134489850,
    }


@pytest.fixture
def valid_bac():
    return {
        "C-H": 0.25,
        "C-C": -1.89,
        "C=C": -0.40,
        "C#C": -1.50,
        "O-H": -1.09,
        "C-O": -1.18,
        "C=O": -0.01,
        "N-H": 1.36,
        "C-N": -0.44,
        "C#N": 0.22,
        "C-S": -2.35,
        "O=S": -5.19,
        "S-H": -0.52,
    }


@pytest.fixture
def primary_level():
    return LevelCreate(
        method="B3LYP",
        basis="6-31G(d,p)",
        dispersion="gd3bj",
        # Add other required fields if any
    )


@pytest.fixture
def isodesmic_high_level():
    return LevelCreate(
        method="M062X",
        basis="cc-pVTZ",
        # Add other required fields if any
    )


def test_encorr_schema(
    supported_elements, valid_aec, valid_bac, primary_level, isodesmic_high_level
):
    """Test creating an EnCorr object"""

    # Test case 1: Valid aec and bac
    encorr_1 = EnCorrCreate(
        supported_elements=supported_elements,
        energy_unit="hartree",
        aec=valid_aec,
        bac=valid_bac,
        primary_level=primary_level,
    )
    assert encorr_1.supported_elements == supported_elements
    assert encorr_1.energy_unit == "hartree"
    assert encorr_1.aec == valid_aec
    assert encorr_1.bac == valid_bac
    assert encorr_1.isodesmic_reactions is None
    assert encorr_1.isodesmic_high_level is None
    # assert encorr_1.reviewer_flags == {}

    # Test case 2: Valid isodesmic reactions
    isodesmic_reactions = [
        IsodesmicReactionEntry(
            reactants=["[CH2]CCCC", "[CH]"],
            products=["[C]C", "[CH2]C(C)C"],
            stoichiometry=[1, 1, 1, 1],
            DHrxn298=16.809,
        ),
        IsodesmicReactionEntry(
            reactants=["InChI=1S/C5H11/c1-3-5-4-2/h1,3-5H2,2H3", "[CH3]"],
            products=["CCCC", "InChI=1S/C2H4/c1-2/h1H,2H3"],
            stoichiometry=[1, 1, 1, 1],
            DHrxn298=15.409,
        ),
    ]

    # Test case 2: Valid isodesmic reactions
    encorr_2 = EnCorrCreate(
        supported_elements=supported_elements,
        energy_unit="kcal/mol",
        isodesmic_reactions=isodesmic_reactions,
        primary_level=primary_level,
        isodesmic_high_level=isodesmic_high_level,
    )

    assert encorr_2.supported_elements == supported_elements
    assert encorr_2.energy_unit == "kcal/mol"
    assert encorr_2.aec is None
    assert encorr_2.bac is None
    assert encorr_2.isodesmic_reactions == isodesmic_reactions
    # assert encorr_2.isodesmic_high_level == isodesmic_high_level
    # assert encorr_2.reviewer_flags == {}

    # Negative Tests

    # Test case 3: Invalid element in supported elements
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=["M", "C", "N", "O", "S", "P"],
            energy_unit="hartree",
            aec=valid_aec,
            bac=valid_bac,
            primary_level=primary_level,
        )
    assert "does not seem to correspond to a known chemical element" in str(
        exc_info.value
    )

    # Test case 4: Invalid energy units
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="wrong",
            aec=valid_aec,
            bac=valid_bac,
            primary_level=primary_level,
        )
    assert 'Invalid energy unit "wrong"' in str(exc_info.value)

    # Test case 5: aec element not in supported_elements
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="hartree",
            aec={
                "Si": -0.502155915123,
                "C": -37.8574709934,
                "N": -54.6007233609,
                "O": -75.0909131284,
                "P": -341.281730319,
                "S": -398.134489850,
            },
            bac=valid_bac,
            # primary_level=primary_level
        )
    assert 'The supported_elements list is missing the symbol "Si"' in str(
        exc_info.value
    )

    # Test case 6: aec and supported_elements have different lengths
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="hartree",
            aec={
                "H": -0.502155915123,
                "C": -37.8574709934,
                "N": -54.6007233609,
                "O": -75.0909131284,
                "P": -341.281730319,
            },
            bac=valid_bac,
            primary_level=primary_level,
        )
    assert (
        "supported_elements list length and the number of entries in aec must be equal"
        in str(exc_info.value)
    )

    # Test case 7: Space in bac key
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="hartree",
            aec=valid_aec,
            bac={"C- H": 0.25, "C-C": -1.89},
            primary_level=primary_level,
        )
    assert "A bond representation cannot contain spaces" in str(exc_info.value)

    # Test case 8: No bond descriptor in bac key
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="hartree",
            aec=valid_aec,
            bac={"CH": 0.25, "C-C": -1.89},
            primary_level=primary_level,
        )
    assert "Could not find a bond descriptor in CH" in str(exc_info.value)

    # Test case 9: Two bond descriptors in bac key
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="hartree",
            aec=valid_aec,
            bac={"C-=H": 0.25, "C-C": -1.89},
            primary_level=primary_level,
        )
    assert "Found 2 bond descriptors" in str(exc_info.value)

    # Test case 10: bac element not in supported_elements
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="hartree",
            aec=valid_aec,
            bac={"C-Cl": 0.25, "C-C": -1.89},
            primary_level=primary_level,
        )
    assert "The supported_elements list is missing the symbol" in str(exc_info.value)
    assert '"Cl"' in str(exc_info.value)

    # Test case 11: No bac nor isodesmic
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="hartree",
            aec=valid_aec,
            # bac=None,
            primary_level=primary_level,
        )
    assert "Either BAC or isodesmic reactions must be provided." in str(exc_info.value)

    # Test case 12: No aec nor isodesmic
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="hartree",
            bac=valid_bac,
            # aec=None,
            primary_level=primary_level,
        )
    assert "Either isodesmic reactions or BAC and AEC must be provided" in str(
        exc_info.value
    )

    # Test case 13: Both isodesmic and aec
    isodesmic_reactions = [
        IsodesmicReactionEntry(
            reactants=["[CH2]CCCC", "[CH]"],
            products=["[C]C", "[CH2]C(C)C"],
            stoichiometry=[1, 1, 1, 1],
            DHrxn298=16.809,
        )
    ]

    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="hartree",
            aec=valid_aec,
            isodesmic_reactions=isodesmic_reactions,
            isodesmic_high_level=isodesmic_high_level,
            primary_level=primary_level,
        )
    assert (
        "When specifying isodesmic reactions, both aec and bac arguments must not be specified"
        in exc_info.value.errors()[0]["msg"]
    )

    # Test case 14: Both isodesmic and bac
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="hartree",
            bac=valid_bac,
            isodesmic_reactions=IsodesmicReactionEntry(
                reactants=["[CH2]CCCC", "[CH]"],
                products=["[C]C", "[CH2]C(C)C"],
                stoichiometry=[1, 1, 1, 1],
                DHrxn298=16.809,
            ),
            isodesmic_high_level=isodesmic_high_level,
            primary_level=primary_level,
        )
    assert (
        "When specifying isodesmic reactions, both aec and bac arguments must not be specified"
        in str(exc_info.value)
    )

    # Test case 15: Both isodesmic and aec/bac
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="hartree",
            aec=valid_aec,
            bac=valid_bac,
            isodesmic_reactions=IsodesmicReactionEntry(
                reactants=["[CH2]CCCC", "[CH]"],
                products=["[C]C", "[CH2]C(C)C"],
                stoichiometry=[1, 1, 1, 1],
                DHrxn298=16.809,
            ),
            isodesmic_high_level=isodesmic_high_level,
            primary_level=primary_level,
        )
    assert (
        "When specifying isodesmic reactions, both aec and bac arguments must not be specified"
        in str(exc_info.value)
    )

    # Test case 16: isodesmic 'reactants' not a list
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="kcal/mol",
            isodesmic_reactions=IsodesmicReactionEntry(
                reactants="[CH2]CCCC+[CH]",
                products=["[C]C", "[CH2]C(C)C"],
                stoichiometry=[1, 1, 1, 1],
                DHrxn298=16.809,
            ),
            isodesmic_high_level=isodesmic_high_level,
            primary_level=primary_level,
        )
    assert (
        "Input should be a valid list [type=list_type, input_value='[CH2]CCCC+[CH]', input_type=str]"
        in str(exc_info.value)
    )

    # Test case 17: isodesmic 'products' not a list

    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="kcal/mol",
            isodesmic_reactions=IsodesmicReactionEntry(
                reactants=["[CH2]CCCC", "[CH]"],
                products="[C]C+[CH2]C(C)C",
                stoichiometry=[1, 1, 1, 1],
                DHrxn298=16.809,
            ),
            isodesmic_high_level=isodesmic_high_level,
            primary_level=primary_level,
        )
    assert (
        "Input should be a valid list [type=list_type, input_value='[C]C+[CH2]C(C)C', input_type=str]"
        in str(exc_info.value)
    )

    # Test case 18: isodesmic 'products' has an invalid identifier
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="kcal/mol",
            isodesmic_reactions=IsodesmicReactionEntry(
                reactants=["[CH2]CCCC", "[CH]"],
                products=["[C]C++++f151_invalid", "[CH2]C(C)C"],
                stoichiometry=[1, 1, 1, 1],
                DHrxn298=16.809,
            ),
            isodesmic_high_level=isodesmic_high_level,
            primary_level=primary_level,
        )
    assert "Invalid species identifier" in str(exc_info.value)

    # Test case 19: isodesmic 'stoichiometry' is not a list
    with pytest.raises(TypeError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="kcal/mol",
            isodesmic_reactions=[
                IsodesmicReactionEntry(
                    reactants=["[CH2]CCCC", "[CH]"],
                    products=["[C]C", "[CH2]C(C)C"],
                    stoichiometry="*1 *1 *1 *1",
                    DHrxn298=16.809,
                )
            ],
            isodesmic_high_level=isodesmic_high_level,
            primary_level=primary_level,
        )
    assert "Stoichiometry must be a list of integers" in str(exc_info.value)

    # Test case 20: isodesmic 'stoichiometry' coefficient is not an integer
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="kcal/mol",
            isodesmic_reactions=[
                IsodesmicReactionEntry(
                    reactants=["[CH2]CCCC", "[CH]"],
                    products=["[C]C", "[CH2]C(C)C"],
                    stoichiometry=["one", 1, 1, 1],
                    DHrxn298=16.809,
                )
            ],
            isodesmic_high_level=isodesmic_high_level,
            primary_level=primary_level,
        )
    assert "Input should be a valid integer, unable to parse" in str(exc_info.value)

    # Test case 21: isodesmic 'DHrxn298' is not a float
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="kcal/mol",
            isodesmic_reactions=[
                IsodesmicReactionEntry(
                    reactants=["[CH2]CCCC", "[CH]"],
                    products=["[C]C", "[CH2]C(C)C"],
                    stoichiometry=[1, 1, 1, 1],
                    DHrxn298=(16.809, "kJ/mol"),
                )
            ],
            isodesmic_high_level=isodesmic_high_level,
            primary_level=primary_level,
        )
    assert (
        " Input should be a valid number [type=float_type, input_value=(16.809, 'kJ/mol'), input_type=tuple]"
        in str(exc_info.value)
    )

    # Test case 22: isodesmic reaction has a wrong key
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="kcal/mol",
            isodesmic_reactions=IsodesmicReactionEntry(
                reactants=["[CH2]CCCC", "[CH]"],
                products=["[C]C", "[CH2]C(C)C"],
                stoichiometry=[1, 1, 1, 1],
                enthalpy_change_of_reaction=16.809,  # Incorrect key
            ),
            isodesmic_high_level=isodesmic_high_level,
            primary_level=primary_level,
        )
    assert (
        "enthalpy_change_of_reaction\n  Extra inputs are not permitted [type=extra_forbidden, input_value=16.809, input_type=float]"
        in str(exc_info.value)
    )

    # Test case 23: isodesmic reaction is missing a key ('products')
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="kcal/mol",
            isodesmic_reactions=IsodesmicReactionEntry(
                reactants=["[CH2]CCCC", "[CH]"],
                stoichiometry=[1, 1, 1, 1],
                DHrxn298=16.809,  # Missing 'products'
            ),
            isodesmic_high_level=isodesmic_high_level,
            primary_level=primary_level,
        )
    assert (
        "products\n  Field required [type=missing, input_value={'reactants': ['[CH2]CCCC... 1], 'DHrxn298': 16.809}, input_type=dict]"
        in str(exc_info.value)
    )

    # Test case 24: isodesmic reaction is missing a key ('reactants')
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="kcal/mol",
            isodesmic_reactions=IsodesmicReactionEntry(
                products=["[C]C", "[CH2]C(C)C"],
                stoichiometry=[1, 1, 1, 1],
                DHrxn298=16.809,  # Missing 'reactants'
            ),
            isodesmic_high_level=isodesmic_high_level,
            primary_level=primary_level,
        )
    assert (
        "reactants\n  Field required [type=missing, input_value={'products': ['[C]C', '[C... 1], 'DHrxn298': 16.809}, input_type=dict]"
        in str(exc_info.value)
    )

    # Test case 25: isodesmic reaction has an extra key ('index')
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="kcal/mol",
            isodesmic_reactions=IsodesmicReactionEntry(
                reactants=["[CH2]CCCC", "[CH]"],
                products=["[C]C", "[CH2]C(C)C"],
                stoichiometry=[1, 1, 1, 1],
                DHrxn298=16.809,
                index=152,  # Extra key
            ),
            isodesmic_high_level=isodesmic_high_level,
            primary_level=primary_level,
        )
    assert (
        "index\n  Extra inputs are not permitted [type=extra_forbidden, input_value=152, input_type=int]"
        in str(exc_info.value)
    )

    # Test case 26: isodesmic reaction with no isodesmic_high_level_id
    with pytest.raises(ValidationError) as exc_info:
        EnCorrCreate(
            supported_elements=supported_elements,
            energy_unit="kcal/mol",
            isodesmic_reactions=[
                IsodesmicReactionEntry(
                    reactants=["[CH2]CCCC", "[CH]"],
                    products=["[C]C", "[CH2]C(C)C"],
                    stoichiometry=[1, 1, 1, 1],
                    DHrxn298=16.809,
                )
            ],
            # isodesmic_high_level is missing
            primary_level=primary_level,
        )
    assert (
        "Value error, The 'isodesmic_high_level' must be provided when 'isodesmic_reactions' are specified"
        in str(exc_info.value)
    )
