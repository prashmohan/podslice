class RehostRouter:
    """
    A router to control all database operations on models in the
    rehost_app application.
    """
    route_app_labels = {'rehost_app'}

    def db_for_read(self, model, **hints):
        """
        Attempts to read rehost_app models go to rehost_db.
        """
        if model._meta.app_label in self.route_app_labels:
            return 'rehost_db'
        return None

    def db_for_write(self, model, **hints):
        """
        Attempts to write rehost_app models go to rehost_db.
        """
        if model._meta.app_label in self.route_app_labels:
            return 'rehost_db'
        return None

    def allow_relation(self, obj1, obj2, **hints):
        """
        Allow relations if a model in the rehost_app is involved.
        """
        if (
            obj1._meta.app_label in self.route_app_labels
            or obj2._meta.app_label in self.route_app_labels
        ):
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """
        Make sure the rehost_app only appears in the 'rehost_db' database.
        """
        if app_label in self.route_app_labels:
            return db == 'rehost_db'
        return None