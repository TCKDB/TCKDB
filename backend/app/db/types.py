from sqlalchemy.types import UserDefinedType


class RDKitMol(UserDefinedType):
    """
    Allows SQLAlchemy to understand and generate the RDKIT cartridge column type (mol) that is used in the Postgres image
    Essentially when generating SQL, allows to declare specific columns as mol
    """

    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        return "mol"
