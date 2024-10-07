from sqlalchemy import JSON, Column, DateTime, Integer, String, func, event
from sqlalchemy.orm import Session

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.bot import Bot as BotModel
from tckdb.backend.app.models.species import Species as SpeciesModel


class AuditLog(Base):
    """
    Model to store audit logs
    """

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True, nullable=False)
    model = Column(String(50), nullable=False)  # eg. "bots", "species"
    model_id = Column(Integer, nullable=False)
    action = Column(String(50), nullable=False)  # eg. "create", "update", "delete"
    changes = Column(JSON, nullable=True)
    timestamp = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    performed_by = Column(String(50), nullable=True)  # eg. "user1", "bot2" #TODO: Implement this, then make it not nullable


def after_insert_listener(mapper, connection, target):
    """
    Listener for insert operations.
    """
    session = Session.object_session(target)
    audit = AuditLog(
        model=target.__tablename__,
        model_id=target.id,
        action="create",
        changes=None,
    )
    session.add(audit)


def after_update_listener(mapper, connection, target):
    """
    Listener for update operations.
    """
    session = Session.object_session(target)
    state = session.object_session(target)._inspect(target)
    changes = {}
    for attr in state.attrs:
        hist = attr.history
        if hist.has_changes():
            changes[attr.key] = {
                "old": hist.deleted[0] if hist.deleted else None,
                "new": hist.added[0] if hist.added else None,
            }
    if changes:
        audit = AuditLog(
            model=target.__tablename__,
            model_id=target.id,
            action="update",
            changes=changes,
        )
        session.add(audit)

def after_delete_listener(mapper, connection, target):
    """
    Listener for delete operations.
    Dtermines if the delection is soft or hard based on the presence of 'deleted_at'
    """
    session = Session.object_session(target)
    if hasattr(target, 'deleted_at') and target.deleted_at is not None:
        action = 'soft_delete'
    else:
        action = 'hard_delete'
    audit = AuditLog(
        model=target.__tablename__,
        model_id=target.id,
        action=action,
        changes=None,
    )
    session.add(audit)

    
# Register the listeners
for cls in [BotModel, SpeciesModel]:
    event.listen(cls, 'after_insert', after_insert_listener)
    event.listen(cls, 'after_update', after_update_listener)
    event.listen(cls, 'after_delete', after_delete_listener)