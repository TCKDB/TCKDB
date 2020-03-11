"""
TCKDB backend app tests schemas test_literature module
"""

import unittest

from pydantic import ValidationError

from tckdb.backend.app.schemas.literature import LiteratureBase, LiteratureTypeEnum


class TestLiteratureTypeEnum(unittest.TestCase):
    """
    Contains unit tests for the LiteratureTypeEnum class
    """

    def test_literature_type_enum(self):
        """Test LiteratureTypeEnum"""
        LiteratureTypeEnum('article')
        LiteratureTypeEnum('book')
        LiteratureTypeEnum('thesis')
        with self.assertRaises(ValueError):
            LiteratureTypeEnum('1000')


class TestLiteratureBaseSchema(unittest.TestCase):
    """
    Contains unit tests for the LiteratureBase schema
    """

    def test_literature_model(self):
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
        self.assertEqual(lit1.type, 'article')
        self.assertEqual(lit1.authors, 'M.I. It, D.C. Wash')
        self.assertEqual(lit1.title, 'Kinetics of the Reactions in a Model: Part II')
        self.assertEqual(lit1.year, 2020)
        self.assertEqual(lit1.journal, 'Int. J. Chem. Kin.')
        self.assertEqual(lit1.volume, 53)
        self.assertEqual(lit1.issue, 2)
        self.assertEqual(lit1.page_start, 2222)
        self.assertEqual(lit1.page_end, 2229)
        self.assertEqual(lit1.doi, '10.67/doi')
        self.assertEqual(lit1.url, 'u.rl.com/article/abstract')

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
        self.assertEqual(lit2.type, 'book')
        self.assertEqual(lit2.authors, 'M.I. It, D.C. Wash')
        self.assertEqual(lit2.title, 'Principles of Kinetic Modeling')
        self.assertEqual(lit2.year, 1982)
        self.assertEqual(lit2.publisher, 'Wee-Ly')
        self.assertEqual(lit2.editors, 'E.D. Torr')
        self.assertEqual(lit2.edition, '2nd Edn.')
        self.assertEqual(lit2.chapter_title, 'These are Updated Rates')
        self.assertEqual(lit2.publication_place, "New York NY")
        self.assertEqual(lit2.isbn, '978-3-16-148410-0')
        self.assertEqual(lit2.url, 'u.rl.com/book/abstract')

        lit3 = LiteratureBase(type='thesis',
                              authors='P.H. David',
                              title='Kinetic Modeling Dissertation',
                              year=2020,
                              publisher='MIT',
                              advisor='P.R. Fessor',
                              url='u.rl.com/dissertation/abstract',
                              )
        self.assertEqual(lit3.type, 'thesis')
        self.assertEqual(lit3.authors, 'P.H. David')
        self.assertEqual(lit3.title, 'Kinetic Modeling Dissertation')
        self.assertEqual(lit3.year, 2020)
        self.assertEqual(lit3.publisher, 'MIT')
        self.assertEqual(lit3.advisor, 'P.R. Fessor')
        self.assertEqual(lit3.url, 'u.rl.com/dissertation/abstract')

        with self.assertRaises(ValidationError):
            # wrong type
            LiteratureBase(type='wrong', authors='P.H. David', title='Kinetic Modeling Dissertation',
                           year=2020, url='url.com')
        with self.assertRaises(ValidationError):
            # wrong authors
            LiteratureBase(type='thesis', authors='P.H.', title='Kinetic Modeling Dissertation',
                           year=2020, url='url.com')
        with self.assertRaises(ValidationError):
            # wrong title
            LiteratureBase(type='thesis', authors='P.H. David', title='Kinetic_Modeling_Dissertation',
                           year=2020, url='url.com')
        with self.assertRaises(ValidationError):
            # wrong year
            LiteratureBase(type='thesis', authors='P.H. David', title='Kinetic Modeling Dissertation',
                           year=20020, url='url.com')
        with self.assertRaises(ValidationError):
            # no journal for an article
            LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kinetics of', year=2020, volume=53,
                           issue=2, page_start=2222, page_end=2229, doi='10.67/doi', url='url.com')
        with self.assertRaises(ValidationError):
            # no publisher for a book
            LiteratureBase(type='book', authors='M.I. It', title='Kinetic Modeling', year=1982, editors='E.D. Torr',
                           edition='2nd Edn.', chapter_title='Updated Rates', publication_place='New York NY',
                           isbn='978-3-16-148410-0', url='url.com')
        with self.assertRaises(ValidationError):
            # no volume for an article
            LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kinetics of', year=2020,
                           journal='Int. J.', issue=2, page_start=2222, page_end=2229, doi='10.67/doi', url='url.com')
        with self.assertRaises(ValidationError):
            # wrong volume
            LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kin of', year=2020, journal='I. J.',
                           volume=-50, issue=2, page_start=2222, page_end=2229, doi='10.67/doi', url='url.com')
        with self.assertRaises(ValidationError):
            # wrong issue
            LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kin of', year=2020, journal='I. J.',
                           volume=50, issue=-2, page_start=2222, page_end=2229, doi='10.67/doi', url='url.com')
        with self.assertRaises(ValidationError):
            # no page_start for an article
            LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kinetics of', year=2020,
                           journal='Int. J.', volume=50, issue=2, page_end=2229, doi='10.67/doi', url='url.com')
        with self.assertRaises(ValidationError):
            # wrong page_start
            LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kin of', year=2020, journal='I. J.',
                           volume=50, issue=2, page_start=-1, page_end=2229, doi='10.67/doi', url='url.com')
        with self.assertRaises(ValidationError):
            # no page_end for an article
            LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kinetics of', year=2020,
                           journal='Int. J.', volume=50, issue=2, page_start=2222, doi='10.67/doi', url='url.com')
        with self.assertRaises(ValidationError):
            # wrong page_end
            LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kin of', year=2020, journal='I. J.',
                           volume=50, issue=2, page_start=2222, page_end=-6, doi='10.67/doi', url='url.com')
        with self.assertRaises(ValidationError):
            # page_end lower than page_start
            LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kin of', year=2020, journal='I. J.',
                           volume=50, issue=2, page_start=2222, page_end=1222, doi='10.67/doi', url='url.com')
        with self.assertRaises(ValidationError):
            # no editors for a book
            LiteratureBase(type='book', authors='M.I. It', title='Kinetic Modeling', year=1982, publisher='Wee-Ly',
                           edition='2nd Edn.', chapter_title='Updated Rates', publication_place='New York NY',
                           isbn='978-3-16-148410-0', url='url.com')
        with self.assertRaises(ValidationError):
            # long editions for a book
            LiteratureBase(type='book', authors='M.I. It', title='Kinetic Modeling', year=1982, publisher='Wee-Ly',
                           edition='2nd Edn. 2nd Edn. 2nd Edn. 2nd Edn. 2nd Edn. 2nd Edn. ',
                           chapter_title='Updated Rates', publication_place='New York NY',
                           isbn='978-3-16-148410-0', url='url.com', editors='E.D. Torr')
        with self.assertRaises(ValidationError):
            # no publication_place for a book
            LiteratureBase(type='book', authors='M.I. It', title='Kinetic Modeling', year=1982, publisher='Wee-Ly',
                           edition='2nd Edn.', chapter_title='Updated Rates', editors='E.D. Torr',
                           isbn='978-3-16-148410-0', url='url.com')
        with self.assertRaises(ValidationError):
            # no advisor for a thesis
            LiteratureBase(type='thesis', authors='P.H. David', title='Kinetic Modeling Dissertation', year=2020,
                           publisher='MIT', url='u.rl.com/dissertation/abstract')
        with self.assertRaises(ValidationError):
            # no doi for an article
            LiteratureBase(type='article', authors='M.I. It, D.C. Wash', title='Kinetics of', year=2020,
                           journal='Int. J.', volume=50, issue=2, page_start=2222, page_end=2229, url='url.com')
        with self.assertRaises(ValidationError):
            # no isbn for a book
            LiteratureBase(type='book', authors='M.I. It, D.C. Wash', title='Principles of Kinetic Modeling', year=1982,
                           publisher='Wee-Ly', editors='E.D. Torr', edition='2nd Edn.', chapter_title='Updated Rates',
                           publication_place='New York NY', url='u.rl.com/book/abstract')
        with self.assertRaises(ValidationError):
            # no url
            LiteratureBase(type='book', authors='M.I. It, D.C. Wash', title='Principles of Kinetic Modeling', year=1982,
                           publisher='Wee-Ly', editors='E.D. Torr', edition='2nd Edn.', chapter_title='Updated Rates',
                           publication_place='New York NY', isbn='978-3-16-148410-0')
        with self.assertRaises(ValidationError):
            # wrong url
            LiteratureBase(type='book', authors='M.I. It, D.C. Wash', title='Principles of Kinetic Modeling', year=1982,
                           publisher='Wee-Ly', editors='E.D. Torr', edition='2nd Edn.', chapter_title='Updated Rates',
                           publication_place='New York NY', isbn='978-3-16-148410-0', url='u.rl.com book/abstract')
        with self.assertRaises(ValidationError):
            # wrong url
            LiteratureBase(type='book', authors='M.I. It, D.C. Wash', title='Principles of Kinetic Modeling', year=1982,
                           publisher='Wee-Ly', editors='E.D. Torr', edition='2nd Edn.', chapter_title='Updated Rates',
                           publication_place='New York NY', isbn='978-3-16-148410-0', url='u-rl-com/book/abstract')
