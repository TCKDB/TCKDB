"""
TCKDB backend app tests schemas test_author module
"""

import pytest
from pydantic import ValidationError

from tckdb.backend.app.schemas.bot import BotBase


def test_bot_schema():
    """Test creating an instance of Author"""
    bot1 = BotBase(name='ARC', version='1.1.0', url='https://github.com/ReactionMechanismGenerator/ARC')
    assert bot1.name == 'ARC'
    assert bot1.version == '1.1.0'
    assert bot1.url == 'https://github.com/ReactionMechanismGenerator/ARC'

    with pytest.raises(ValidationError):
        BotBase(name='ARC', version='1.1.0', url='https://github-com/ReactionMechanismGenerator/ARC')
    with pytest.raises(ValidationError):
        BotBase(name='ARC', version='1.1.0', url='https://github com/ReactionMechanismGenerator/ARC')
