"""
TCKDB backend app tests schemas test_literature module
"""

import pytest
from pydantic import ValidationError

from tckdb.backend.app.schemas.literature import LiteratureBase, LiteratureTypeEnum


def test_literature_type_enum():
    """Test LiteratureTypeEnum"""
    LiteratureTypeEnum('article')
    LiteratureTypeEnum('book')
    LiteratureTypeEnum('thesis')
    with pytest.raises(ValueError):
        LiteratureTypeEnum('1000')


def test_literature_schema():
    """Test creating an instance of Literature"""
    lit1 = LiteratureBase(type='article',
                          authors='M.I. It, D.C. Wash',
                          title='Kinetics of the Reactions in a Model: Part II',
                          year=2020,
                          journal='Int. J. Chem. Kin.',
                          volume=53,
                          issue=2,
                          page_start=2222,
                          page_end=2229,
                          doi='10.67/doi',
                          url='u.rl.com/article/abstract',
                          )
    assert lit1.type == 'article'
    assert lit1.authors == 'M.I. It, D.C. Wash'
    assert lit1.title == 'Kinetics of the Reactions in a Model: Part II'
    assert lit1.year == 2020
    assert lit1.journal == 'Int. J. Chem. Kin.'
    assert lit1.volume == 53
    assert lit1.issue == 2
    assert lit1.page_start == 2222
    assert lit1.page_end == 2229
    assert lit1.doi == '10.67/doi'
    assert lit1.url == 'u.rl.com/article/abstract'

    lit2 = LiteratureBase(type='book',
                          authors='M.I. It, D.C. Wash',
                          title='Principles of Kinetic Modeling',
                          year=1982,
                          publisher='Wee-Ly',
                          editors='E.D. Torr',
                          edition='2nd Edn.',
                          chapter_title='These are Updated Rates',
                          publication_place='New York NY',
                          isbn='978-3-16-148410-0',
                          url='u.rl.com/book/abstract',
                          )
    assert lit2.type == 'book'
    assert lit2.authors == 'M.I. It, D.C. Wash'
    assert lit2.title == 'Principles of Kinetic Modeling'
    assert lit2.year == 1982
    assert lit2.publisher == 'Wee-Ly'
    assert lit2.editors == 'E.D. Torr'
    assert lit2.edition == '2nd Edn.'
    assert lit2.chapter_title == 'These are Updated Rates'
    assert lit2.publication_place == "New York NY"
    assert lit2.isbn == '978-3-16-148410-0'
    assert lit2.url == 'u.rl.com/book/abstract'

    lit3 = LiteratureBase(type='thesis',
                          authors='P.H. David',
                          title='Kinetic Modeling Dissertation',
                          year=2020,
                          publisher='MIT',
                          advisor='P.R. Fessor',
                          url='u.rl.com/dissertation/abstract',
                          )
    assert lit3.type == 'thesis'
    assert lit3.authors == 'P.H. David'
    assert lit3.title == 'Kinetic Modeling Dissertation'
    assert lit3.year == 2020
    assert lit3.publisher == 'MIT'
    assert lit3.advisor == 'P.R. Fessor'
    assert lit3.url == 'u.rl.com/dissertation/abstract'

    with pytest.raises(ValidationError):
        # wrong type
        LiteratureBase(type='wrong', authors='P.H. David', title='Kinetic Modeling Dissertation',
                       year=2020, url='url.com')
    with pytest.raises(ValidationError):
        # wrong authors
        LiteratureBase(type='thesis', authors='P.H.', title='Kinetic Modeling Dissertation',
                       year=2020, url='url.com')
    with pytest.raises(ValidationError):
        # wrong title
        LiteratureBase(type='thesis', authors='P.H. David', title='Kinetic_Modeling_Dissertation',
                       year=2020, url='url.com')
    with pytest.raises(ValidationError):
        # wrong year
        LiteratureBase(type='thesis', authors='P.H. David', title='Kinetic Modeling Dissertation',
                       year=20020, url='url.com')
    with pytest.raises(ValidationError):
        # no journal for an article
        LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kinetics of', year=2020, volume=53,
                       issue=2, page_start=2222, page_end=2229, doi='10.67/doi', url='url.com')
    with pytest.raises(ValidationError):
        # no publisher for a book
        LiteratureBase(type='book', authors='M.I. It', title='Kinetic Modeling', year=1982, editors='E.D. Torr',
                       edition='2nd Edn.', chapter_title='Updated Rates', publication_place='New York NY',
                       isbn='978-3-16-148410-0', url='url.com')
    with pytest.raises(ValidationError):
        # no volume for an article
        LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kinetics of', year=2020,
                       journal='Int. J.', issue=2, page_start=2222, page_end=2229, doi='10.67/doi', url='url.com')
    with pytest.raises(ValidationError):
        # wrong volume
        LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kin of', year=2020, journal='I. J.',
                       volume=-50, issue=2, page_start=2222, page_end=2229, doi='10.67/doi', url='url.com')
    with pytest.raises(ValidationError):
        # wrong issue
        LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kin of', year=2020, journal='I. J.',
                       volume=50, issue=-2, page_start=2222, page_end=2229, doi='10.67/doi', url='url.com')
    with pytest.raises(ValidationError):
        # no page_start for an article
        LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kinetics of', year=2020,
                       journal='Int. J.', volume=50, issue=2, page_end=2229, doi='10.67/doi', url='url.com')
    with pytest.raises(ValidationError):
        # wrong page_start
        LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kin of', year=2020, journal='I. J.',
                       volume=50, issue=2, page_start=-1, page_end=2229, doi='10.67/doi', url='url.com')
    with pytest.raises(ValidationError):
        # no page_end for an article
        LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kinetics of', year=2020,
                       journal='Int. J.', volume=50, issue=2, page_start=2222, doi='10.67/doi', url='url.com')
    with pytest.raises(ValidationError):
        # wrong page_end
        LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kin of', year=2020, journal='I. J.',
                       volume=50, issue=2, page_start=2222, page_end=-6, doi='10.67/doi', url='url.com')
    with pytest.raises(ValidationError):
        # page_end lower than page_start
        LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kin of', year=2020, journal='I. J.',
                       volume=50, issue=2, page_start=2222, page_end=1222, doi='10.67/doi', url='url.com')
    with pytest.raises(ValidationError):
        # no editors for a book
        LiteratureBase(type='book', authors='M.I. It', title='Kinetic Modeling', year=1982, publisher='Wee-Ly',
                       edition='2nd Edn.', chapter_title='Updated Rates', publication_place='New York NY',
                       isbn='978-3-16-148410-0', url='url.com')
    with pytest.raises(ValidationError):
        # long editions for a book
        LiteratureBase(type='book', authors='M.I. It', title='Kinetic Modeling', year=1982, publisher='Wee-Ly',
                       edition='2nd Edn. 2nd Edn. 2nd Edn. 2nd Edn. 2nd Edn. 2nd Edn. ',
                       chapter_title='Updated Rates', publication_place='New York NY',
                       isbn='978-3-16-148410-0', url='url.com', editors='E.D. Torr')
    with pytest.raises(ValidationError):
        # no publication_place for a book
        LiteratureBase(type='book', authors='M.I. It', title='Kinetic Modeling', year=1982, publisher='Wee-Ly',
                       edition='2nd Edn.', chapter_title='Updated Rates', editors='E.D. Torr',
                       isbn='978-3-16-148410-0', url='url.com')
    with pytest.raises(ValidationError):
        # no advisor for a thesis
        LiteratureBase(type='thesis', authors='P.H. David', title='Kinetic Modeling Dissertation', year=2020,
                       publisher='MIT', url='u.rl.com/dissertation/abstract')
    with pytest.raises(ValidationError):
        # no doi for an article
        LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kinetics of', year=2020,
                       journal='Int. J.', volume=50, issue=2, page_start=2222, page_end=2229, url='url.com')
    with pytest.raises(ValidationError):
        # no isbn for a book
        LiteratureBase(type='book', authors='M.I. It, D.C. Wash', title='Principles of Kinetic Modeling', year=1982,
                       publisher='Wee-Ly', editors='E.D. Torr', edition='2nd Edn.', chapter_title='Updated Rates',
                       publication_place='New York NY', url='u.rl.com/book/abstract')
    with pytest.raises(ValidationError):
        # wrong url
        LiteratureBase(type='book', authors='M.I. It, D.C. Wash', title='Principles of Kinetic Modeling', year=1982,
                       publisher='Wee-Ly', editors='E.D. Torr', edition='2nd Edn.', chapter_title='Updated Rates',
                       publication_place='New York NY', isbn='978-3-16-148410-0', url='u.rl.com book/abstract')
    with pytest.raises(ValidationError):
        # wrong url
        LiteratureBase(type='book', authors='M.I. It, D.C. Wash', title='Principles of Kinetic Modeling', year=1982,
                       publisher='Wee-Ly', editors='E.D. Torr', edition='2nd Edn.', chapter_title='Updated Rates',
                       publication_place='New York NY', isbn='978-3-16-148410-0', url='u-rl-com/book/abstract')
