"""Regression: a Supabase client shared across threads drops concurrent calls (httpx RemoteProtocolError)."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.config import get_settings
from app.db import get_db
from app.tools.supabase_store import SupabaseStore

needs_env = pytest.mark.skipif(not get_settings().supabase_url, reason="no Supabase settings")
needs_db = pytest.mark.skipif(os.environ.get("RUN_DB_TESTS") != "1", reason="RUN_DB_TESTS!=1")


@needs_env
def test_each_thread_gets_its_own_client_and_reuses_it():
    here = get_db()
    assert get_db() is here
    other = []
    t = threading.Thread(target=lambda: other.append(get_db()))
    t.start(); t.join()
    assert other[0] is not here


@needs_env
def test_stores_resolve_the_client_per_thread():
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
