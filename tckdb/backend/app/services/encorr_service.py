from sqlalchemy.orm import Session
from sqlalchemy.exc import NoResultFound
from tckdb.backend.app.models.encorr import EnCorr
from tckdb.backend.app.models.level import Level
from tckdb.backend.app.schemas.encorr import EnCorrCreate, EnCorrUpdate
from tckdb.backend.app.schemas.level import LevelCreate

def get_or_create_level(db: Session, level_data: LevelCreate) -> Level:
    """
    Retrieves a Level object matching the provided data.
    If it does not exist, creates a new Level entry.

    Args:
        db (Session): The database session.
        level_data (LevelCreate): The Level data.

    Returns:
        Level: The existing or newly created Level object.
    """
    query = db.query(Level).filter_by(
        method=level_data.method,
        basis=level_data.basis,
        auxiliary_basis=level_data.auxiliary_basis,
        level_arguments=level_data.level_arguments,
        solvation_description=level_data.solvation_description
    )
    existing_level = query.first()
    if existing_level:
        return existing_level
    else:
        new_level = Level(**level_data.dict())
        db.add(new_level)
        db.commit()
        db.refresh(new_level)
        return new_level

def create_encorr(db: Session, encorr_data: EnCorrCreate) -> EnCorr:
    """
    Creates a new EnCorr entry, linking to existing Levels if they exist.

    Args:
        db (Session): The database session.
        encorr_data (EnCorrCreate): The EnCorr data.

    Returns:
        EnCorr: The created EnCorr object.
    """
    # Handle primary_level
    primary_level = get_or_create_level(db, encorr_data.primary_level)
    
    # Handle isodesmic_high_level if provided
    isodesmic_high_level = None
    if encorr_data.isodesmic_high_level:
        isodesmic_high_level = get_or_create_level(db, encorr_data.isodesmic_high_level)
    
    # Create EnCorr object
    encorr = EnCorr(
        supported_elements=encorr_data.supported_elements,
        energy_unit=encorr_data.energy_unit,
        aec=encorr_data.aec,
        bac=encorr_data.bac,
        isodesmic_reactions=encorr_data.isodesmic_reactions,
        reviewer_flags=encorr_data.reviewer_flags,
        primary_level=primary_level,
        isodesmic_high_level=isodesmic_high_level
    )
    
    db.add(encorr)
    db.commit()
    db.refresh(encorr)
    return encorr

def get_encorr_by_id(db: Session, encorr_id: int) -> EnCorr:
    """
    Retrieves an EnCorr entry by ID.

    Args:
        db (Session): The database session.
        encorr_id (int): The ID of the EnCorr entry.

    Returns:
        EnCorr | None: The EnCorr object or None if not found.
    """
    return db.query(EnCorr).filter(EnCorr.id == encorr_id).first()

def update_encorr(db: Session, encorr_id: int, encorr_data: EnCorrUpdate) -> EnCorr:
    """
    Updates an existing EnCorr entry.

    Args:
        db (Session): The database session.
        encorr_id (int): The ID of the EnCorr entry to update.
        encorr_data (EnCorrUpdate): The updated EnCorr data.

    Returns:
        EnCorr | None: The updated EnCorr object or None if not found.
    """
    encorr = get_encorr_by_id(db, encorr_id)
    if not encorr:
        raise ValueError("Energy correction not found")
    
    # Update fields
    for key, value in encorr_data.dict(exclude_unset=True).items():
        if key in ["primary_level", "isodesmic_high_level"]:
            # Handle Level updates
            if value is not None:
                level = get_or_create_level(db, value)
                setattr(encorr, key, level)
        else:
            setattr(encorr, key, value)
    
    db.commit()
    db.refresh(encorr)
    return encorr

def soft_delete_encorr(db: Session, encorr_id: int):
    """
    Soft deletes an EnCorr entry by setting a 'deleted' flag.

    Args:
        db (Session): The database session.
        encorr_id (int): The ID of the EnCorr entry to delete.

    Raises:
        ValueError: If the EnCorr entry is not found.
    """
    encorr = get_encorr_by_id(db, encorr_id)
    if not encorr:
        raise ValueError("Energy correction not found")
    encorr.deleted = True  # Ensure 'deleted' column exists in the EnCorr model
    db.commit()

def _restore_encorr(db: Session, encorr_id: int) -> EnCorr:
    """
    Restores a soft-deleted EnCorr entry.

    Args:
        db (Session): The database session.
        encorr_id (int): The ID of the EnCorr entry to restore.

    Returns:
        EnCorr | None: The restored EnCorr object or None if not found or not deleted.
    """
    encorr = db.query(EnCorr).filter(EnCorr.id == encorr_id, EnCorr.deleted is True).first()
    if not encorr:
        return None
    encorr.deleted = False
    db.commit()
    db.refresh(encorr)
    return encorr
