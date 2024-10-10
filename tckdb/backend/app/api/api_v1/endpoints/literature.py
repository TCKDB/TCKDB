from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from tckdb.backend.app.schemas.literature import LiteratureCreate, LiteratureUpdate, LiteratureOut
from tckdb.backend.app.db.session import get_db
from tckdb.backend.app.services.literature_service import create_literature, update_literature, get_literature_by_id, soft_delete_literature

router = APIRouter(
    tags=["literature"],
)

@router.post("/", response_model=LiteratureOut, status_code=status.HTTP_201_CREATED)
def create_new_literature(literature: LiteratureCreate, db: Session = Depends(get_db)):
    """
    API endpoint to create new literature
    
    Args:
        literature (LiteratureCreate): The literature data to be added
        db (Session): The database session (injected via Depends)
    
    Returns:
        LiteratureOut: The created literature entry
    """
    try:
        return create_literature(db=db, literature_data=literature)
    except IntegrityError as e:
        raise HTTPException(status_code=400, detail=f"Failed to create literature: {str(e)}")


@router.get("/{literature_id}", response_model=LiteratureOut)
def read_literature(literature_id: int, db: Session = Depends(get_db)):
    """
    Retrieve literature by ID.
    
    Args:
        literature_id (int): The literature ID
        db (Session): The database session
    
    Returns:
        LiteratureOut: The requested literature object
    """
    literature = get_literature_by_id(db=db, literature_id=literature_id)
    if literature is None:
        raise HTTPException(status_code=404, detail="Literature not found")
    return literature


@router.put("/{literature_id}", response_model=LiteratureOut)
def update_literature_entry(literature_id: int, literature: LiteratureUpdate, db: Session = Depends(get_db)):
    """
    Update an existing literature entry.
    
    Args:
        literature_id (int): The ID of the literature entry to update
        literature (LiteratureUpdate): The updated literature data
        db (Session): The database session
    
    Returns:
        LiteratureOut: The updated literature object
    """
    try:
        return update_literature(db=db, literature_id=literature_id, literature_data=literature)
    except IntegrityError as e:
        raise HTTPException(status_code=400, detail=f"Failed to update literature: {str(e)}")


@router.delete("/{literature_id}/soft", response_model=dict)
def delete_literature_soft(literature_id: int, db: Session = Depends(get_db)):
    """
    Delete an existing literature entry by ID.
    
    Args:
        literature_id (int): The ID of the literature entry to delete
        db (Session): The database session
    
    Returns:
        dict: Confirmation message on successful deletion
    """
    try:
        soft_delete_literature(db=db, literature_id=literature_id)
        return {"detail": "Literature soft deleted"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to delete literature: {str(e)}")

@router.post("/{literature_id}/restore", response_model=LiteratureOut)
def restore_literature(literature_id: int, db: Session = Depends(get_db)):
    """
    Restore a soft-deleted literature entry.
    
    Args:
        literature_id (int): The ID of the literature entry to restore
        db (Session): The database session
    
    Returns:
        LiteratureOut: The restored literature object
    """
    try:
        literature = restore_literature(db=db, literature_id=literature_id)
        return literature
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to restore literature: {str(e)}")
