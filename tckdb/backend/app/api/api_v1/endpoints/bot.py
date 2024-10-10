from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from tckdb.backend.app.db.session import get_db
from tckdb.backend.app.models.bot import Bot as BotModel
from tckdb.backend.app.schemas.bot import BotCreate, BotUpdate, BotOut

router = APIRouter(
    tags=["bot"],
)

@router.post("/", response_model=BotOut, status_code=status.HTTP_201_CREATED)
def create_bot(bot: BotCreate, db: Session = Depends(get_db)):
    """
    Create a new bot entry in the database
    
    Args:
        bot(BotCreate): The bot data to be added to the database
        db(Session): The database session. Defaults to Depends(get_db)
        
    Returns:
        Bot: The created bot object
    
    Raises:
        HTTPException: If the bot already exists
    """
    if bot.name:
        existing_bot = db.query(BotModel).filter(BotModel.name == bot.name).first()
        if existing_bot:
            raise HTTPException(status_code=400, detail="Bot already exists")
    db_bot = BotModel(**bot.dict())
    db.add(db_bot)
    db.commit()
    db.refresh(db_bot)
    return db_bot

@router.get("/{bot_id}", response_model=BotOut)
def read_bot(bot_id: int, db: Session=Depends(get_db)):
    """
    Retrieve a bot by its ID
    
    Args:
        bot_id(int): The bot ID
        db(Session): The database session. Defaults to Depends(get_db)
    
    Returns:
        Bot: The bot object
    
    Raises:
        HTTPException: If the bot is not found
    
    """
    db_bot = db.query(BotModel).filter(BotModel.id == bot_id).first()
    if db_bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    return db_bot

@router.get("/", response_model=List[BotOut])
def read_bots_list(skip: int = 0,
                   limit: int = 100,
                   db: Session = Depends(get_db)):
    """
    Retrieve a list of bots
    
    Args:
        skip(int): The number of entries to skip
        limit(int): The number of entries to retrieve
        db(Session): The database session. Defaults to Depends(get_db)
    
    Returns:
        List[Bot]: The list of bot objects
    
    """
    bots = db.query(BotModel).offset(skip).limit(limit).all()
    return bots

# @router.put("/{bot_id}", response_model=Bot)
# def update_bot(bot_id: int, bot: BotFullUpdate, db: Session=Depends(get_db)):
#     """
#     Update a bot by its ID
    
#     Args:
#         bot_id(int): The bot ID
#         bot(BotFullUpdate): The bot data to be fully updated
#         db(Session): The database session. Defaults to Depends(get_db)
    
#     Returns:
#         Bot: The updated bot object
    
#     Raises:
#         HTTPException: If the bot is not found
    
#     """
#     db_bot = db.query(BotModel).filter(BotModel.id == bot_id).first()
#     if db_bot is None:
#         raise HTTPException(status_code=404, detail="Bot not found")
#     for key, value in bot.dict().items():
#         setattr(db_bot, key, value)
#     db.add(db_bot)
#     db.commit()
#     db.refresh(db_bot)
#     return db_bot

@router.patch("/{bot_id}", response_model=BotOut)
def partial_update_bot(bot_id: int, bot: BotUpdate, db: Session=Depends(get_db)):
    """
    Partially update a bot by its ID
    
    Args:
        bot_id(int): The bot ID
        bot(BotPartialUpdate): The bot data to be partially updated
        db(Session): The database session. Defaults to Depends(get_db)
    
    Returns:
        Bot: The updated bot object
    
    Raises:
        HTTPException: If the bot is not found
    
    """
    db_bot = db.query(BotModel).filter(BotModel.id == bot_id).first()
    if db_bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    update_data = bot.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_bot, key, value)
    db.add(db_bot)
    db.commit()
    db.refresh(db_bot)
    return db_bot


@router.delete("/{bot_id}/hard", response_model=dict)
def delete_bot_hard(bot_id: int, db: Session=Depends(get_db)):
    """
    Delete a bot by its ID
    
    Args:
        bot_id(int): The bot ID
        db(Session): The database session. Defaults to Depends(get_db)
    
    Raises:
        HTTPException: If the bot is not found
    """
    db_bot = db.query(BotModel).filter(BotModel.id == bot_id).first()
    if db_bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    db_bot.hard_delete(db)
    db.commit()
    return {'detail': 'Bot permanently deleted'}

@router.delete("/{bot_id}/soft", response_model=dict)
def delete_bot_soft(bot_id: int, db: Session=Depends(get_db)):
    """
    Soft delete a bot by its ID
    
    Args:
        bot_id(int): The bot ID
        db(Session): The database session. Defaults to Depends(get_db)
    
    Raises:
        HTTPException: If the bot is not found
    """
    db_bot = db.query(BotModel).filter(BotModel.id == bot_id).first()
    if db_bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    db_bot.soft_delete()
    db.commit()
    db.refresh(db_bot)
    return {'detail': 'Bot soft deleted'}


@router.post("/{bot_id}/restore", response_model=BotOut)
def restore_bot(bot_id: int, db: Session=Depends(get_db),
                #user=Depends(required_roles(["admin"])) # TODO: To be implemented later
                ):
    """
    Restore a bot by its ID
    
    Args:
        bot_id(int): The bot ID
        db(Session): The database session. Defaults to Depends(get_db)
        user(User): The user object. Defaults to Depends(required_roles(["admin"]))
        
    Returns:
        Bot: The restored bot object
        
    Raises:
        HTTPException: If the bot is not found
    """
    db_bot = db.query(BotModel).with_deleted().filter(
        BotModel.id == bot_id,
        BotModel.deleted_at.isnot(None)
    ).first()
    if db_bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    db_bot.deleted_at = None
    db.commit()
    db.refresh(db_bot)
    return db_bot
