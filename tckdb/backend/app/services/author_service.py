from typing import Optional

from sqlalchemy.orm import Session

from tckdb.backend.app.models.author import Author as AuthorModel
from tckdb.backend.app.schemas.author import AuthorCreate


def get_or_create_author(
    db: Session, author_data: AuthorCreate
) -> AuthorModel:
    """
    Retrieves an existing author or creates a new one if not found.

    Args:
        db (Session): The database session.
        author_data (AuthorCreate): The author data.

    Returns:
        AuthorModel: The retrieved or created author.
    """
    query = db.query(AuthorModel).filter(
        AuthorModel.first_name.ilike(author_data.first_name),
        AuthorModel.last_name.ilike(author_data.last_name),
    )
    if author_data.orcid:
        query = query.filter(AuthorModel.orcid == author_data.orcid)
    author = query.first()
    if author:
        return author
    else:
        new_author = AuthorModel(
            first_name=author_data.first_name,
            last_name=author_data.last_name,
            orcid=author_data.orcid
        )
        db.add(new_author)
        db.flush()
        return new_author
