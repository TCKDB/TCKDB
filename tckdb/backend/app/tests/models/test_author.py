"""
TCKDB backend app tests models test_author module
"""

from tckdb.backend.app.models.author import Author


def test_author_model():
    """Test creating an instance of Author"""
    author_1 = Author(name='I. B. Writing', email='email@dot.com', affiliation='Institution',
                      uploaded_species= 30, reviewed_species=15)
    assert author_1.name == 'I. B. Writing'
    assert author_1.email == 'email@dot.com'
    assert author_1.affiliation == 'Institution'
    assert author_1.uploaded_species == 30
    assert author_1.reviewed_species == 15
    assert author_1.reviewed_networks is None
    assert str(author_1) == "<Author(name='I. B. Writing', email='email@dot.com', affiliation='Institution', " \
                            "uploads=30, reviews=15)>"
    assert repr(author_1) == "<Author(id=None, name='I. B. Writing', email='email@dot.com', " \
                             "affiliation='Institution', uploaded_species=30, reviewed_species=15)>"

    author_2 = Author(name='I. B. Writing', email='email@dot.com', affiliation='Institution',
                      uploaded_species=3000, uploaded_non_physical_species=1, uploaded_reactions=500,
                      uploaded_networks=35, reviewed_species=1500, reviewed_non_physical_species=5,
                      reviewed_reactions=60, reviewed_networks=10)
    assert author_2.uploaded_species == 3000
    assert author_2.uploaded_non_physical_species == 1
    assert author_2.uploaded_reactions == 500
    assert author_2.uploaded_networks == 35
    assert author_2.reviewed_species == 1500
    assert author_2.reviewed_non_physical_species == 5
    assert author_2.reviewed_reactions == 60
    assert author_2.reviewed_networks == 10
    assert str(author_2) == "<Author(name='I. B. Writing', email='email@dot.com', affiliation='Institution', " \
                            "uploads=3536, reviews=1575)>"
    assert repr(author_2) == "<Author(id=None, name='I. B. Writing', email='email@dot.com', " \
                             "affiliation='Institution', uploaded_species=3000, uploaded_non_physical_species=1, " \
                             "uploaded_reactions=500, uploaded_networks=35, reviewed_species=1500, " \
                             "reviewed_non_physical_species=5, reviewed_reactions=60, reviewed_networks=10)>"
