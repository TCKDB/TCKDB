"""
TCKDB backend app tests schema test_author module
"""

import unittest

from pydantic.error_wrappers import ValidationError

from tckdb.backend.app.schemas.author import AuthorBase


class TestAuthorBaseSchema(unittest.TestCase):
    """
    Contains unit tests for the AuthorBase schema
    """

    def test_author_model(self):
        """Test creating an instance of Author"""
        author_1 = AuthorBase(name='Ed Joe', email='ed.joe@mit.edu', affiliation='MIT')
        self.assertEqual(author_1.name, 'Ed Joe')
        self.assertEqual(author_1.email, 'ed.joe@mit.edu')
        self.assertEqual(author_1.affiliation, 'MIT')

        with self.assertRaises(ValidationError):
            AuthorBase(name='Ed Joe', email='not a valid email', affiliation='MIT')
