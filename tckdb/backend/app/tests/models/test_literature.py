"""
TCKDB backend app tests models test_literature module
"""

import unittest

from tckdb.backend.app.models.literature import Literature


class TestLiteratureModel(unittest.TestCase):
    """
    Contains unit tests for the Literature module
    """

    def test_literature_model(self):
        """Test creating an instance of Author"""
        lit1 = Literature(type='article',
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
        self.assertEqual(repr(lit1),
                         "<Literature(id=None, "
                         "type='article', "
                         "authors='M.I. It, D.C. Wash', "
                         "title='Kinetics of the Reactions in a Model: Part II', "
                         "year=2020, "
                         "journal='Int. J. Chem. Kin.', "
                         "volume=53, "
                         "issue=2, "
                         "page_start=2222, "
                         "page_end=2229, "
                         "doi='10.67/doi', "
                         "url='u.rl.com/article/abstract')>")
        self.assertEqual(str(lit1), 'M.I. It, D.C. Wash, "Kinetics of the Reactions in a Model: Part II", '
                                    'Int. J. Chem. Kin. 2020, 53(2), 2222-2229. doi: 10.67/doi')

        lit2 = Literature(type='book',
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
        self.assertEqual(repr(lit2),
                         "<Literature(id=None, "
                         "type='book', "
                         "authors='M.I. It, D.C. Wash', "
                         "title='Principles of Kinetic Modeling', "
                         "year=1982, "
                         "publisher='Wee-Ly', "
                         "editors='E.D. Torr', "
                         "edition='2nd Edn.', "
                         "chapter_title='These are Updated Rates', "
                         "publication_place='New York NY', "
                         "isbn='978-3-16-148410-0', "
                         "url='u.rl.com/book/abstract')>")
        self.assertEqual(str(lit2), 'M.I. It, D.C. Wash, "These are Updated Rates", '
                                    'in: E.D. Torr "Principles of Kinetic Modeling", 2nd Edn., Wee-Ly, '
                                    'New York NY 1982. ISBN: 978-3-16-148410-0')

        lit3 = Literature(type='thesis',
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
        self.assertEqual(repr(lit3),
                         "<Literature(id=None, "
                         "type='thesis', "
                         "authors='P.H. David', "
                         "title='Kinetic Modeling Dissertation', "
                         "year=2020, "
                         "publisher='MIT', "
                         "advisor='P.R. Fessor', "
                         "url='u.rl.com/dissertation/abstract')>")
        self.assertEqual(str(lit3), 'P.H. David, Dissertation title: "Kinetic Modeling Dissertation", '
                                    '2020, MIT, Advisor: P.R. Fessor. URL: u.rl.com/dissertation/abstract')

