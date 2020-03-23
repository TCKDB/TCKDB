"""
TCKDB backend app tests schemas test_author module
"""

import pytest
from pydantic import ValidationError

from tckdb.backend.app.schemas.author import AuthorBase


def test_author_schema():
    """Test creating an instance of Author"""
    author_1 = AuthorBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution')
    assert author_1.name == 'I. B. Writing'
    assert author_1.email == 'email@dot.com'
    assert author_1.affiliation == 'Institution'

    with pytest.raises(ValidationError):
        AuthorBase(name='I. B. Writing', email='not_a_valid.email', affiliation='Institution')
    with pytest.raises(ValidationError):
        AuthorBase(name='I. B. Writing', email='not a.valid@email', affiliation='Institution')
    with pytest.raises(ValidationError):
        AuthorBase(name='I. B. Writing', email='not.a@valid@email', affiliation='Institution')
    with pytest.raises(ValidationError):
        AuthorBase(name='I. B. Writing', email='not_a_valid@email', affiliation='Institution')
    with pytest.raises(ValidationError):
        AuthorBase(name='Writing', email='email@dot.com', affiliation='Institution')
