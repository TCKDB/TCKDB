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
        author_1 = AuthorBase(name='I. B. Writing', email='email@dot.com', affiliation='Institution')
        self.assertEqual(author_1.name, 'I. B. Writing')
        self.assertEqual(author_1.email, 'email@dot.com')
        self.assertEqual(author_1.affiliation, 'Institution')

        with self.assertRaises(ValidationError):
            AuthorBase(name='I. B. Writing', email='not_a_valid.email', affiliation='Institution')
        with self.assertRaises(ValidationError):
            AuthorBase(name='I. B. Writing', email='not a.valid@email', affiliation='Institution')
        with self.assertRaises(ValidationError):
            AuthorBase(name='I. B. Writing', email='not.a@valid@email', affiliation='Institution')
        with self.assertRaises(ValidationError):
            AuthorBase(name='I. B. Writing', email='not_a_valid@email', affiliation='Institution')
        with self.assertRaises(ValidationError):
            AuthorBase(name='Writing', email='email@dot.com', affiliation='Institution')
