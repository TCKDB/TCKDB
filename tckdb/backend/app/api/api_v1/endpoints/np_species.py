from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tckdb.backend.app.db.session import get_db
from tckdb.backend.app.models.bot import Bot as BotModel
from tckdb.backend.app.models.np_species import (
    NonPhysicalSpecies as NonPhysicalSpeciesModel,
)
from tckdb.backend.app.schemas.np_species import (
    NonPhysicalSpeciesBase,
    NonPhysicalSpeciesCreate,
    NonPhysicalSpeciesRead,
    NonPhysicalSpeciesUpdate,
)

router = APIRouter(
    tags=["non-physical species"],
)


@router.post(
    "/", response_model=NonPhysicalSpeciesRead, status_code=status.HTTP_201_CREATED
)
def create_np_species(
    np_species: NonPhysicalSpeciesCreate, db: Session = Depends(get_db)
):
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
        existing_np_species = (
            db.query(NonPhysicalSpeciesModel)
            .filter(NonPhysicalSpeciesModel.label == np_species.label)
            .first()
        )
        if existing_np_species:
            raise HTTPException(
                status_code=400, detail="Non-physical species already exists"
            )

    bot = None
    if np_species.bot:
        bot_data = np_species.bot

        # Query for existing bot
        existing_bot = (
            db.query(BotModel)
            .filter(
                BotModel.name == bot_data.name, BotModel.version == bot_data.version
            )
            .first()
        )
        if existing_bot:
            bot = existing_bot
        else:
            # Create new bot
            bot = BotModel(**bot_data.model_dump())
            db.add(bot)
            try:
                db.commit()
                db.refresh(bot)
            except IntegrityError as e:
                db.rollback()
                # Attempt to fetch the bot again in case of concurrent creation
                existing_bot = (
                    db.query(BotModel)
                    .filter(
                        BotModel.name == bot_data.name,
                        BotModel.version == bot_data.version,
                    )
                    .first()
                )
                if not existing_bot:
                    raise HTTPException(
                        status_code=400, detail=f"Bot creation failed: {e}"
                    )

    np_species_data = np_species.dict(exclude={"bot"})
    if bot:
        db_np_species = NonPhysicalSpeciesModel(**np_species_data, bot_id=bot.id)
    else:
        db_np_species = NonPhysicalSpeciesModel(**np_species_data)
    db.add(db_np_species)
    try:
        db.commit()
        db.refresh(db_np_species)
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=400, detail=f"Failed to create non-physical species: {str(e)}"
        )
    return db_np_species


@router.get("/{np_species_id}", response_model=NonPhysicalSpeciesRead)
def read_np_species(np_species_id: int, db: Session = Depends(get_db)):
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
    db_np_species = (
        db.query(NonPhysicalSpeciesModel)
        .filter(NonPhysicalSpeciesModel.id == np_species_id)
        .first()
    )
    if db_np_species is None:
        raise HTTPException(status_code=404, detail="Non-physical species not found")
    return db_np_species


@router.get("/", response_model=List[NonPhysicalSpeciesRead])
def read_np_species_list(
    skip: int = 0,
    limit: int = 100,
    label: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi: Optional[str] = None,
    db: Session = Depends(get_db),
) -> List[NonPhysicalSpeciesBase]:
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


# @router.patch("/{np_species_id}", response_model=NonPhysicalSpeciesRead)
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


@router.patch("/{np_species_id}", response_model=NonPhysicalSpeciesRead)
def partial_update_non_physical_species(
    np_species_id: int,
    np_species: NonPhysicalSpeciesUpdate,
    db: Session = Depends(get_db),
):
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
    db_np_species = (
        db.query(NonPhysicalSpeciesModel)
        .filter(NonPhysicalSpeciesModel.id == np_species_id)
        .first()
    )
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
    db_np_species = (
        db.query(NonPhysicalSpeciesModel)
        .filter(NonPhysicalSpeciesModel.id == np_species_id)
        .first()
    )
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
    db_np_species = (
        db.query(NonPhysicalSpeciesModel)
        .filter(NonPhysicalSpeciesModel.id == np_species_id)
        .first()
    )
    if db_np_species is None:
        raise HTTPException(status_code=404, detail="Non-physical species not found")
    db_np_species.soft_delete()
    db.commit()
    return {"detail": "Non-physical species soft deleted"}


@router.post("/{np_species_id}/restore", response_model=NonPhysicalSpeciesRead)
def restore_np_species(np_species_id: int, db: Session = Depends(get_db)):
    """
    Restore a soft-deleted non-physical species by its ID
    """
    db_np_species = (
        db.query(NonPhysicalSpeciesModel)
        .with_deleted()
        .filter(
            NonPhysicalSpeciesModel.id == np_species_id,
            NonPhysicalSpeciesModel.deleted_at.isnot(None),
        )
        .first()
    )
    if db_np_species is None:
        raise HTTPException(status_code=404, detail="Non-physical species not found")
    db_np_species.deleted_at = None
    db.commit()
    db.refresh(db_np_species)
    return db_np_species
