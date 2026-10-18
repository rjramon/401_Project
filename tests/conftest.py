"""Shared pytest fixtures for the Local Buy and Sell milestone.

These fixtures let the trading-service tests run against a **separate MySQL
test database** (``TEST_DATABASE_URL`` → e.g. ``sts_test``) and never touch
the development database (``DATABASE_URL`` → e.g. ``sts``). The split is the
whole point of this file (design: *Testing Strategy → Separate test
database*; Requirement 11.4).

What this module provides
-------------------------
* ``test_app``        — a Flask app built with ``TestConfig`` (session-scoped).
* ``_schema``         — creates the schema once in the TEST database from the
                        SQLAlchemy models, and drops it at the end.
* ``app_context``     — pushes an application context for a single test.
* ``reset_db``        — resets the TEST database to an empty schema before each
                        test (``scripts/reset_demo.py`` semantics: drop_all +
                        create_all), test DB only.
* ``db_session``      — the shared SQLAlchemy session
                        (``app.extensions.db.session``) on a freshly reset DB.
* ``seeded``          — a ``reset`` + ``scripts/seed_demo.seed`` state so a
                        test starts from the exact demo world (demo user,
                        account with $10,000.00, DEMO1 @ $50 / 1000 shares,
                        trainee starter position in DEMO2). Returns a small
                        namespace of the seeded records plus the session.

Robustness in environments with no MySQL
-----------------------------------------
A real MySQL test database may not be reachable (for example in CI without a
database service, or on a developer machine before ``sts_test`` is created).
Rather than erroring the whole suite, the DB-dependent fixtures **skip** the
tests that request them when the test database cannot be reached. Tests that
need no database (e.g. ``tests/test_trading_errors.py``) are unaffected and
keep passing.

No secrets are hard-coded here: the connection string comes entirely from the
``TEST_DATABASE_URL`` environment variable via ``app.config.TestConfig``.
"""

import os
import sys
from types import SimpleNamespace

import pytest

# Make the project root importable so ``app`` and ``scripts`` resolve when
# pytest is launched from the project root.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import create_app  # noqa: E402  (import after sys.path setup)
from app.extensions import db  # noqa: E402

# Reuse the seed logic from the demo script so tests and the real demo world
# stay in lock-step (same user, cash, stocks, and starter position).
from scripts.seed_demo import seed as seed_demo  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_test_database_url():
    """Return TEST_DATABASE_URL or skip the requesting test if it is missing.

    TestConfig reads this value; without it there is no test database to talk
    to, so DB-dependent tests are skipped rather than failing.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL is not set; set it to a SEPARATE MySQL test "
            "database (e.g. mysql+pymysql://user:pass@localhost:3306/sts_test) "
            "to run database-backed tests."
        )
    return url


def _assert_not_dev_database(app):
    """Fail loudly if the test DB URI accidentally equals the dev DB URI.

    This is a safety belt so a misconfigured environment can never let tests
    drop/recreate the development database. The dev and test databases MUST be
    different (design: Separate test database; Requirement 11.4).
    """
    test_uri = app.config.get("SQLALCHEMY_DATABASE_URI")
    dev_uri = os.environ.get("DATABASE_URL")
    if dev_uri and test_uri and test_uri == dev_uri:
        raise RuntimeError(
            "Refusing to run tests: TEST_DATABASE_URL equals DATABASE_URL. "
            "Point TEST_DATABASE_URL at a SEPARATE database (e.g. sts_test) "
            "so the development database is never reset by the test suite."
        )


def _can_connect(app):
    """Return True if the configured TEST database accepts a connection.

    Used to skip (not fail) DB-dependent tests when no MySQL test database is
    reachable in this environment. A short connect timeout is applied via a
    throwaway engine so an unreachable host fails fast instead of blocking the
    whole suite on a long TCP timeout.
    """
    from sqlalchemy import create_engine, text

    uri = app.config.get("SQLALCHEMY_DATABASE_URI")
    if not uri:
        return False

    # A small per-driver connect timeout keeps the probe quick. For the
    # pymysql driver this is the ``connect_timeout`` kwarg (seconds); other
    # drivers simply ignore unknown connect args or are retried by the caller.
    connect_args = {}
    if uri.startswith("mysql+pymysql"):
        connect_args = {"connect_timeout": 3}

    probe = create_engine(uri, connect_args=connect_args, pool_pre_ping=True)
    try:
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        probe.dispose()


# ---------------------------------------------------------------------------
# App + schema (session scoped)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_app():
    """A Flask app configured for testing against the SEPARATE test DB.

    Built with ``TestConfig`` so the connection string comes from
    ``TEST_DATABASE_URL``. The app is created once per test session. No
    database connection is opened merely by creating it.
    """
    _require_test_database_url()
    app = create_app("app.config.TestConfig")
    _assert_not_dev_database(app)
    return app


@pytest.fixture(scope="session")
def _schema(test_app):
    """Create the schema once in the TEST database; drop it at the end.

    Importing the models (done inside ``create_app``) registers all tables on
    the shared metadata; ``create_all`` builds them in the test database only.
    If the test database is unreachable, the whole set of DB-backed tests is
    skipped here with a clear message.
    """
    if not _can_connect(test_app):
        pytest.skip(
            "Test database is not reachable at TEST_DATABASE_URL. Start MySQL "
            "and create the test database (e.g. sts_test) to run "
            "database-backed tests."
        )

    with test_app.app_context():
        import app.models  # noqa: F401  (registers tables on db.metadata)

        db.create_all()

    yield test_app

    # Tear the schema down at the end of the session so the test database is
    # left clean. This only ever targets the TEST database.
    with test_app.app_context():
        db.session.remove()
        db.drop_all()


# ---------------------------------------------------------------------------
# Per-test context, reset, and session
# ---------------------------------------------------------------------------

@pytest.fixture
def app_context(_schema):
    """Push an application context for the duration of one test."""
    with _schema.app_context():
        yield _schema


@pytest.fixture
def reset_db(app_context):
    """Reset the TEST database to an empty schema before each test.

    This mirrors ``scripts/reset_demo.py`` semantics (drop_all + create_all)
    but targets ONLY the test database bound by the test app context. Starting
    each test from an empty, known schema keeps tests independent. The dev
    database is never touched.
    """
    db.session.remove()
    db.drop_all()
    db.create_all()
    yield
    # Leave a clean session for the next test; the next reset rebuilds state.
    db.session.remove()


@pytest.fixture
def db_session(reset_db):
    """The shared SQLAlchemy session on a freshly reset TEST database.

    This is ``app.extensions.db.session`` — the same session object the
    application and the data-access helpers use — so tests exercise the real
    machinery, not a parallel one.
    """
    return db.session


@pytest.fixture
def seeded(db_session):
    """A freshly reset TEST database loaded with the exact demo world.

    Reuses ``scripts/seed_demo.seed`` so the seeded state matches the real
    demo exactly:
        * one trainee ``User``;
        * one practice ``Account`` with $10,000.00 cash and starting balance;
        * stocks including DEMO1 @ $50.00 with 1,000 available shares;
        * a trainee starter ``Position`` in DEMO2 (a non-DEMO1 stock) so a
          Sell can be exercised immediately.

    Returns a ``SimpleNamespace`` with the seeded records and the session so
    trading-service tests can read trusted ids/values directly:
        ``session, user, account, stocks`` (dict by ticker), ``position``,
        and ``demo1`` / ``demo2`` convenience handles.
    """
    created, user, account, stocks_by_ticker, position = seed_demo(db_session)
    # The session is empty right before seeding (reset_db ran), so this always
    # creates fresh demo rows. Commit so the data is persisted and readable
    # back exactly as the application would see it.
    db_session.commit()

    return SimpleNamespace(
        session=db_session,
        created=created,
        user=user,
        account=account,
        stocks=stocks_by_ticker,
        position=position,
        demo1=stocks_by_ticker.get("DEMO1"),
        demo2=stocks_by_ticker.get("DEMO2"),
    )
