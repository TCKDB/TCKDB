# This code should be under the api folder, not in the model folder

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from model_example.model import Model
from model_example.schema import ModelCreate, ModelPatchSchema, ModelSchema, ModelUpdateSchema
from sqlalchemy.orm import Session

from tckdb.backend.app.db.session import get_db


router = APIRouter(
    tags=["model"],
)

@router.post("/", response_model=ModelSchema, status_code=status.HTTP_201_CREATED)
def create_bot(model_data: ModelCreate, db: Session = Depends(get_db)):
    """
    Create a new bot entry in the database
    """
    new_model = Model.create(model_data, db)
    return new_model.to_client_response()


@router.get("/{bot_id}", response_model=ModelSchema)
def read_bot(model_id: int, db: Session = Depends(get_db)):
    """
    Retrieve a bot by its ID
    """
    model = Model.read(model_id, db)
    return model.to_client_response()


@router.get("/", response_model=List[ModelSchema])
def read_bots_list(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """
    Retrieve a list of bots
    """


@router.put("/{bot_id}", response_model=ModelSchema)
def update_bot(bot_id: int, bot: ModelUpdateSchema, db: Session = Depends(get_db)):
    """
    Update a bot by its ID
    """


@router.patch("/{bot_id}", response_model=ModelSchema)
def partial_update_bot(bot_id: int, bot: ModelPatchSchema, db: Session = Depends(get_db)):
    """
    Partially update a bot by its ID
    """


@router.delete("/{bot_id}/hard", response_model=dict)
def delete_bot_hard(bot_id: int, db: Session = Depends(get_db)):
    """
    Delete a bot by its ID
    """


@router.delete("/{bot_id}/soft", response_model=dict)
def delete_bot_soft(bot_id: int, db: Session = Depends(get_db)):
    """
    Soft delete a bot by its ID
    """


@router.post("/{bot_id}/restore", response_model=ModelSchema)
def restore_bot(
    bot_id: int,
    db: Session = Depends(get_db),
    # user=Depends(required_roles(["admin"])) # TODO: To be implemented later
):
    """
    Restore a bot by its ID
    """
    db_bot = (
        db.query(BotModel)
        .with_deleted()
        .filter(BotModel.id == bot_id, BotModel.deleted_at.isnot(None))
        .first()
    )
    if db_bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    db_bot.deleted_at = None
    db.commit()
    db.refresh(db_bot)
    return db_bot
