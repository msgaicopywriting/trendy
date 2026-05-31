import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from trendy.db import init_db, Base


@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    init_db(engine)
    return engine


@pytest.fixture
def db_session(test_engine):
    """
    Each test gets a clean transaction that is rolled back after the test.
    Uses nested transactions (SAVEPOINTs) so the schema persists but data is clean.
    """
    connection = test_engine.connect()
    transaction = connection.begin()

    # Bind a session to the connection so it participates in the transaction
    SessionLocal = sessionmaker(bind=connection, autoflush=False, autocommit=False)
    session = SessionLocal()

    # Begin a nested (SAVEPOINT) transaction so session.commit() doesn't commit to DB
    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, transaction):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()
