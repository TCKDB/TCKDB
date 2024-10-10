from sqlalchemy.orm import Session
from tckdb.backend.app.models.literature import Literature
from tckdb.backend.app.models.author import Author
from tckdb.backend.app.schemas.literature import LiteratureCreate, LiteratureUpdate
from fastapi import HTTPException

def create_literature(db: Session, literature_data: LiteratureCreate):
    """Create a new literature entry and check for ISBN/DOI uniqueness."""
    # Check if ISBN exists for books
    if literature_data.type == 'book' and literature_data.isbn:
        existing_book = db.query(Literature).filter_by(isbn=literature_data.isbn).first()
        if existing_book:
            raise HTTPException(status_code=400, detail=f"Book with ISBN {literature_data.isbn} already exists.")
    
    # Check if DOI exists for articles
    if literature_data.type == 'article' and literature_data.doi:
        existing_article = db.query(Literature).filter_by(doi=literature_data.doi).first()
        if existing_article:
            raise HTTPException(status_code=400, detail=f"Article with DOI {literature_data.doi} already exists.")
    
    # Validate author IDs
    if literature_data.author_ids:
        for author_id in literature_data.author_ids:
            author = db.query(Author).get(author_id)
            if not author:
                raise HTTPException(status_code=400, detail=f"Author with ID {author_id} does not exist.")

    # Create the literature entry
    literature = Literature(**literature_data.dict(exclude={'authors', 'author_ids'}))

    # Add new authors
    if literature_data.authors:
        for author_data in literature_data.authors:
            author = Author(**author_data.dict())
            db.add(author)
            db.commit()
            db.refresh(author)
            literature.authors.append(author)

    db.add(literature)
    db.commit()
    db.refresh(literature)
    return literature


def get_literature_by_id(db: Session, literature_id: int):
    """Retrieve literature by its ID."""
    return db.query(Literature).filter(Literature.id == literature_id).first()


def update_literature(db: Session, literature_id: int, literature_data: LiteratureUpdate):
    """Update an existing literature entry."""
    db_literature = db.query(Literature).filter(Literature.id == literature_id).first()
    if not db_literature:
        raise HTTPException(status_code=404, detail="Literature not found")
    
    for key, value in literature_data.dict(exclude_unset=True).items():
        setattr(db_literature, key, value)
    
    db.commit()
    db.refresh(db_literature)
    return db_literature


def soft_delete_literature(db: Session, literature_id: int):
    """Delete literature by its ID."""
    db_literature = db.query(Literature).filter(Literature.id == literature_id).first()
    if not db_literature:
        raise HTTPException(status_code=404, detail="Literature not found")
    db_literature.soft_delete()
    db.commit()
    db.refresh(db_literature)

def restore_literature(db: Session, literature_id: int):
    """Restore a soft-deleted literature entry."""
    db_literature = db.query(Literature).with_deleted().filter(
        Literature.id == literature_id, 
        Literature.deleted_at.isnot(None)
    )
    if not db_literature:
        raise HTTPException(status_code=404, detail="Literature not found")
    db_literature.deleted_at = None
    db.commit()
    db.refresh(db_literature)
    return db_literature
