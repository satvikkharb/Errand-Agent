"""Database access for the Supabase-backed stores.

Default is a direct, pooled Postgres connection (app/db_pg.py): one network round trip per query. Set
DB_ACCESS=rest to go through Supabase's REST API instead (one client per thread), or when no SUPABASE_DB_URL is set.
"""
import threading

from supabase import Client, create_client

from app.config import get_settings

_local = threading.local()


def get_rest_db() -> Client:
    """One REST client per thread. A client shared across threads drops ~4% of concurrent calls
    (httpx RemoteProtocolError), and FastAPI runs sync endpoints on a thread pool."""
    client = getattr(_local, "client", None)
    if client is None:
        s = get_settings()
        client = _local.client = create_client(s.supabase_url, s.supabase_service_role_key)
    return client


def uses_postgres() -> bool:
    s = get_settings()
    return s.db_access.lower() == "postgres" and bool(s.supabase_db_url)


def get_db():
    """The query surface the stores use: a shared connection pool (thread-safe) or a per-thread REST client."""
    if uses_postgres():
        from app.db_pg import get_pg

        s = get_settings()
        return get_pg(s.supabase_db_url, s.db_pool_min, s.db_pool_max)
    return get_rest_db()
