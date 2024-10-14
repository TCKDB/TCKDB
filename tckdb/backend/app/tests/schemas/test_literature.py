"""
TCKDB backend app tests schemas test_literature module
"""
from datetime import datetime
import pytest
from pydantic import ValidationError

from tckdb.backend.app.schemas.literature import LiteratureBase, LiteratureTypeEnum, LiteratureCreate
from tckdb.backend.app.schemas.author import AuthorCreate


def create_author(first_name: str, last_name: str) -> AuthorCreate:
    """Helper function to create an AuthorCreate instance."""
    return AuthorCreate(first_name=first_name, last_name=last_name)


@pytest.mark.parametrize("valid_type", ['article', 'book', 'thesis'])
def test_literature_type_enum_valid(valid_type):
    """Test valid LiteratureTypeEnum values."""
    assert LiteratureTypeEnum(valid_type) == valid_type


@pytest.mark.parametrize("invalid_type", ['1000', 'journal', '', None])
def test_literature_type_enum_invalid(invalid_type):
    """Test invalid LiteratureTypeEnum values."""
    with pytest.raises(ValueError):
        LiteratureTypeEnum(invalid_type)


@pytest.mark.parametrize("case", [
    {
        "input": {
            "type": 'article',
            "authors": [
                create_author('M.I.', 'It'),
                create_author('D.C.', 'Wash')
            ],
            "title": 'Kinetics of the Reactions in a Model: Part II',
            "year": 2020,
            "journal": 'Int. J. Chem. Kin.',
            "volume": 53,
            "issue": 2,
            "page_start": 2222,
            "page_end": 2229,
            "doi": '10.67/doi',
            "url": 'u.rl.com/article/abstract',
        },
        "expected": {
            "type": 'article',
            "authors": [
                create_author('M.I.', 'It'),
                create_author('D.C.', 'Wash')
            ],
            "title": 'Kinetics of the Reactions in a Model: Part II',
            "year": 2020,
            "journal": 'Int. J. Chem. Kin.',
            "volume": 53,
            "issue": 2,
            "page_start": 2222,
            "page_end": 2229,
            "doi": '10.67/doi',
            "url": 'u.rl.com/article/abstract',
        }
    },
    {
        "input": {
            "type": 'book',
            "authors": [
                create_author('M.I.', 'It'),
                create_author('D.C.', 'Wash')
            ],
            "title": 'Principles of Kinetic Modeling',
            "year": 1982,
            "publisher": 'Wee-Ly',
            "editors": 'E.D. Torr',
            "edition": '2nd Edn.',
            "chapter_title": 'These are Updated Rates',
            "publication_place": 'New York NY',
            "isbn": '978-3-16-148410-0',
            "url": 'u.rl.com/book/abstract',
        },
        "expected": {
            "type": 'book',
            "authors": [
                create_author('M.I.', 'It'),
                create_author('D.C.', 'Wash')
            ],
            "title": 'Principles of Kinetic Modeling',
            "year": 1982,
            "publisher": 'Wee-Ly',
            "editors": 'E.D. Torr',
            "edition": '2nd Edn.',
            "chapter_title": 'These are Updated Rates',
            "publication_place": 'New York NY',
            "isbn": '978-3-16-148410-0',
            "url": 'u.rl.com/book/abstract',
        }
    },
    {
        "input": {
            "type": 'thesis',
            "authors": [
                create_author('M.I.', 'It')
            ],
            "title": 'Kinetic Modeling Dissertation',
            "year": 2020,
            "publisher": 'MIT',
            "advisor": 'P.R. Fessor',
            "url": 'u.rl.com/dissertation/abstract',
        },
        "expected": {
            "type": 'thesis',
            "authors": [
                create_author('M.I.', 'It')
            ],
            "title": 'Kinetic Modeling Dissertation',
            "year": 2020,
            "publisher": 'MIT',
            "advisor": 'P.R. Fessor',
            "url": 'u.rl.com/dissertation/abstract',
        }
    },
])

def test_valid_literature_schema(case):
    """Test creating valid instances of LiteratureCreate."""
    lit = LiteratureCreate(**case["input"])
    for field, expected_value in case["expected"].items():
        assert getattr(lit, field) == expected_value, f"Mismatch in field '{field}'"

@pytest.mark.parametrize("invalid_case", [
    {
        "input": {
            "type": 'wrong',
            "author_ids": [1],
            "title": 'Kinetic Modeling Dissertation',
            "year": 2020,
            "url": 'u.rl.com/dissertation/abstract',
            "advisor": 'P.R. Fessor'
        },
        "field": 'type',
        "message": "value is not a valid enumeration member; permitted: 'article', 'book', 'thesis'"
    },
    {
        "input": {
            "type": 'thesis',
            "title": 'Kinetic Modeling Dissertation',
            "year": 2020,
            "url": 'u.rl.com/dissertation/abstract',
            "advisor": 'P.R. Fessor'
            # Missing 'authors' and 'author_ids'
        },
        "field": 'authors or author_ids',
        "message": "Either 'authors' or 'author_ids' must be provided."
    },
    {
        "input": {
            "type": 'thesis',
            "authors": [
                create_author('M.I.', 'It')
            ],
            "title": 'Kinetic_Modeling_Dissertation',  # Underscores in title
            "year": 2020,
            "url": 'url.com'
            # Missing 'advisor'
        },
        "field": 'title',
        "message": "The title appears to contain underscores. Got: Kinetic_Modeling_Dissertation. Please replace underscores with spaces."
    },
    {
        "input": {
            "type": 'thesis',
            "authors": [
                create_author('M.I.', 'It'),
                create_author('D.C.', 'Wash')
            ],
            "title": 'Kinetic Modeling Dissertation',
            "year": 20020,  # Year too large
            "url": 'url.com'
        },
        "field": 'year',
        "message": "ensure this value is less than or equal to 9999"
    },
    {
        "input": {
            "type": 'thesis',
            "authors": [
                create_author('M.I.', 'It'),
                create_author('D.C.', 'Wash')
            ],
            "title": 'Kinetic Modeling Dissertation',
            "year": datetime.now().year + 10,  # Year in the future
            "url": 'url.com'
        },
        "field": 'year',
        "message": f"The year {datetime.now().year + 10} is in the future. It must be <= {datetime.now().year}."
    },
    {
        "input": {
            "type": 'article',
            "authors": [
                create_author('M.I.', 'It'),
                create_author('D.C.', 'Wash')
            ],
            "title": 'Kinetics of',
            "year": 2020,
            "volume": 53,
            "issue": 2,
            "page_start": 2222,
            "page_end": 2229,
            "doi": '10.67/doi',
            "url": 'url.com',  # Missing 'journal'
        },
        "field": 'journal',
        "message": "The journal argument is missing for a literature type article"
    },
    # Add more invalid cases as needed...
])
def test_invalid_literature_schema(invalid_case):
    """Test creating invalid instances of LiteratureBase."""
    with pytest.raises(ValidationError) as exc_info:
        LiteratureBase(**invalid_case["input"])
    error = exc_info.value.errors()[0]
    assert invalid_case["message"] in error["msg"], f"Expected error message '{invalid_case['message']}' not found."
    # Optionally, check the field location
    if 'field' in invalid_case and invalid_case["field"] != 'authors or author_ids':
        assert invalid_case["field"] in error['loc'], f"Expected error location '{invalid_case['field']}' not found."
