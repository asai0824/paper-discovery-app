from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from .config import settings

# Supabase pooler URL の ?pgbouncer=true は psycopg2 非対応なので除去
_db_url = settings.database_url.replace("?pgbouncer=true", "")
_is_sqlite = _db_url.startswith("sqlite")

engine = create_engine(
    _db_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    **({"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10} if not _is_sqlite else {}),
)

# SQLiteのWALモード有効化（読み書き競合を大幅に軽減）
if _is_sqlite:
    @event.listens_for(engine, "connect")
    def set_wal_mode(dbapi_conn, connection_record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA busy_timeout=5000")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@contextmanager
def get_db() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
