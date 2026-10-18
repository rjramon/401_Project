"""Application package and ``create_app`` factory.

The factory wires together configuration, the single SQLAlchemy instance, and
the route blueprints. It deliberately does NOT connect to, reset, or re-seed
the database. Schema creation and demo seeding are explicit, separate scripts
(see ``scripts/``), which is what lets saved results survive restarts
(Requirement 11.4).

``db.init_app(app)`` registers the extension with the app but does not open a
connection, so ``create_app`` can be constructed in tests without a live
database.
"""

import importlib

from . import extensions
from .config import BaseConfig
from .extensions import db


def create_app(config="app.config.DevConfig"):
    """Create and configure a Flask application.

    Args:
        config: Either a config class, or a dotted import path string such as
            ``"app.config.TestConfig"``. A string is resolved to the class so
            callers can select an environment by name.

    Returns:
        A configured Flask application. The database is bound (``init_app``)
        but no connection is opened and no data is reset or seeded.
    """
    # Imported here so that importing the package does not require Flask to be
    # installed just to read the version/metadata, and to keep the factory the
    # single place the app object is built.
    from flask import Flask

    app = Flask(__name__)

    # Resolve a dotted-path string ("app.config.TestConfig") to the class.
    config_obj = _resolve_config(config)
    app.config.from_object(config_obj)

    # Bind the one SQLAlchemy instance to this app. init_app does NOT connect
    # to the database; the first connection happens lazily on first query.
    #
    # Reference the instance via the ``extensions`` module (not a bare ``db``
    # name) on purpose: the sibling data-access module ``app/db.py`` is a
    # submodule of this same package, so importing it elsewhere rebinds the
    # package attribute ``app.db`` to that MODULE and would shadow a plain
    # ``db`` name here. Going through ``extensions.db`` is immune to that
    # collision and always resolves to the SQLAlchemy instance.
    extensions.db.init_app(app)

    # Import models for their side effect of registering tables on the shared
    # metadata. This is optional at this stage of the project: the factory
    # must remain importable before models.py exists (Task 2 adds it), so a
    # missing module is tolerated here rather than crashing import.
    try:
        importlib.import_module("app.models")
    except ModuleNotFoundError:
        # models.py arrives in a later task; the factory stays importable.
        pass

    _register_blueprints(app)

    return app


def _resolve_config(config):
    """Return a config class given either a class or a dotted-path string."""
    if isinstance(config, str):
        module_path, _, class_name = config.rpartition(".")
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    return config


def _register_blueprints(app):
    """Register route blueprints, tolerating modules that do not exist yet.

    The route modules (``app/routes/main.py`` and ``app/routes/orders.py``)
    are created in later tasks. Registering them defensively means the
    factory is importable and runnable now, and the real routes light up
    automatically once those modules are added.
    """
    for module_name, attr in (("app.routes.main", "bp"), ("app.routes.orders", "bp")):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            # Route module not created yet (later task); skip without crashing.
            continue
        blueprint = getattr(module, attr, None)
        if blueprint is not None:
            app.register_blueprint(blueprint)
