from sqlalchemy.orm import Query

class SoftDeleteQuery(Query):
    
    def __init__(self, entities, session=None):
        super().__init__(entities, session)
        self._with_deleted = False
        
    def with_deleted(self):
        """
        Inlcude soft deleted records in the query results
        """
        self._with_deleted = True
        return self
    
    def _get_filter(self):
        """
        Return the filter condition based on the 'deleted_at' field.
        Assumes that models have a 'deleted_at' field.
        """
        for desc in self.column_descriptions:
            model = desc['type']
            if hasattr(model, 'deleted_at'):
                if self._with_deleted:
                    return model.deleted_at.is_(None)
        return None
    
    def get(self, ident):
        """
        Get a record by its primary key
        """
        if not self._with_deleted:
            self.filter(self._get_filter())
        return super().get(ident)
    
    def __iter__(self):
        if not self._with_deleted:
            filter_condition = self._get_filter()
            if filter_condition is not None:
                self.filter(filter_condition)
        return super().__iter__()
