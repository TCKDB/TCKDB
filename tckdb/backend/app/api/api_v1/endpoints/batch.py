

from typing import Dict, Any
from xml.dom import ValidationErr
from fastapi import APIRouter, Depends, HTTPException

from tckdb.backend.app.db.session import get_db
from tckdb.backend.app.schemas.batch import BatchUploadPayload

from tckdb.backend.app.models import Author as AuthorModel
from tckdb.backend.app.models import Literature as LiteratureModel
from tckdb.backend.app.models import Level as LevelModel
from tckdb.backend.app.models import Bot as BotModel
from tckdb.backend.app.models import ESS as ESSModel
from tckdb.backend.app.models import Freq as FrequencyScaleModel
from tckdb.backend.app.models import Species as SpeciesModel



router = APIRouter(
    tags=["batch"],
)

@router.post("/", summary="Upload a batch of data to the database.", response_model=Dict[str, Any])
def batch_upload(payload: BatchUploadPayload, db = Depends(get_db)):
    """
    Batch upload multiple related entities: Authors, Literature, Levels, Species, EnCorrs, Bots, ESS Entries, and Frequencies.
    
    Establishes relationships based on temporary IDs provided in the payload.    
    """

    temp_id_map : Dict[str, int] = {}
    
    try:
        with db.begin_nested():
            
            # 1. Process Authors
            for author_data in payload.authors:
                # Check if existing author
                existing_author = db.query(AuthorModel).filter(
                    AuthorModel.first_name == author_data.first_name.strip(),
                    AuthorModel.last_name == author_data.last_name.strip(),
                    AuthorModel.orcid == author_data.orcid
                ).first()
                
                if existing_author:
                    temp_id_map[author_data.connection_id] = existing_author.id
                else:
                    new_author = AuthorModel(
                        first_name=author_data.first_name.strip(),
                        last_name=author_data.last_name.strip(),
                        orcid=author_data.orcid
                    )
                    db.add(new_author)
                    db.flush() # flush to get the ID
                    temp_id_map[author_data.connection_id] = new_author.id
    
            # 2. Process Literature
            for literature_data in payload.literature:
                # Check if existing literature
                existing_literature = db.query(LiteratureModel).filter(
                    LiteratureModel.doi == literature_data.doi,
                    LiteratureModel.isbn == literature_data.isbn
                ).first()
                
                if existing_literature:
                    temp_id_map[literature_data.connection_id] = existing_literature.id
                else:
                    new_literature = LiteratureModel(**literature_data.dict(exclude={'author_connection_ids', 'connection_id'}))
                    db.add(new_literature)
                    db.flush()
                    temp_id_map[literature_data.connection_id] = new_literature.id
            
            # 3. Process Levels
            for level_data in payload.levels:
                # Check if existing level
                existing_level = db.query(LevelModel).filter(
                    LevelModel.method == level_data.method,
                    LevelModel.basis == level_data.basis,
                    LevelModel.auxiliary_basis == level_data.auxiliary_basis,
                    LevelModel.dispersion == level_data.dispersion,
                    LevelModel.grid == level_data.grid,
                    LevelModel.solvent == level_data.solvent,
                    LevelModel.solvation_method == level_data.solvation_method,
                    LevelModel.solvation_description == level_data.solvation_description,
                    LevelModel.level_arguments == level_data.level_arguments
                ).first()
                
                if existing_level:
                    temp_id_map[level_data.connection_id] = existing_level.id
                else:
                    new_level = LevelModel(**level_data.dict(exclude={'connection_id'}))
                    db.add(new_level)
                    db.flush()
                    temp_id_map[level_data.connection_id] = new_level.id
            
            # 4. Process Bots
            for bot_data in payload.bots:
                # Check if existing bot
                existing_bot = db.query(BotModel).filter(
                    BotModel.name == bot_data.name,
                    BotModel.version == bot_data.version,
                    BotModel.url == bot_data.url,
                    BotModel.git_hash == bot_data.git_hash,
                    BotModel.git_branch == bot_data.git_branch
                ).first()
                
                if existing_bot:
                    temp_id_map[bot_data.connection_id] = existing_bot.id
                else:
                    new_bot = BotModel(**bot_data.dict(exclude={'connection_id'}))
                    db.add(new_bot)
                    db.flush()
                    temp_id_map[bot_data.connection_id] = new_bot.id
            
            # 5. Process ESS
            for ess_data in payload.ess:
                # Check if existing ess
                existing_ess = db.query(ESSModel).filter(
                    ESSModel.name == ess_data.name,
                    ESSModel.version == ess_data.version,
                    ESSModel.revision == ess_data.revision,
                    ESSModel.url == ess_data.url
                ).first()
                
                if existing_ess:
                    temp_id_map[ess_data.connection_id] = existing_ess.id
                else:
                    new_ess = ESSModel(**ess_data.dict(exclude={'connection_id'}))
                    db.add(new_ess)
                    db.flush()
                    temp_id_map[ess_data.connection_id] = new_ess.id
            
            # 6. Process Frequencies Scale
            for freq_scale_data in payload.freq_scales:
                # Grab the connection level ID
                freq_level_connection = freq_scale_data.level_connection_id
                
                # Retrieve the level ID from the temp ID map
                freq_level_id = temp_id_map.get(freq_level_connection)
                
                if not freq_level_id:
                    raise HTTPException(status_code=400, detail=f"Level connection ID {freq_level_connection} not found for Encorr Data.")
                
                new_freq_scale = FrequencyScaleModel(
                    level_id=freq_level_id,
                    **freq_scale_data.dict(exclude={'level_connection_id', 'connection_id'})
                )
                db.add(new_freq_scale)
                db.flush()
                temp_id_map[freq_scale_data.connection_id] = new_freq_scale.id
                
                # 7. Process Species
                created_species = []
                for species_data in payload.species:
                    # Grab all connection IDS for Levels
                    level_connections = species_data.level_connections
                    opt_level_id = None
                    freq_level_id = None
                    scan_level_id = None
                    irc_level_id = None
                    sp_level_id = None
                    
                    if level_connections:
                        opt_level_id = temp_id_map.get(level_connections.opt)
                        freq_level_id = temp_id_map.get(level_connections.freq)
                        scan_level_id = temp_id_map.get(level_connections.scan)
                        irc_level_id = temp_id_map.get(level_connections.irc)
                        sp_level_id = temp_id_map.get(level_connections.sp)
                    
                    # Grab all connection IDS for ESS
                    ess_connections = species_data.ess_connections
                    opt_ess_id = None
                    freq_ess_id = None
                    scan_ess_id = None
                    irc_ess_id = None
                    sp_ess_id = None
                    
                    if ess_connections:
                        opt_ess_id = temp_id_map.get(ess_connections.opt)
                        freq_ess_id = temp_id_map.get(ess_connections.freq)
                        scan_ess_id = temp_id_map.get(ess_connections.scan)
                        irc_ess_id = temp_id_map.get(ess_connections.irc)
                        sp_ess_id = temp_id_map.get(ess_connections.sp)

                    # Gather other connections
                    literature_id = temp_id_map.get(species_data.literature_connection_id)
                    bot_id = temp_id_map.get(species_data.bot_connection_id)
                    encorr_id = temp_id_map.get(species_data.encorr_connection_id)
                    freq_scale_id = temp_id_map.get(species_data.freq_scale_connection_id)
                    
                    # Create the species object
                    new_species: SpeciesModel = SpeciesModel(
                        **species_data.dict(exclude={'level_connections', 'ess_connections', 'literature_connection_id',
                                                     'bot_connection_id', 'encorr_connection_id', 'freq_scale_connection_id',
                                                     'connection_id'})
                    )
                    
                    # Add the connections
                    new_species.opt_level_id = opt_level_id
                    new_species.freq_level_id = freq_level_id
                    new_species.scan_level_id = scan_level_id
                    new_species.irc_level_id = irc_level_id
                    new_species.sp_level_id = sp_level_id
                    
                    new_species.opt_ess_id = opt_ess_id
                    new_species.freq_ess_id = freq_ess_id
                    new_species.scan_ess_id = scan_ess_id
                    new_species.irc_ess_id = irc_ess_id
                    new_species.sp_ess_id = sp_ess_id
                    
                    new_species.literature_id = literature_id
                    new_species.bot_id = bot_id
                    new_species.encorr_id = encorr_id
                    new_species.freq_scale_id = freq_scale_id
                    
                    db.add(new_species)
                    db.flush()
                    created_species.append({
                        "id": new_species.id,
                    })
                    
                    
                
                
            
    except ValidationErr as ve:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Validation Error: {ve}") from ve
    except HTTPException as he:
        db.rollback()
        raise he
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {e}") from e
    
    return {"detail": "Batch upload successful.",
            "species": created_species}
