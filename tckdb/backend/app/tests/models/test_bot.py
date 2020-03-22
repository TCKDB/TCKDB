"""
TCKDB backend app tests models test_bot module
"""

from tckdb.backend.app.models.bot import Bot


def test_bot_model():
    """Test creating an instance of Author"""
    bot_1 = Bot(name='ARC', version='1.1.0', url='https://github.com/ReactionMechanismGenerator/ARC')
    assert bot_1.name == 'ARC'
    assert bot_1.version == '1.1.0'
    assert bot_1.url == 'https://github.com/ReactionMechanismGenerator/ARC'
    assert str(bot_1) == "<Bot(id=None, name='ARC', version='1.1.0', " \
                         "url='https://github.com/ReactionMechanismGenerator/ARC')>"
