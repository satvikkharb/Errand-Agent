"""Regression: a Supabase client shared across threads drops concurrent calls (httpx RemoteProtocolError)."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.config import get_settings
from app.db import get_db, get_rest_db
from app.tools.supabase_store import SupabaseStore

needs_env = pytest.mark.skipif(not get_settings().supabase_url, reason="no Supabase settings")
needs_db = pytest.mark.skipif(os.environ.get("RUN_DB_TESTS") != "1", reason="RUN_DB_TESTS!=1")


@needs_env
def test_each_thread_gets_its_own_rest_client_and_reuses_it():
    here = get_rest_db()
    assert get_rest_db() is here
    other = []
    t = threading.Thread(target=lambda: other.append(get_rest_db()))
    t.start(); t.join()
    assert other[0] is not here


@needs_env
def test_stores_resolve_the_rest_client_per_thread(monkeypatch):
    monkeypatch.setattr("app.tools.supabase_store.get_db", get_rest_db)  # REST mode: a client per thread
    store = SupabaseStore()  # built on the main thread, used from another
    seen = []
    t = threading.Thread(target=lambda: seen.append(store.db))
    t.start(); t.join()
    assert seen[0] is not store.db


@needs_db
def test_concurrent_queries_through_one_store_do_not_fail():
    store = SupabaseStore()

    def work(_):
        return len(store.areas())

    with ThreadPoolExecutor(8) as pool:
        assert set(pool.map(work, range(80))) == {2}  # 80 overlapping calls, zero errors


@needs_env
def test_postgres_mode_shares_one_thread_safe_pool():
    from app.db import uses_postgres
    if not uses_postgres():
        pytest.skip("DB_ACCESS is not postgres")
    here = get_db()
    other = []
    t = threading.Thread(target=lambda: other.append(get_db()))
    t.start(); t.join()
    assert other[0] is here  # one pool for every thread; psycopg's pool does the locking


@needs_db
def test_concurrent_queries_through_the_pool_do_not_fail_or_mix_results():
    from app.db import uses_postgres
    if not uses_postgres():
        pytest.skip("DB_ACCESS is not postgres")
    db = get_db()

    def work(i):
        row = db.table("providers").select("id").order("id").limit(1).execute().data[0]["id"]
        n = len(db.table("slots").select("id").eq("provider_id", row).limit(5).execute().data)
        return row, n

    with ThreadPoolExecutor(max_workers=24) as ex:
        out = list(ex.map(work, range(200)))
    assert len(set(out)) == 1 and out[0][1] == 5
