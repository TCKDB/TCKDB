"""
TCKDB backend app tests schemas test_person module
"""

import pytest
from pydantic import ValidationError

from tckdb.backend.app.schemas.person import PersonBase


def test_person_schema():
    """Test creating an instance of Person"""
    person_1 = PersonBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution',
                          uploaded_species=300000, uploaded_reactions=15000, uploaded_networks=500)
    assert person_1.name == 'I. B. Writing'
    assert person_1.email == 'email@dot.com'
    assert person_1.affiliation == 'Institution'
    assert person_1.uploaded_species == 0
    assert person_1.uploaded_reactions == 0
    assert person_1.uploaded_networks == 0
    assert person_1.reviewed_species == 0

    with pytest.raises(ValidationError):
        # no @ in email
        PersonBase(name='I. B. Writing', email='not_a_valid.email.com', affiliation='Institution')
    with pytest.raises(ValidationError):
        # two @ in email
        PersonBase(name='I. B. Writing', email='not.a@valid@email', affiliation='Institution')
    with pytest.raises(ValidationError):
        # space in email
        PersonBase(name='I. B. Writing', email='not a.valid@email.com', affiliation='Institution')
    with pytest.raises(ValidationError):
        # space in email
        PersonBase(name='I. B. Writing', email='email @dot.com', affiliation='Institution')
    with pytest.raises(ValidationError):
        # no dot in email
        PersonBase(name='I. B. Writing', email='not_a_valid@emailcom', affiliation='Institution')
    with pytest.raises(ValidationError):
        # no space in name
        PersonBase(name='Writing', email='email@dot.com', affiliation='Institution')
    with pytest.raises(ValidationError):
        PersonBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution', uploaded_species=-1)
    with pytest.raises(ValidationError):
        PersonBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution',
                   uploaded_non_physical_species=-1)
    with pytest.raises(ValidationError):
        PersonBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution', uploaded_reactions=-1)
    with pytest.raises(ValidationError):
        PersonBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution', uploaded_networks=-1)
    with pytest.raises(ValidationError):
        PersonBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution', reviewed_species=-1)
    with pytest.raises(ValidationError):
        PersonBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution',
                   reviewed_non_physical_species=-1)
    with pytest.raises(ValidationError):
        PersonBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution', reviewed_reactions=-1)
    with pytest.raises(ValidationError):
        PersonBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution', reviewed_networks=-1)
