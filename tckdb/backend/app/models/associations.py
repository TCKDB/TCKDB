"""
TCKDB backend app models associations module

This module contains association tables used in Many-to-Many data models

Create a Table to associate LJ and trans with a species per Network
(https://docs.sqlalchemy.org/en/13/core/metadata.html)

Extracted from species and not used, should be implemented once we have Network
(should be modified, this is not the correct implementation):

        # trans_id (int)
        #     The energy transfer model from the :ref:`Trans table <trans>`.
        # trans (relationship)
        #     An attribute that establishes a bidirectional relationship in a One to Many data model
        #     with the :ref:`Trans table <trans>`,
        #     where the "reverse" side is a Many to One data model.
        # LJ_id (int)
        #     The Lennard-Jones coefficients from the :ref:`LJ table <LJ>`.
        # LJ (relationship)
        #     An attribute that establishes a bidirectional relationship in a One to Many data model
        #     with the :ref:`LJ table <LJ>`,
        #     where the "reverse" side is a Many to One data model.

    trans_id = Column(Integer, ForeignKey('trans.id'), nullable=True, unique=False)
    trans = relationship('Trans', back_populates='species')
    LJ_id = Column(Integer, ForeignKey('lj.id'), nullable=True, unique=False)
    LJ = relationship('LJ', back_populates='species')
"""

from sqlalchemy import Column, Integer, ForeignKey, Table

from tckdb.backend.app.db.base_class import Base


species_authors = Table('species_authors',
                        Base.metadata,
                        Column('species_id', Integer, ForeignKey('species.id'), primary_key=True),
                        Column('author_id', Integer, ForeignKey('person.id'), primary_key=True),
                        )

species_reviewers = Table('species_reviewers',
                          Base.metadata,
                          Column('species_id', Integer, ForeignKey('species.id'), primary_key=True),
                          Column('reviewer_id', Integer, ForeignKey('person.id'), primary_key=True),
                          )

np_species_authors = Table('np_species_authors',
                           Base.metadata,
                           Column('np_species_id', Integer, ForeignKey('nonphysicalspecies.id'), primary_key=True),
                           Column('author_id', Integer, ForeignKey('person.id'), primary_key=True),
                           )

np_species_reviewers = Table('np_species_reviewers',
                             Base.metadata,
                             Column('np_species_id', Integer, ForeignKey('nonphysicalspecies.id'), primary_key=True),
                             Column('reviewer_id', Integer, ForeignKey('person.id'), primary_key=True),
                             )
