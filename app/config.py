"""Application configuration.

All configuration is driven by environment variables so that no secret ever
lives in the source tree and so that moving from local MySQL to a hosted
database (e.g. AWS RDS) later needs no code change — only a different
``DATABASE_URL`` in the environment.

A local ``.env`` file is loaded automatically when present (via
python-dotenv) purely as a developer convenience; in a deployed environment
the real environment variables take precedence.
"""

import os

from dotenv import load_dotenv

# Load variables from a local .env file if one exists. This never overrides
# variables that are already set in the real environment.
load_dotenv()


class BaseConfig:
    """Settings shared by every environment."""

    # Secret key comes only from the environment; never hard-coded.
    SECRET_KEY = os.environ.get("SECRET_KEY")

    # Disable the event system we do not use; it adds overhead and is
    # deprecated. The ORM itself is unaffected.
    SQLALCHEMY_TRACK_MODIFICATIONS = False


class DevConfig(BaseConfig):
    """Local development configuration.

    Uses the development database identified by ``DATABASE_URL``, e.g.
    ``mysql+pymysql://user:password@localhost:3306/sts``.
    """

    DEBUG = True
    # Fully env-configurable connection string. A later move to AWS RDS is
    # just a different DATABASE_URL — no code change required.
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")


class TestConfig(BaseConfig):
    """Automated-test configuration.

    Uses a SEPARATE MySQL test database identified by ``TEST_DATABASE_URL``
    (e.g. ``mysql+pymysql://user:password@localhost:3306/sts_test``) so that
    the development database is never touched by tests.
    """

    TESTING = True
    # Must come from TEST_DATABASE_URL — a different database than Dev.
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL")
