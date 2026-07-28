from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from config import settings


UNCONFIGURED_DATABASE_TARGET = URL.create(
    "postgresql",
    host="configuration-required.invalid",
    port=5432,
    database="configuration_required",
)


def _database_engine_target():
    """Return a configured target or an intentionally unreachable sentinel."""
    return settings.database_url or UNCONFIGURED_DATABASE_TARGET

engine = create_engine(
    _database_engine_target(),
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency — database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.commit()
