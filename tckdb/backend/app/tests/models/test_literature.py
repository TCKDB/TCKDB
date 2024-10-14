from click import edit
from tckdb.backend.app.models.literature import Literature
from tckdb.backend.app.models.author import Author

def test_literature_model():
    """
    Test creating a new literature entry
    """
    
    author1 = Author(first_name='M.I.', last_name='It')
    author2 = Author(first_name='D.C.', last_name='Wash')
    
    # Testing an Article
    lit1 = Literature(type='article',
                      authors=[author1, author2],  # List of authors
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
    assert lit1.authors == [author1, author2]
    assert lit1.title == 'Kinetics of the Reactions in a Model: Part II'
    assert lit1.year == 2020
    assert lit1.journal == 'Int. J. Chem. Kin.'
    assert lit1.volume == 53
    assert lit1.issue == 2
    assert lit1.page_start == 2222
    assert lit1.page_end == 2229
    assert lit1.doi == '10.67/doi'
    assert lit1.url == 'u.rl.com/article/abstract'
    assert repr(lit1) == f"<Literature(id=None, " \
                         f"type='article', " \
                         f"authors=[<Author(id=None, first_name='M.I.', last_name='It')>, " \
                         f"<Author(id=None, first_name='D.C.', last_name='Wash')>], " \
                         f"title='Kinetics of the Reactions in a Model: Part II', " \
                         f"year=2020, " \
                         f"journal='Int. J. Chem. Kin.', " \
                         f"volume=53, " \
                         f"issue=2, " \
                         f"page_start=2222, " \
                         f"page_end=2229, " \
                         f"doi='10.67/doi', " \
                         f"url='u.rl.com/article/abstract')>"
    assert str(lit1) == 'M.I. It, D.C. Wash, "Kinetics of the Reactions in a Model: Part II", ' \
                        'Int. J. Chem. Kin. 2020, 53(2), 2222-2229. doi: 10.67/doi'
    
    # Testing a Book
    lit2 = Literature(type='book',
                      authors=[author1, author2],
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
    assert lit2.authors == [author1, author2]
    assert lit2.title == 'Principles of Kinetic Modeling'
    assert lit2.year == 1982
    assert lit2.publisher == 'Wee-Ly'
    assert lit2.editors == 'E.D. Torr'
    assert lit2.edition == '2nd Edn.'
    assert lit2.chapter_title == 'These are Updated Rates'
    assert lit2.publication_place == 'New York NY'
    assert lit2.isbn == '978-3-16-148410-0'
    assert lit2.url == 'u.rl.com/book/abstract'
    assert repr(lit2) == f"<Literature(id=None, " \
                         f"type='book', " \
                         f"authors=[<Author(id=None, first_name='M.I.', last_name='It')>, " \
                         f"<Author(id=None, first_name='D.C.', last_name='Wash')>], " \
                         f"title='Principles of Kinetic Modeling', " \
                         f"year=1982, " \
                         f"publisher='Wee-Ly', " \
                         f"editors='E.D. Torr', " \
                         f"edition='2nd Edn.', " \
                         f"chapter_title='These are Updated Rates', " \
                         f"publication_place='New York NY', " \
                         f"isbn='978-3-16-148410-0', " \
                         f"url='u.rl.com/book/abstract')>"
    assert str(lit2) == 'M.I. It, D.C. Wash, "These are Updated Rates", ' \
                        'in: E.D. Torr "Principles of Kinetic Modeling", 2nd Edn., Wee-Ly, ' \
                        'New York NY 1982. ISBN: 978-3-16-148410-0'
                        
    # Testing a Thesis
    author3 = Author(first_name='P.H.', last_name='David')

    lit3 = Literature(type='thesis',
                      authors=[author3],
                      title='Kinetic Modeling Dissertation',
                      year=2020,
                      publisher='MIT',
                      advisor='P.R. Ofessor',
                      url='u.rl.com/dissertation/abstract',
                      )
    assert lit3.type == 'thesis'
    assert lit3.authors == [author3]
    assert lit3.title == 'Kinetic Modeling Dissertation'
    assert lit3.year == 2020
    assert lit3.publisher == 'MIT'
    assert lit3.advisor == 'P.R. Ofessor'
    assert lit3.url == 'u.rl.com/dissertation/abstract'
    assert repr(lit3) == f"<Literature(id=None, " \
                         f"type='thesis', " \
                         f"authors=[<Author(id=None, first_name='P.H.', last_name='David')>], " \
                         f"title='Kinetic Modeling Dissertation', " \
                         f"year=2020, " \
                         f"publisher='MIT', " \
                         f"advisor='P.R. Ofessor', " \
                         f"url='u.rl.com/dissertation/abstract')>"
    assert str(lit3) == 'P.H. David, Dissertation title: "Kinetic Modeling Dissertation", ' \
                        '2020, MIT, Advisor: P.R. Ofessor. URL: u.rl.com/dissertation/abstract'
