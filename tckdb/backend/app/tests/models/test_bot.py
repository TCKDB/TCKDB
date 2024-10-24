"""
TCKDB backend app tests models test_bot module
"""

from tckdb.backend.app.models.bot import Bot


def test_bot_model():
    """Test creating an instance of Bot"""
    bot_1 = Bot(
        name="ARC",
        version="1.1.0",
        url="https://github.com/ReactionMechanismGenerator/ARC",
        git_hash="7ba4d74c73198c76c70742de8c254e075200a582",
        git_branch="master",
    )
    assert bot_1.name == "ARC"
    assert bot_1.version == "1.1.0"
    assert bot_1.url == "https://github.com/ReactionMechanismGenerator/ARC"
    assert bot_1.git_hash == "7ba4d74c73198c76c70742de8c254e075200a582"
    assert bot_1.git_branch == "master"
    assert (
        str(bot_1)
        == "<Bot(name='ARC', version='1.1.0', url='https://github.com/ReactionMechanismGenerator/ARC')>"
    )
    assert (
        repr(bot_1) == "<Bot(id=None, name='ARC', version='1.1.0', "
        "url='https://github.com/ReactionMechanismGenerator/ARC', "
        "git_hash='7ba4d74c73198c76c70742de8c254e075200a582', "
        "git_branch='master')>"
    )
