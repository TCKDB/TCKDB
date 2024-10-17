from urllib import response
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tckdb.backend.app.models.literature import Literature as LiteratureModel
from tckdb.backend.app.schemas.literature import LiteratureCreate, LiteratureUpdate
from tckdb.backend.app.core.config import API_V1_STR



@pytest.mark.usefixtures('setup_database')
class TestLiteratureEndpoints:
    """A class to test the literature endpoints."""
    
    @pytest.fixture(scope='class', autouse=True)
    def setup_literature(self, request, client):
        """Setup a literature entry."""
        
        article_data = {
            "type": "article",
            "authors": [{"first_name": "John", "last_name": "Doe"}, {"first_name": "Jane", "last_name": "Doe"}],
            "title": "Research Paper on FastAPI",
            "year": 2022,
            "journal": "FastAPI Journal",
            "volume": 10,
            "issue": 1,
            "page_start": 1,
            "page_end": 10,
            "doi": "10.1234/fake-doi",
            "url": "https://example.com/research-paper"
        }
        
        response = client.post(
            f"{API_V1_STR}/literature/",
            json=article_data
        )
        assert response.status_code == 201, response.text
        data = response.json()
        print("Created article data: ", data)
        request.cls.article_id = data["id"]
        request.cls.article_data = data
    
        book_data = {
            "type": "book",
            "authors": [{"first_name": "M.I.", "last_name": "It"}, {"first_name": "D.C", "last_name": "Wash"}],
            "title": "Principles of Kinetic Modeling",
            "year": 1982,
            "publisher": "Wee-Ly",
            "editors": "E.D. Torr",
            "chapter_title": "These are Updated Rates",
            "publication_place": "New York",
            "isbn": "978-3-16-148410-0",
            "url": "https://example.com/book"
        }
        
        response = client.post(
            f"{API_V1_STR}/literature/",
            json=book_data
        )
        assert response.status_code == 201, response.text
        data = response.json()
        request.cls.book_id = data["id"]
        request.cls.book_data = data
        
        thesis_data = {
            "type": "thesis",
            "authors": [{"first_name": "P.H.", "last_name": "David"}],
            "title": "Kinetic Modeling Dissertation",
            "year": 2020,
            "publisher": "MIT",
            "advisor": "Dr. X",
            "url": "https://example.com/thesis"
        }
        
        response = client.post(
            f"{API_V1_STR}/literature/",
            json=thesis_data
        )
        assert response.status_code == 201, response.text
        data = response.json()
        request.cls.thesis_id = data["id"]
        request.cls.thesis_data = data
    
    def get_literature_from_db(self, literature_id, db):
        """Helper method to fetch a literature entry from the database by ID."""
        
        return db.query(LiteratureModel).filter(LiteratureModel.id == literature_id).first()
    
    def test_create_literatures(self):
        """Test creating literature entries."""
        
        assert self.article_data["type"] == "article"
        assert self.article_data["authors"] == [{"id": 1, "first_name": "John", "last_name": "Doe"}, {"id": 2,"first_name": "Jane", "last_name": "Doe"}]
        assert self.article_data["title"] == "Research Paper on FastAPI"
        
        assert self.book_data["type"] == "book"
        assert self.book_data["authors"] == [{"id":3, "first_name": "M.I.", "last_name": "It"}, {"id": 4, "first_name": "D.C", "last_name": "Wash"}]
        assert self.book_data["title"] == "Principles of Kinetic Modeling"
        
        assert self.thesis_data["type"] == "thesis"
        assert self.thesis_data["authors"] == [{"id":5, "first_name": "P.H.", "last_name": "David"}]
        assert self.thesis_data["title"] == "Kinetic Modeling Dissertation"
    
    def test_read_literature(self, client):
        """
        Test retrieving a literature entry by its ID
        """
        
        # Read out article
        response_article = client.get(f"{API_V1_STR}/literature/{self.article_id}")
        assert response_article.status_code == 200, response_article.text
        data = response_article.json()
        assert data["type"] == "article"
        assert data["authors"] == [{'id': 1, 'first_name': 'John', 'last_name': 'Doe'}, {'id': 2, 'first_name': 'Jane', 'last_name': 'Doe'}]
        assert data["title"] == "Research Paper on FastAPI"
        assert data["journal"] == "FastAPI Journal"

        # Read out book
        response_book = client.get(f"{API_V1_STR}/literature/{self.book_id}")
        assert response_book.status_code == 200, response_book.text
        data = response_book.json()
        assert data["type"] == "book"
        assert data["authors"] == [{'id': 3, 'first_name': 'M.I.', 'last_name': 'It'}, {'id': 4, 'first_name': 'D.C', 'last_name': 'Wash'}]
        assert data["title"] == "Principles of Kinetic Modeling"
        assert data["publisher"] == "Wee-Ly"
        
        # Read out thesis
        response_thesis = client.get(f"{API_V1_STR}/literature/{self.thesis_id}")
        assert response_thesis.status_code == 200, response_thesis.text
        data = response_thesis.json()
        assert data["type"] == "thesis"
        assert data["authors"] == [{'id': 5, 'first_name': 'P.H.', 'last_name': 'David'}]
        assert data["title"] == "Kinetic Modeling Dissertation"
        assert data["advisor"] == "Dr. X"
        
        
    def test_update_literature_partial(self, client):
        """
        Test partial updating the literature entry's attributes
        """
        
        # Update article
        # Update the title and journal and add a new author
        response_article = client.patch(
            f"{API_V1_STR}/literature/{self.article_id}",
            json={
                "title": "Updated Research Paper on FastAPI",
                "journal": "Updated FastAPI Journal",
                "authors": [{"first_name": "John", "last_name": "Doe"}, {"first_name": "Jane", "last_name": "Doe"}, {"first_name": "John", "last_name": "Smith"}]
            },
        )
        assert response_article.status_code == 200, response_article.text
        updated_data = response_article.json()
        assert updated_data["title"] == "Updated Research Paper on FastAPI"
        assert updated_data["journal"] == "Updated FastAPI Journal"
        assert updated_data["authors"] == [{'id': 1, 'first_name': 'John', 'last_name': 'Doe'}, {'id': 2, 'first_name': 'Jane', 'last_name': 'Doe'}, {'id': 6, 'first_name': 'John', 'last_name': 'Smith'}]
        
        # Update book
        # Update the title and publisher and add a new author
        response_book = client.patch(
            f"{API_V1_STR}/literature/{self.book_id}",
            json={
                "title": "Updated Principles of Kinetic Modeling",
                "publisher": "Updated Wee-Ly",
                "authors": [{"first_name": "M.I.", "last_name": "It"}, {"first_name": "D.C", "last_name": "Wash"}, {"first_name": "J.P", "last_name": "Morgan"}]
            },
        )
        assert response_book.status_code == 200, response_book.text
        updated_data = response_book.json()
        assert updated_data["title"] == "Updated Principles of Kinetic Modeling"
        assert updated_data["publisher"] == "Updated Wee-Ly"
        assert updated_data["authors"] == [{'id': 3, 'first_name': 'M.I.', 'last_name': 'It'}, {'id': 4, 'first_name': 'D.C', 'last_name': 'Wash'}, {'id': 7, 'first_name': 'J.P', 'last_name': 'Morgan'}]
        
        # Update thesis
        # Update the title and advisor and add a new author
        response_thesis = client.patch(
            f"{API_V1_STR}/literature/{self.thesis_id}",
            json={
                "title": "Updated Kinetic Modeling Dissertation",
                "advisor": "Dr. Y",
                "authors": [{"first_name": "J.P", "last_name": "Morgan"}, {"first_name": "Jim", "last_name": "David"}]
            },
        )
        assert response_thesis.status_code == 200, response_thesis.text
        updated_data = response_thesis.json()
        assert updated_data["title"] == "Updated Kinetic Modeling Dissertation"
        assert updated_data["advisor"] == "Dr. Y"
        assert updated_data["authors"] == [{'id': 5, 'first_name': 'P.H.', 'last_name': 'David'}, {'id': 7, 'first_name': 'J.P', 'last_name': 'Morgan'}, {'id': 8, 'first_name': 'Jim', 'last_name': 'David'}]
    
    def test_soft_delete_literature(self, client, db_session):
        """
        Test soft deleting the literature entry
        """
        
        # Soft delete article
        response_article = client.delete(f"{API_V1_STR}/literature/{self.article_id}/soft")
        assert response_article.status_code == 200, response_article.text
        db_article = self.get_literature_from_db(self.article_id, db_session)
        assert db_article is not None
        assert db_article.deleted_at is not None
        
        # Soft delete book
        response_book = client.delete(f"{API_V1_STR}/literature/{self.book_id}/soft")
        assert response_book.status_code == 200, response_book.text
        db_book = self.get_literature_from_db(self.book_id, db_session)
        assert db_book is not None
        assert db_book.deleted_at is not None
        
        # Soft delete thesis
        response_thesis = client.delete(f"{API_V1_STR}/literature/{self.thesis_id}/soft")
        assert response_thesis.status_code == 200, response_thesis.text
        db_thesis = self.get_literature_from_db(self.thesis_id, db_session)
        assert db_thesis is not None
        assert db_thesis.deleted_at is not None
        
    def test_restore_literature(self, client, db_session):
        """
        Test restoring the literature entry
        """
            
        # Restore article
        response_article = client.post(f"{API_V1_STR}/literature/{self.article_id}/restore")
        assert response_article.status_code == 200, response_article.text
        db_article = self.get_literature_from_db(self.article_id, db_session)
        assert db_article is not None
        assert db_article.deleted_at is None
        
        # Restore book
        response_book = client.post(f"{API_V1_STR}/literature/{self.book_id}/restore")
        assert response_book.status_code == 200, response_book.text
        db_book = self.get_literature_from_db(self.book_id, db_session)
        assert db_book is not None
        assert db_book.deleted_at is None
        
        # Restore thesis
        response_thesis = client.post(f"{API_V1_STR}/literature/{self.thesis_id}/restore")
        assert response_thesis.status_code == 200, response_thesis.text
        db_thesis = self.get_literature_from_db(self.thesis_id, db_session)
        assert db_thesis is not None
        assert db_thesis.deleted_at is None
