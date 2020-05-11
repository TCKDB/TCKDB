"""
TCKDB backend app tests schemas test_author module
"""

import pytest
from pydantic import ValidationError

from tckdb.backend.app.schemas.author import AuthorBase


def test_author_schema():
    """Test creating an instance of Author"""
    author_1 = AuthorBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution',
                          uploaded_species=300000, uploaded_reactions=15000, uploaded_networks=500)
    assert author_1.name == 'I. B. Writing'
    assert author_1.email == 'email@dot.com'
    assert author_1.affiliation == 'Institution'
    assert author_1.uploaded_species == 300000
    assert author_1.uploaded_reactions == 15000
    assert author_1.uploaded_networks == 500
    assert author_1.reviewed_species is None

    with pytest.raises(ValidationError):
        # no @ in email
        AuthorBase(name='I. B. Writing', email='not_a_valid.email.com', affiliation='Institution')
    with pytest.raises(ValidationError):
        # two @ in email
        AuthorBase(name='I. B. Writing', email='not.a@valid@email', affiliation='Institution')
    with pytest.raises(ValidationError):
        # space in email
        AuthorBase(name='I. B. Writing', email='not a.valid@email.com', affiliation='Institution')
    with pytest.raises(ValidationError):
        # space in email
        AuthorBase(name='I. B. Writing', email='email @dot.com', affiliation='Institution')
    with pytest.raises(ValidationError):
        # no dot in email
        AuthorBase(name='I. B. Writing', email='not_a_valid@emailcom', affiliation='Institution')
    with pytest.raises(ValidationError):
        # no space in name
        AuthorBase(name='Writing', email='email@dot.com', affiliation='Institution')
    with pytest.raises(ValidationError):
        AuthorBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution', uploaded_species=-1)
    with pytest.raises(ValidationError):
        AuthorBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution',
                   uploaded_non_physical_species=-1)
    with pytest.raises(ValidationError):
        AuthorBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution', uploaded_reactions=-1)
    with pytest.raises(ValidationError):
        AuthorBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution', uploaded_networks=-1)
    with pytest.raises(ValidationError):
        AuthorBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution', reviewed_species=-1)
    with pytest.raises(ValidationError):
        AuthorBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution',
                   reviewed_non_physical_species=-1)
    with pytest.raises(ValidationError):
        AuthorBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution', reviewed_reactions=-1)
    with pytest.raises(ValidationError):
        AuthorBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution', reviewed_networks=-1)
