"""Shared Flask extensions.

The single SQLAlchemy instance lives here and nowhere else. It is created
once at import time and bound to the application inside ``create_app`` via
``db.init_app(app)``. Keeping it in its own module avoids circular imports
between the app factory, the models, and the data-access helpers.
"""

from flask_sqlalchemy import SQLAlchemy

# The one and only SQLAlchemy instance for the whole application.
# Created once here; bound to an app with db.init_app(app) in create_app().
db = SQLAlchemy()
