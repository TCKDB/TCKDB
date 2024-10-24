"""
TCKDB backend app tests schemas test_bot module
"""

import pytest
from pydantic import ValidationError

from tckdb.backend.app.schemas.bot import BotBase


def test_bot_schema():
    """Test creating an instance of Bot"""
    bot1 = BotBase(
        name="ARC",
        version="1.1.0",
        url="https://github.com/ReactionMechanismGenerator/ARC",
        git_hash="7ba4d74c73198c76c70742de8c254e075200a582",
        git_branch="master",
    )
    assert bot1.name == "ARC"
    assert bot1.version == "1.1.0"
    assert bot1.url == "https://github.com/ReactionMechanismGenerator/ARC"
    assert bot1.git_hash == "7ba4d74c73198c76c70742de8c254e075200a582"
    assert bot1.git_branch == "master"

    with pytest.raises(ValidationError):
        # wrong url (no .)
        BotBase(
            name="ARC",
            version="1.1.0",
            url="https://github-com/ReactionMechanismGenerator/ARC",
        )
    with pytest.raises(ValidationError):
        # wrong url (space)
        BotBase(
            name="ARC",
            version="1.1.0",
            url="https://github.com ReactionMechanismGenerator/ARC",
        )
    with pytest.raises(ValidationError):
        # wrong git commit (not alphanumeric)
        BotBase(
            name="ARC",
            version="1.1.0",
            url="https://github.com/ReactionMechanismGenerator/ARC",
            git_hash="-7ba4d74c73198c76c70742de8c254e075200a582",
            git_branch="master",
        )
