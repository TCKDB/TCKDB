
from typing import List

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


from tckdb.backend.app.models.author import Author
from tckdb.backend.app.models.literature import Literature
from tckdb.backend.app.schemas.literature import LiteratureCreate, LiteratureRead, LiteratureType, LiteratureUpdate
from fastapi import HTTPException, APIRouter, Depends

from tckdb.backend.app.core.config import API_V1_STR


def create_literature(literature_data: LiteratureCreate, db: Session):
    """
    Create a new literature entry and check for ISBN/DOI uniqueness.
    
    Args:
        literature_data (LiteratureCreate): The literature data to be added.
        db (Session): The database session.
    
    Returns:
        Literature: The created literature entry.
    """
    # 1. Check ISBN uniqueness for books
    if literature_data.type == LiteratureType.book and literature_data.isbn:
        existing_book = db.query(Literature).filter_by(isbn=literature_data.isbn).first()
        if existing_book:
            raise HTTPException(
                status_code=400, 
                detail=f"Book with ISBN {literature_data.isbn} already exists."
            )
    
    # 2. Check DOI uniqueness for articles
    if literature_data.type == LiteratureType.article and literature_data.doi:
        existing_article = db.query(Literature).filter_by(doi=literature_data.doi).first()
        if existing_article:
            raise HTTPException(
                status_code=400, 
                detail=f"Article with DOI {literature_data.doi} already exists."
            )
    
    # 3. Handle Authors: Check if they exist or create new ones
    author_instances: List[Author] = []
    for author_data in literature_data.authors:
        # Normalize names by stripping whitespace
        first_name = author_data.first_name.strip()
        last_name = author_data.last_name.strip()
        
        # Check if the author already exists
        existing_author = db.query(Author).filter_by(
            first_name=first_name,
            last_name=last_name
        ).first()
        
        if existing_author:
            author_instances.append(existing_author)
        else:
            # Create new Author
            new_author = Author(
                first_name=first_name,
                last_name=last_name
            )
            db.add(new_author)
            try:
                db.commit()  # Commit to assign an ID
                db.refresh(new_author)
                author_instances.append(new_author)
            except IntegrityError:
                db.rollback()
                raise HTTPException(
                    status_code=400, 
                    detail=f"Author {first_name} {last_name} could not be created due to a database error."
                )
    
    # 4. Create Literature Instance
    literature = Literature(
        type=literature_data.type,
        title=literature_data.title.strip(),
        year=literature_data.year,
        journal=literature_data.journal.strip() if literature_data.journal else None,
        volume=literature_data.volume,
        issue=literature_data.issue,
        page_start=literature_data.page_start,
        page_end=literature_data.page_end,
        doi=literature_data.doi.strip() if literature_data.doi else None,
        isbn=literature_data.isbn.strip() if literature_data.isbn else None,
        url=literature_data.url.strip() if literature_data.url else None,
        publisher=literature_data.publisher.strip() if literature_data.publisher else None,
        editors=literature_data.editors.strip() if literature_data.editors else None,
        edition=literature_data.edition.strip() if literature_data.edition else None,
        chapter_title=literature_data.chapter_title.strip() if literature_data.chapter_title else None,
        publication_place=literature_data.publication_place.strip() if literature_data.publication_place else None,
        advisor=literature_data.advisor.strip() if literature_data.advisor else None,
        authors=author_instances  # SQLAlchemy Author instances
    )
    
    # 5. Add to Session and Commit
    db.add(literature)
    try:
        db.commit()
        db.refresh(literature)
    except IntegrityError as e:
        db.rollback()
        # Determine if the error is due to unique constraints
        if 'unique constraint' in str(e.orig).lower():
            raise HTTPException(
                status_code=400, 
                detail="Duplicate ISBN or DOI detected."
            )
        else:
            raise HTTPException(
                status_code=500, 
                detail="Internal server error."
            )
    
    # 6. Optional Debugging Print
    print(f"Created literature: {literature}")
    
    return literature


def get_literature_by_id(db: Session, literature_id: int):
    """Retrieve literature by its ID."""
    return db.query(Literature).filter(Literature.id == literature_id).first()


def update_literature(db: Session, literature_id: int, literature_data: LiteratureUpdate):
    """Update an existing literature entry."""
    db_literature = db.query(Literature).filter(Literature.id == literature_id).first()
    if not db_literature:
        raise HTTPException(status_code=404, detail="Literature not found")
    
    # Update other fields
    update_data = literature_data.dict(exclude_unset=True, exclude={'authors'})
    for key, value in update_data.items():
        setattr(db_literature, key, value)
    
    # Handle adding new authors
    if literature_data.authors:
        # Create a set of tuples containing existing authors' first and last names for quick lookup
        existing_authors = {(author.first_name.lower(), author.last_name.lower()) for author in db_literature.authors}

        for author_data in literature_data.authors:
            author_tuple = (author_data.first_name.strip().lower(), author_data.last_name.strip().lower())

            if author_tuple in existing_authors:
                # Skip adding duplicate author
                continue

            # Check if the author already exists in the database
            existing_author = db.query(Author).filter(
                Author.first_name.ilike(author_data.first_name.strip()),
                Author.last_name.ilike(author_data.last_name.strip())
            ).first()

            if existing_author:
                # Associate existing author with the literature
                db_literature.authors.append(existing_author)
                existing_authors.add(author_tuple)  # Update the existing authors set
            else:
                # Create and add a new author
                new_author = Author(
                    first_name=author_data.first_name.strip(),
                    last_name=author_data.last_name.strip()
                )
                db.add(new_author)
                db_literature.authors.append(new_author)
                existing_authors.add(author_tuple)  # Update the existing authors set
    
    try:
        db.commit()
        db.refresh(db_literature)
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to update literature: {str(e)}")
    
    return db_literature


def soft_delete_literature(db: Session, literature_id: int):
    """Delete literature by its ID."""
    db_literature = db.query(Literature).filter(Literature.id == literature_id).first()
    if not db_literature:
        raise HTTPException(status_code=404, detail="Literature not found")
    db_literature.soft_delete()
    db.commit()
    db.refresh(db_literature)

def _restore_literature(db: Session, literature_id: int):
    """Restore a soft-deleted literature entry."""
    db_literature = db.query(Literature).with_deleted().filter(
        Literature.id == literature_id, 
        Literature.deleted_at.isnot(None)
    ).first()  # Fetch the actual instance

    if not db_literature:
        raise HTTPException(status_code=404, detail="Literature not found")

    db_literature.deleted_at = None  # Update the field
    db.commit()
    db.refresh(db_literature)
    return db_literature
