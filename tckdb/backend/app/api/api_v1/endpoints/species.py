from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional

from tckdb.backend.app.schemas.species import Species, SpeciesCreate, SpeciesUpdate
from tckdb.backend.app.models.species import Species as SpeciesModel
from tckdb.backend.app.db.session import get_db

router = APIRouter(
    # prefix="/species",
    tags=["species"],
)

@router.post("/", response_model=Species, status_code=status.HTTP_201_CREATED)
def create_species(species: SpeciesCreate, db: Session = Depends(get_db)):
    """
    Create a new species entry in the database
    
    Args:
        species(SpeciesCreate): The species data to be added to the database
        db(Session): The database session. Defaults to Depends(get_db)
        
    Returns:
        Species: The created species object
    
    Raises:
        HTTPException: If the species already exists
    """
    
    if species.label:
        existing_species = db.query(SpeciesModel).filter(SpeciesModel.label == species.label).first()
        if existing_species:
            raise HTTPException(status_code=400, detail="Species already exists")
    db_species = SpeciesModel(**species.dict())
    db.add(db_species)
    db.commit()
    db.refresh(db_species)
    
    print(f"Species {db_species.label} created")
    print(f"Species: {db_species}")
    
    return db_species

@router.get("/{species_id}", response_model=Species)
def read_species(species_id: int, db: Session=Depends(get_db)):
    """
    Retrieve a species by its ID
    
    Args:
        species_id(int): The species ID
        db(Session): The database session. Defaults to Depends(get_db)
    
    Returns:
        Species: The species object
    
    Raises:
        HTTPException: If the species is not found
    
    """
    db_species = db.query(SpeciesModel).filter(SpeciesModel.id == species_id).first()
    if db_species is None:
        raise HTTPException(status_code=404, detail="Species not found")
    return db_species

@router.get("/", response_model=List[Species])
def read_species_list(skip: int = 0, 
                      limit: int = 100, 
                      label: Optional[str] = None,
                      smiles: Optional[str] = None,
                      inchi: Optional[str] = None,
                      db: Session = Depends(get_db)) -> List[Species]:
    """
    Retrieve a list of species with optional pagination
    
    Args:
        skip(int): The number of species to skip
        limit(int): The number of species to retrieve
        label(Optional[str]): Filter by species label
        smiles(Optional[str]): Filter by species SMILES
        inchi(Optional[str]): Filter by species InChI
        db(Session): The database session. Defaults to Depends(get_db)
        
    Returns:
        List[Species]: A list of species objects
    """
    query = db.query(SpeciesModel)
    if label:
        query = query.filter(SpeciesModel.label.ilike(f"%{label}%"))
    if smiles:
        query = query.filter(SpeciesModel.smiles.ilike(f"%{smiles}%"))
    if inchi:
        query = query.filter(SpeciesModel.inchi.ilike(f"%{inchi}%"))
    species = query.offset(skip).limit(limit).all()
    return species

@router.put("/{species_id}", response_model=Species)
def update_species(species_id: int, species: SpeciesCreate, db: Session = Depends(get_db)):
    """
    Update an existing species entry in the database
    
    Args:
        species_id(int): The species ID
        species(SpeciesCreate): The species data to be updated
        db(Session): The database session. Defaults to Depends(get_db)
        
    Returns:
        Species: The updated species object
        
    Raises:
        HTTPException: If the species is not found
    """
    db_species = db.query(SpeciesModel).filter(SpeciesModel.id == species_id).first()
    if not db_species:
        raise HTTPException(status_code=404, detail="Species not found")
    # Replace all fields
    for key, value in species.dict().items():
        setattr(db_species, key, value)
    db.commit()
    db.refresh(db_species)
    return db_species

@router.patch("/{species_id}", response_model=Species)
def partial_update_species(species_id: int, species: SpeciesUpdate, db: Session = Depends(get_db)):
    """
    Partially update an existing species entry in the database
    
    Args:
        species_id(int): The species ID
        species(SpeciesUpdate): The species data to be updated
        db(Session): The database session. Defaults to Depends(get_db)
    
    Returns:
        Species: The updated species object
    
    Raises:
        HTTPException: If the species is not found
    """
    db_species = db.query(SpeciesModel).filter(SpeciesModel.id == species_id).first()
    if not db_species:
        raise HTTPException(status_code=404, detail="Species not found")
    update_data = species.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_species, key, value)
    db.commit()
    db.refresh(db_species)
    return db_species


@router.delete("/{species_id}/hard", response_model=dict)
def delete_species_hard(species_id: int, db: Session = Depends(get_db)):
    """
    Permanently delete a species by its ID
    
    Args:
        species_id(int): The species ID
        db(Session): The database session. Defaults to Depends(get_db)
        
    Returns:
        dict: Confirmation message
    
    Raises:
        HTTPException: If the species is not found
    """
    db_species = db.query(SpeciesModel).filter(SpeciesModel.id == species_id).first()
    if db_species is None:
        raise HTTPException(status_code=404, detail="Species not found")
    db_species.hard_delete(db)
    db.commit()
    return {'detail': 'Species permanently deleted'}


@router.delete("/{species_id}/soft", response_model=dict)
def delete_species_soft(species_id: int, db: Session = Depends(get_db)):
    """
    Soft delete a species by its ID
    
    Args:
        species_id(int): The species ID
        db(Session): The database session. Defaults to Depends(get_db)
    
    Returns:
        dict: Confirmation message
    
    Raises:
        HTTPException: If the species is not found
    """
    db_species = db.query(SpeciesModel).filter(
        SpeciesModel.id == species_id,
        SpeciesModel.deleted_at.is_(None)
    ).first()
    if db_species is None:
        raise HTTPException(status_code=404, detail="Species not found")
    db_species.soft_delete()
    db.commit()
    db.refresh(db_species)
    return {'detail': 'Species soft deleted'}


@router.post("/{species_id}/restore", response_model=Species)
def restore_species(species_id: int, db: Session = Depends(get_db)):
    """
    Restore a soft deleted species by its ID
    
    Args:
        species_id(int): The species ID
        db(Session): The database session. Defaults to Depends(get_db)
    
    Returns:
        Species: The restored species object
        
    Raises:
        HTTPException: If the species is not found
    """
    db_species = db.query(SpeciesModel).with_deleted().filter(
        SpeciesModel.id == species_id,
        SpeciesModel.deleted_at.isnot(None)
    ).first()
    if db_species is None:
        raise HTTPException(status_code=404, detail="Species not found")
    db_species.deleted_at = None
    db.commit()
    db.refresh(db_species)
    return db_species
