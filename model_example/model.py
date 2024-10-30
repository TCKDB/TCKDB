"""
TCKDB backend app models bot module
"""

from typing import List
from model_example.schema import ModelCreate, ModelCreateBatch, ModelSchema, ModelUpdate
from sqlalchemy import Column, Integer

from tckdb.backend.app.db.base_class import Base


class Model(Base):
    """
    A class for representing a TCKDB --Model-- item
    """

    id = Column(Integer, primary_key=True, index=True, nullable=False)

    def __str__(self) -> str:
        """
        A string representation from which the object can be reconstructed.
        """
        pass

    def __repr__(self) -> str:
        """
        A string representation from which the object can be reconstructed.
        """
        pass

    @classmethod
    def to_client_response():
        """
        Return the models data in a structure that can be sent to the client
        """
        pass

    @staticmethod
    def create(model_data: ModelCreate, db_session) -> 'Model':
        """
        Creat and insert a model to the db
        """
        new_model = Model()
        db_session.add(new_model)
        db_session.flush()
        return new_model

    @staticmethod
    def batch_create(model_data: ModelCreateBatch, db_session) -> List['Model']:
        """
        Creat and insert a model to the db
        """
        pass

    @staticmethod
    def read(uniqe_id: str) -> 'Model':
        """
        Query a model from the db (raise error if does not exist)
        """
        pass

    @staticmethod
    def update(model_data: ModelUpdate, db_session) -> 'Model':
        """
        Query a model from the db
        """
        pass

    @staticmethod
    def patch(model_data: ModelUpdate, db_session) -> 'Model':
        """
        Query a model from the db
        """
        pass

    @staticmethod
    def delete(uniqe_id: str, db_session) -> bool:
        """
        Query a model from the db
        """
        pass

    @staticmethod
    def exists(uniqe_id: str, db_session) -> bool:
        """
        Check if a model with given id already exists in the db
        """
        pass

    @staticmethod
    def from_schema(model_schema_object: ModelSchema, db_session) -> bool:
        """
        Check if a model with given id already exists in the db
        """
        pass


'''
Hey, I was trying to think about stuff that could make our work easier in the future and thought about having slightly different way to organize our logic regarding to the models and schema. You have more experience with these parts of the code so I would be happy to hear what you think :slightly_smiling_face: 

Overall the main guidelines are: 
1. Each model has a class that inherits the Base class
2. All logic related to that model is managed under that class with static or class method (depend on the use case)
3. We will have some basic common functionality in all models like: exists, read, create, update, patch, delete etc.
4. Each model has Schema thats used as type declaration and checking
5. `Model` and `Schema` files should be under the same model folder

Maybe?:
1. All models should inherit the `Audit` columns (so maybe we want to make it part of the base). another thought regarding that is maybe we want to explicitly add these columns to each model (not sure what I think is better)
2. `Model` and `Schema` files should be under the same model folder

Open Questions:
1. Does the schema has more functionality at the moment?
2. 
'''