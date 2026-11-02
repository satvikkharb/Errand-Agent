import threading

from supabase import Client, create_client

from app.config import get_settings

_local = threading.local()


def get_db() -> Client:
    """One client per thread. A client shared across threads drops ~4% of concurrent calls
    (httpx RemoteProtocolError), and FastAPI runs sync endpoints on a thread pool."""
    client = getattr(_local, "client", None)
    if client is None:
        s = get_settings()
        client = _local.client = create_client(s.supabase_url, s.supabase_service_role_key)
    return client
