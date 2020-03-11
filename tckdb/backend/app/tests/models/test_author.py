"""
TCKDB backend app tests models test_author module
"""

import unittest

from tckdb.backend.app.models.author import Author


class TestAuthorModel(unittest.TestCase):
    """
    Contains unit tests for the Author module
    """

    def test_author_model(self):
        """Test creating an instance of Author"""
        author_1 = Author(name='I. B. Writing', email='email@dot.com', affiliation='Institution')
        self.assertEqual(author_1.name, 'I. B. Writing')
        self.assertEqual(author_1.email, 'email@dot.com')
        self.assertEqual(author_1.affiliation, 'Institution')
        self.assertEqual((str(author_1)),
                         '<Author(id="None", name="I. B. Writing", email="email@dot.com", affiliation="Institution")>')
