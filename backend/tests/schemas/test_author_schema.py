import pytest
from pydantic import ValidationError

from app.schemas.entities.author import AuthorCreate, AuthorUpdate


def test_author_create_derives_full_name_from_given_and_family_name() -> None:
    author = AuthorCreate(given_name="  Ada  ", family_name="  Lovelace  ")

    assert author.given_name == "Ada"
    assert author.family_name == "Lovelace"
    assert author.full_name == "Ada Lovelace"


def test_author_create_uses_family_name_when_given_name_missing() -> None:
    author = AuthorCreate(family_name="Curie")

    assert author.full_name == "Curie"


def test_author_create_keeps_explicit_full_name() -> None:
    author = AuthorCreate(
        given_name="Niels",
        family_name="Bohr",
        full_name="Niels Henrik David Bohr",
    )

    assert author.full_name == "Niels Henrik David Bohr"


def test_author_update_derives_full_name_when_family_name_present() -> None:
    author = AuthorUpdate(given_name="Linus", family_name="Pauling")

    assert author.full_name == "Linus Pauling"


def test_author_create_normalizes_compact_orcid() -> None:
    author = AuthorCreate(family_name="Curie", orcid="0000000218250097")

    assert author.orcid == "0000-0002-1825-0097"


def test_author_create_rejects_invalid_orcid_check_digit() -> None:
    with pytest.raises(ValidationError):
        AuthorCreate(family_name="Curie", orcid="0000-0002-1825-0098")
