from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional

from tckdb.backend.app.schemas.np_species import NonPhysicalSpeciesBase, NonPhysicalSpeciesCreate, NonPhysicalSpeciesUpdate, NonPhysicalSpeciesOut
from tckdb.backend.app.models.np_species import NonPhysicalSpecies as NonPhysicalSpeciesModel
from tckdb.backend.app.db.session import get_db

router = APIRouter(
    tags=["non-physical species"],
)

@router.post("/", response_model=NonPhysicalSpeciesOut, status_code=status.HTTP_201_CREATED)
def create_np_species(np_species: NonPhysicalSpeciesCreate, db: Session = Depends(get_db)):
    """
    Create a new non-physical species entry in the database
    
    Args:
        np_species(NonPhysicalSpeciesCreate): The non-physical species data to be added to the database
        db(Session): The database session. Defaults to Depends(get_db)
        
    Returns:
        NonPhysicalSpecies: The created non-physical species object
        
    Raises:
        HTTPException: If the non-physical species already exists
    """
    
    if np_species.label:
        existing_np_species = db.query(NonPhysicalSpeciesModel).filter(NonPhysicalSpeciesModel.label == np_species.label).first()
        if existing_np_species:
            raise HTTPException(status_code=400, detail="Non-physical species already exists")
    db_np_species = NonPhysicalSpeciesModel(**np_species.dict())
    db.add(db_np_species)
    db.commit()
    db.refresh(db_np_species)
    
    print(f"Non-physical species {db_np_species.label} created")
    print(f"Non-physical species: {db_np_species}")
    
    return db_np_species

@router.get("/{np_species_id}", response_model=NonPhysicalSpeciesOut)
def read_np_species(np_species_id: int, db: Session=Depends(get_db)):
    """
    Retrieve a non-physical species by its ID
    
    Args:
        np_species_id(int): The non-physical species ID
        db(Session): The database session. Defaults to Depends(get_db)
    
    Returns:
        NonPhysicalSpecies: The non-physical species object
    
    Raises:
        HTTPException: If the non-physical species is not found
    
    """
    db_np_species = db.query(NonPhysicalSpeciesModel).filter(NonPhysicalSpeciesModel.id == np_species_id).first()
    if db_np_species is None:
        raise HTTPException(status_code=404, detail="Non-physical species not found")
    return db_np_species

@router.get("/", response_model=List[NonPhysicalSpeciesOut])
def read_np_species_list(skip: int = 0,
                         limit: int = 100,
                         label: Optional[str] = None,
                         smiles: Optional[str] = None,
                         inchi: Optional[str] = None,
                         db: Session = Depends(get_db)) -> List[NonPhysicalSpeciesBase]:
    """
    Retrieve a list of non-physical species
    
    Args:
        skip(int): The number of species to skip
        limit(int): The maximum number of species to return
        db(Session): The database session. Defaults to Depends(get_db)
    
    Returns:
        List[NonPhysicalSpecies]: The list of non-physical species
    
    """
    np_species = db.query(NonPhysicalSpeciesModel).offset(skip).limit(limit).all()
    return np_species

# @router.patch("/{np_species_id}", response_model=NonPhysicalSpeciesOut)
# def update_non_physical_species(np_species_id: int, np_species: NonPhysicalSpeciesCreate, db: Session = Depends(get_db)):
#     """
#     Update a non-physical species by its ID
    
#     Args:
#         np_species_id(int): The non-physical species ID
#         np_species(NonPhysicalSpeciesUpdate): The updated non-physical species data
#         db(Session): The database session. Defaults to Depends(get_db)
    
#     Returns:
#         NonPhysicalSpecies: The updated non-physical species object
    
#     Raises:
#         HTTPException: If the non-physical species is not found
    
#     """
#     db_np_species = db.query(NonPhysicalSpeciesModel).filter(NonPhysicalSpeciesModel.id == np_species_id).first()
#     if db_np_species is None:
#         raise HTTPException(status_code=404, detail="Non-physical species not found")
#     for key, value in np_species.dict().items():
#         setattr(db_np_species, key, value)
#     db.commit()
#     db.refresh(db_np_species)
#     return db_np_species

@router.patch("/{np_species_id}", response_model=NonPhysicalSpeciesOut)
def partial_update_non_physical_species(np_species_id: int, np_species: NonPhysicalSpeciesUpdate, db: Session = Depends(get_db)):
    """
    Partially update a non-physical species by its ID
    
    Args:
        np_species_id(int): The non-physical species ID
        np_species(NonPhysicalSpeciesUpdate): The updated non-physical species data
        db(Session): The database session. Defaults to Depends(get_db)
    
    Returns:
        NonPhysicalSpecies: The updated non-physical species object
    
    Raises:
        HTTPException: If the non-physical species is not found
    
    """
    db_np_species = db.query(NonPhysicalSpeciesModel).filter(NonPhysicalSpeciesModel.id == np_species_id).first()
    if db_np_species is None:
        raise HTTPException(status_code=404, detail="Non-physical species not found")
    for key, value in np_species.dict(exclude_unset=True).items():
        setattr(db_np_species, key, value)
    db.commit()
    db.refresh(db_np_species)
    return db_np_species

@router.delete("/{np_species_id}/hard", response_model=dict)
def delete_np_species_hard(np_species_id: int, db: Session = Depends(get_db)):
    """
    Permanently delete a non-physical species by its ID
    """
    db_np_species = db.query(NonPhysicalSpeciesModel).filter(NonPhysicalSpeciesModel.id == np_species_id).first()
    if db_np_species is None:
        raise HTTPException(status_code=404, detail="Non-physical species not found")
    db_np_species.hard_delete(db)
    db.commit()
    return {"detail": "Non-physical species permanently deleted"}

@router.delete("/{np_species_id}/soft", response_model=dict)
def delete_np_species_soft(np_species_id: int, db: Session = Depends(get_db)):
    """
    Soft delete a non-physical species by its ID
    """
    db_np_species = db.query(NonPhysicalSpeciesModel).filter(NonPhysicalSpeciesModel.id == np_species_id).first()
    if db_np_species is None:
        raise HTTPException(status_code=404, detail="Non-physical species not found")
    db_np_species.soft_delete()
    db.commit()
    return {"detail": "Non-physical species soft deleted"}

@router.post("/{np_species_id}/restore", response_model=NonPhysicalSpeciesOut)
def restore_np_species(np_species_id: int, db: Session = Depends(get_db)):
    """
    Restore a soft-deleted non-physical species by its ID
    """
    db_np_species = db.query(NonPhysicalSpeciesModel).with_deleted().filter(
        NonPhysicalSpeciesModel.id == np_species_id,
        NonPhysicalSpeciesModel.deleted_at.isnot(None)
    ).first()
    if db_np_species is None:
        raise HTTPException(status_code=404, detail="Non-physical species not found")
    db_np_species.deleted_at = None
    db.commit()
    db.refresh(db_np_species)
    return db_np_species
