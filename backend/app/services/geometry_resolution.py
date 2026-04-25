from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.chemistry.geometry import parse_xyz
from app.db.models.geometry import Geometry, GeometryAtom
from app.schemas.entities.geometry import GeometryAtomBase, GeometryCreate
from app.schemas.fragments.geometry import GeometryPayload


def geometry_create_from_payload(payload: GeometryPayload) -> GeometryCreate:
    """Translate upload geometry data into a create schema.

    :param payload: Upload-facing geometry payload.
    :returns: ``GeometryCreate`` with canonicalized XYZ text, hash, and atoms.
    :raises ValueError: If the XYZ payload is malformed.
    """

    parsed = parse_xyz(payload)
    geom_hash = hashlib.sha256(parsed.canonical_xyz_text.encode("utf-8")).hexdigest()
    atoms = [
        GeometryAtomBase(atom_index=index, element=element, x=x, y=y, z=z)
        for index, (element, x, y, z) in enumerate(parsed.atoms, start=1)
    ]
    return GeometryCreate(
        natoms=parsed.natoms,
        geom_hash=geom_hash,
        xyz_text=parsed.canonical_xyz_text,
        atoms=atoms,
    )


def resolve_geometry_payload(session: Session, payload: GeometryPayload) -> Geometry:
    """Resolve or create a geometry row from uploaded XYZ text.

    :param session: Active SQLAlchemy session.
    :param payload: Upload-facing geometry payload.
    :returns: Existing or newly created ``Geometry`` row.
    :raises ValueError: If the XYZ payload is malformed.
    """

    geometry_create = geometry_create_from_payload(payload)

    geometry = session.scalar(
        select(Geometry).where(Geometry.geom_hash == geometry_create.geom_hash)
    )
    if geometry is None:
        try:
            with session.begin_nested():
                geometry = Geometry(
                    natoms=geometry_create.natoms,
                    geom_hash=geometry_create.geom_hash,
                    xyz_text=geometry_create.xyz_text,
                )
                session.add(geometry)
                session.flush()

                for atom in geometry_create.atoms:
                    session.add(
                        GeometryAtom(
                            geometry_id=geometry.id,
                            atom_index=atom.atom_index,
                            element=atom.element,
                            x=atom.x,
                            y=atom.y,
                            z=atom.z,
                        )
                    )

                session.flush()
        except IntegrityError:
            geometry = session.scalar(
                select(Geometry).where(Geometry.geom_hash == geometry_create.geom_hash)
            )

    return geometry
