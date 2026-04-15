"""
Database Connection and Session Management
"""
import logging
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from app.core.config import settings

logger = logging.getLogger(__name__)

# Prepare database URL
database_url = settings.DATABASE_URL

# Check if using SQLite (for development)
is_sqlite = database_url.startswith("sqlite")

# Remove sqlite+ prefix if present
if is_sqlite and "+" in database_url:
    database_url = database_url.split("+")[0] + database_url.split("+", 1)[1]

# Create engine with appropriate settings
if is_sqlite:
    # SQLite for development
    engine = create_engine(
        database_url,
        echo=settings.DEBUG,
        connect_args={
            "check_same_thread": False,
            "timeout": 30,
        },
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.close()
else:
    # PostgreSQL and other databases
    engine = create_engine(
        database_url,
        echo=settings.DEBUG,
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=settings.DATABASE_MAX_OVERFLOW,
        pool_pre_ping=True,
    )

# Create session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

# Base class for models
Base = declarative_base()


def init_db() -> None:
    """Initialize database"""
    try:
        # Import all models here to ensure they are registered
        from app.models import organization, user, meeting, transcript, action_item, mention
        
        # Create tables
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise


def close_db() -> None:
    """Close database connections"""
    try:
        engine.dispose()
        logger.info("Database connections closed")
    except Exception as e:
        logger.error(f"Error closing database: {e}")


def get_db():
    """Dependency for getting database sessions"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
