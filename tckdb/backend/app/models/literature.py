"""
TCKDB backend app models literature module
"""

from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import MsgpackExt


class Literature(Base):
    """
    A class for representing a TCKDB Literature item

    Examples::

        Literature(type='article',
                   authors='M.I. It, D.C. Wash',
                   title='Kinetics of the Reactions in a Model: Part II',
                   year=2020,
                   journal='Int. J. Chem. Kin.',
                   volume=53,
                   issue=2,
                   page_start=2222,
                   page_end=2229,
                   doi='10.67/doi',
                   url='url.com/article/abstract')

        Literature(type='book',
                   authors='M.I. It, D.C. Wash',
                   title='Principles of Kinetic Modeling',
                   year=1982,
                   publisher='WeeLy',
                   editors='E.D. Torr',
                   edition='2nd Edn.',
                   chapter_title='These are Updated Rates',
                   publication_place='New York NY',
                   isbn='978-3-16-148410-0',
                   url='url.com/book/abstract')

        Literature(type='thesis',
                   authors='P.H. David',
                   title='Kinetic Modeling Dissertation',
                   year=2020,
                   publisher='MIT',
                   advisor='P.R. Ofessor',
                   url='url.com/dissertation/abstract')

    Attributes:
        id (int)
            The primary key (not a user input)
        type (str)
            The Literature type. Allowed values are ``'article'``, ``'book'``, or ``'thesis'``
        authors (str)
            The names of all authors (limited to 255 characters, use "et al." if needed)
        title (str)
            The article, thesis, or book title
        year (int)
            The publication year
        journal (str, optional)
            The article journal, required for articles
        volume (int, optional)
            The journal volume, required for articles
        issue (int, optional)
            The journal issue
        page_start (int, optional)
            The article starting page, required for articles
        page_end (int, optional)
            The article ending page, required for articles
        publisher (str, optional)
            The book's publisher, required for books
        editors (str, optional)
            The book editors, required for books
        edition (str, optional)
            The book edition
        chapter_title (str, optional)
            The book's chapter title
        publication_place (str, optional)
            The book's publication place, required for books
        advisor (str, optional)
            The thesis advisor, required for theses
        doi (str, optional)
            The article DOI, required for articles
        isbn (str, optional)
            The book ISBN, required for books
        url (str, optional)
            The web address of the Literature source

        species (relationship)
            A One to Many relationship between Literature and Species.
        non_physical_species (relationship)
            A One to Many relationship between Literature and NonPhysicalSpecies.

        reviewer_flags (Dict[str, str])
            Backend flags to assist the review process (not a user input)
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    type = Column(String(10), nullable=False)
    title = Column(String(255), nullable=False)
    year = Column(Integer, nullable=False)
    journal = Column(String(255))
    publisher = Column(String(255))
    volume = Column(Integer)
    issue = Column(Integer)
    page_start = Column(Integer)
    page_end = Column(Integer)
    editors = Column(String(255))
    edition = Column(String(50))
    chapter_title = Column(String(255))
    publication_place = Column(String(255))
    advisor = Column(String(255))
    doi = Column(String(255))
    isbn = Column(String(255))
    url = Column(String(500), nullable=False)
    reviewer_flags = Column(MsgpackExt, nullable=True)

    authors = relationship("Author", secondary="literature_author", back_populates="literature")
    
    def __repr__(self) -> str:
        """
        A string representation from which the object can be reconstructed.
        """
        repr_str = f"<{self.__class__.__name__}("
        repr_str += f"id={self.id}, "
        repr_str += f"type='{self.type}', "
        repr_str += f"authors='{self.authors}', "
        repr_str += f"title='{self.title}', "
        repr_str += f"year={self.year}, "
        if self.journal is not None:
            repr_str += f"journal='{self.journal}', "
        if self.publisher is not None:
            repr_str += f"publisher='{self.publisher}', "
        if self.volume is not None:
            repr_str += f"volume={self.volume}, "
        if self.issue is not None:
            repr_str += f"issue={self.issue}, "
        if self.page_start is not None:
            repr_str += f"page_start={self.page_start}, "
        if self.page_end is not None:
            repr_str += f"page_end={self.page_end}, "
        if self.editors is not None:
            repr_str += f"editors='{self.editors}', "
        if self.edition is not None:
            repr_str += f"edition='{self.edition}', "
        if self.chapter_title is not None:
            repr_str += f"chapter_title='{self.chapter_title}', "
        if self.publication_place is not None:
            repr_str += f"publication_place='{self.publication_place}', "
        if self.advisor is not None:
            repr_str += f"advisor='{self.advisor}', "
        if self.doi is not None:
            repr_str += f"doi='{self.doi}', "
        if self.isbn is not None:
            repr_str += f"isbn='{self.isbn}', "
        repr_str += f"url='{self.url}')>"
        return repr_str

    def __str__(self) -> str:
        """
        A user-friendly string representation of the object.
        """
        if self.type == 'article':
            return f'{self.authors}, "{self.title}", {self.journal} {self.year}, {self.volume}({self.issue}), ' \
                   f'{self.page_start}-{self.page_end}. doi: {self.doi}'
        if self.type == 'book':
            return f'{self.authors}, "{self.chapter_title}", in: {self.editors} "{self.title}", {self.edition}, ' \
                   f'{self.publisher}, {self.publication_place} {self.year}. ISBN: {self.isbn}'
        if self.type == 'thesis':
            return f'{self.authors}, Dissertation title: "{self.title}", {self.year}, {self.publisher}, ' \
                   f'Advisor: {self.advisor}. URL: {self.url}'
