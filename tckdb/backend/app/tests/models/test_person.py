"""
TCKDB backend app tests models test_person module
"""

from tckdb.backend.app.models.person import Person


def test_person_model():
    """Test creating an instance of Person"""
    person_1 = Person(name='I. B. Writing', email='email@dot.com', affiliation='Institution',
                      uploaded_species= 30, reviewed_species=15)
    assert person_1.name == 'I. B. Writing'
    assert person_1.email == 'email@dot.com'
    assert person_1.affiliation == 'Institution'
    assert person_1.uploaded_species == 30
    assert person_1.reviewed_species == 15
    assert person_1.reviewed_networks is None
    assert str(person_1) == "<Person(name='I. B. Writing', email='email@dot.com', affiliation='Institution', " \
                            "uploads=30, reviews=15)>"
    assert repr(person_1) == "<Person(id=None, name='I. B. Writing', email='email@dot.com', " \
                             "affiliation='Institution', uploaded_species=30, reviewed_species=15)>"

    person_2 = Person(name='I. B. Writing', email='email@dot.com', affiliation='Institution',
                      uploaded_species=3000, uploaded_non_physical_species=1, uploaded_reactions=500,
                      uploaded_networks=35, reviewed_species=1500, reviewed_non_physical_species=5,
                      reviewed_reactions=60, reviewed_networks=10)
    assert person_2.uploaded_species == 3000
    assert person_2.uploaded_non_physical_species == 1
    assert person_2.uploaded_reactions == 500
    assert person_2.uploaded_networks == 35
    assert person_2.reviewed_species == 1500
    assert person_2.reviewed_non_physical_species == 5
    assert person_2.reviewed_reactions == 60
    assert person_2.reviewed_networks == 10
    assert str(person_2) == "<Person(name='I. B. Writing', email='email@dot.com', affiliation='Institution', " \
                            "uploads=3536, reviews=1575)>"
    assert repr(person_2) == "<Person(id=None, name='I. B. Writing', email='email@dot.com', " \
                             "affiliation='Institution', uploaded_species=3000, uploaded_non_physical_species=1, " \
                             "uploaded_reactions=500, uploaded_networks=35, reviewed_species=1500, " \
                             "reviewed_non_physical_species=5, reviewed_reactions=60, reviewed_networks=10)>"
