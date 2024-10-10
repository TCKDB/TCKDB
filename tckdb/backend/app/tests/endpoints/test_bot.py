from fastapi.testclient import TestClient
import pytest

from tckdb.backend.app.core.config import API_V1_STR
from tckdb.backend.app.models.bot import Bot as BotModel


@pytest.mark.usefixtures('setup_database')
class TestBotsEndpoints:
    """
    A class to test the bots endpoints
    """
    @pytest.fixture(scope='class', autouse=True)
    def setup_bot(self, request, client):
        """
        A function to setup a bot
        """
        response = client.post(
            f"{API_V1_STR}/bot/",
            json={
                "name": "test_bot",
                "version": "0.1",
                "url": "https://test_bot.com",
                "git_commit": "123456789012345678901234567890123456789a",
                "git_branch": "main"
            },
        )
        print("Response status code:", response.status_code)
        print("Response content:", response.text)
        assert response.status_code == 201, response.text
        data = response.json()
        print("Response data:", data)
        request.cls.bot_id = data["id"]
        request.cls.bot_data = data
        print("Created bot data: ", data)
    
    def get_bot_from_db(self, bot_id, db):
        """
        Helper method to fetch a bot from the database by ID.
        """
        return db.query(BotModel).filter(BotModel.id == bot_id).first()
    
    def test_create_bot(self):
        """
        Test the initial creation of the bot
        """
        assert self.bot_data["name"] == "test_bot"
        assert self.bot_data["version"] == "0.1"
        assert self.bot_data["url"] == "https://test_bot.com"
    
    def test_read_bot(self, client):
        """
        Test retrieving a bot by its ID
        """
        bot_id = self.bot_id
        response = client.get(f"{API_V1_STR}/bot/{bot_id}")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["name"] == "test_bot"
        assert data["version"] == "0.1"
    
    def test_update_bot_partial(self, client):
        """
        Test partial updating the bot's attributes
        """
        bot_id = self.bot_id
        response = client.patch(
            f"{API_V1_STR}/bot/{bot_id}",
            json={
                "name": "updated_bot",
                "version": "0.2",
                "url": "https://updated_bot.com",
            },
        )
        assert response.status_code == 200, response.text
        updated_data = response.json()
        assert updated_data["name"] == "updated_bot"
        assert updated_data["version"] == "0.2"
        assert updated_data["url"] == "https://updated_bot.com"
    
    def test_soft_delete_bot(self, client, db_session):
        """
        Test soft deleting the bot
        """
        bot_id = self.bot_id
        response = client.delete(f"{API_V1_STR}/bot/{bot_id}/soft")
        assert response.status_code == 200, response.text
        db_bot = self.get_bot_from_db(bot_id, db_session)
        assert db_bot is not None
        assert db_bot.deleted_at is not None

    def test_restore_bot(self, client, db_session):
        """
        Test restoring the bot
        """
        bot_id = self.bot_id
        response = client.post(f"{API_V1_STR}/bot/{bot_id}/restore")
        assert response.status_code == 200, response.text
        db_bot = self.get_bot_from_db(bot_id, db_session)
        assert db_bot is not None
        assert db_bot.deleted_at is None
        
        assert db_bot.name == "updated_bot"
        assert db_bot.version == "0.2"
        assert db_bot.url == "https://updated_bot.com"
        
    
