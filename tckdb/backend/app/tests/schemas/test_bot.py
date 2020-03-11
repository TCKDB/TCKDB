"""
TCKDB backend app tests schemas test_author module
"""

import unittest

from pydantic import ValidationError

from tckdb.backend.app.schemas.bot import BotBase


class TestBotBaseSchema(unittest.TestCase):
    """
    Contains unit tests for the AuthorBase schema
    """

    def test_bot_model(self):
        """Test creating an instance of Author"""
        bot1 = BotBase(name='ARC', version='1.1.0', url='https://github.com/ReactionMechanismGenerator/ARC')
        self.assertEqual(bot1.name, 'ARC')
        self.assertEqual(bot1.version, '1.1.0')
        self.assertEqual(bot1.url, 'https://github.com/ReactionMechanismGenerator/ARC')

        with self.assertRaises(ValidationError):
            BotBase(name='ARC', version='1.1.0', url='https://github-com/ReactionMechanismGenerator/ARC')
        with self.assertRaises(ValidationError):
            BotBase(name='ARC', version='1.1.0', url='https://github com/ReactionMechanismGenerator/ARC')
