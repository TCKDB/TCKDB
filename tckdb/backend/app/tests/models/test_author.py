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
        author_1 = Author(name='Ed Joe', email='ed.joe@mit.edu', affiliation='MIT')
        self.assertEqual(author_1.name, 'Ed Joe')
        self.assertEqual(author_1.email, 'ed.joe@mit.edu')
        self.assertEqual(author_1.affiliation, 'MIT')
