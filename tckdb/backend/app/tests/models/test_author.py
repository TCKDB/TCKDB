"""
TCKDB backend app tests models test_author module
"""

from tckdb.backend.app.models.author import Author


def test_author_model():
    """Test creating an instance of Author"""
    author_1 = Author(name='I. B. Writing', email='email@dot.com', affiliation='Institution')
    assert author_1.name == 'I. B. Writing'
    assert author_1.email == 'email@dot.com'
    assert author_1.affiliation == 'Institution'
    assert str(author_1) == "<Author(id=None, name='I. B. Writing', email='email@dot.com', affiliation='Institution')>"
