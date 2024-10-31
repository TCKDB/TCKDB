from typing import Optional

from sqlalchemy.orm import Session

from tckdb.backend.app.models.author import Author as AuthorModel


def get_or_create_author(
    db: Session, first_name: str, last_name: str, orcid: Optional[str] = None
) -> AuthorModel:
    """
    Retrieves an existing author or creates a new one if not found.

    Args:
        db (Session): The database session.
        first_name (str): First name of the author.
        last_name (str): Last name of the author.
        orcid (Optional[str]): ORCID of the author.

    Returns:
        AuthorModel: The retrieved or created author.
    """
    query = db.query(AuthorModel).filter(
        AuthorModel.first_name.ilike(first_name.strip()),
        AuthorModel.last_name.ilike(last_name.strip()),
    )
    if orcid:
        query = query.filter(AuthorModel.orcid == orcid)
    author = query.first()
    if author:
        return author
    else:
        new_author = AuthorModel(
            first_name=first_name.strip(), last_name=last_name.strip(), orcid=orcid
        )
        db.add(new_author)
        db.flush()
        return new_author
