"""
TCKDB backend app tests models test_bot module
"""

import unittest

from tckdb.backend.app.models.bot import Bot


class TestBotModel(unittest.TestCase):
    """
    Contains unit tests for the Author module
    """

    def test_bot_model(self):
        """Test creating an instance of Author"""
        bot_1 = Bot(name='ARC', version='1.1.0', url='https://github.com/ReactionMechanismGenerator/ARC')
        self.assertEqual(bot_1.name, 'ARC')
        self.assertEqual(bot_1.version, '1.1.0')
        self.assertEqual(bot_1.url, 'https://github.com/ReactionMechanismGenerator/ARC')
        self.assertEqual(str(bot_1),
                         "<Bot(id=None, name='ARC', version='1.1.0', "
                         "url='https://github.com/ReactionMechanismGenerator/ARC')>")
