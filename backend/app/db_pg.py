"""Direct Postgres access with the same query surface the stores already use (the supabase-py / PostgREST builder).

Why: from India, every call through Supabase's REST gateway costs ~330 ms even though the database answers in
1 ms. A direct, pooled connection costs one network round trip (~60 ms to Mumbai). The stores keep their code:
`db.table("x").select("*").eq("id", 1).limit(1).execute().data` works exactly as before.

Safety:
  * every table/column name is validated and quoted, every value is a bound parameter (no string building);
  * update and delete without a filter are refused, so a bug cannot rewrite a whole table;
  * rows come back as JSON via to_jsonb(), so types match PostgREST (ISO timestamps, uuids as strings, numbers);
  * database errors are raised as postgrest APIError, so the existing `except APIError` handling keeps working.
"""
import logging
import re
import threading
from typing import Any

import psycopg
from postgrest.exceptions import APIError
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_JSON_KEY = re.compile(r"^[A-Za-z0-9_]+$")
_OPS = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "like": "like", "ilike": "ilike"}
_READ_RETRIES = 1  # a connection that went stale while idle: reads are safe to repeat once, writes are not


def _q(name: str) -> str:
    if not isinstance(name, str) or not _IDENT.match(name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return f'"{name}"'


def _col(name: str) -> str:
    """`payload->>user_id` (a key inside a jsonb column) or a plain column."""
    if "->>" in name:
        base, key = name.split("->>", 1)
        if not _JSON_KEY.match(key):
            raise ValueError(f"unsafe json key: {key!r}")
        return f"{_q(base)}->>'{key}'"
    return _q(name)


def _param(v: Any, col_type: str | None = None) -> Any:
    """Python value -> bindable parameter. A value going into a json/jsonb column is always a json document (so a
    plain string like "Indiranagar" is stored as the json string, as PostgREST does); a Postgres array stays an array."""
    if v is None:
        return None
    if col_type in ("jsonb", "json"):
        return Jsonb(v)
    if isinstance(v, list) and col_type == "ARRAY":
        return v
    return Jsonb(v) if isinstance(v, (dict, list)) else v


class Response:
    def __init__(self, data):
        self.data = data


def api_error(e: psycopg.Error) -> APIError:
    msg = (str(e).splitlines() or [""])[0]
    return APIError({"message": msg, "code": getattr(e, "sqlstate", None) or "", "hint": None, "details": None})


class Query:
    """Collects a query like the PostgREST builder does; execute() runs it."""

    def __init__(self, db: "PgDB", table: str):
        self.db, self.table, self.table_name = db, _q(table), table
        self.op, self.cols, self.rows, self.values = "select", "*", None, None
        self.conflict, self.ignore_dups = None, False
        self.where: list[tuple[str, list]] = []
        self._order: list[str] = []
        self._limit: int | None = None

    # ---- what to do
    def select(self, cols: str = "*", **_ignored):
        self.op = "select"
        self.cols = "*" if cols.strip() == "*" else ", ".join(_q(c.strip()) for c in cols.split(","))
        return self

    def insert(self, rows):
        self.op, self.rows = "insert", rows if isinstance(rows, list) else [rows]
        return self

    def upsert(self, rows, on_conflict: str | None = None, ignore_duplicates: bool = False, **_ignored):
        self.op, self.rows = "upsert", rows if isinstance(rows, list) else [rows]
        self.conflict, self.ignore_dups = on_conflict, ignore_duplicates
        return self

    def update(self, values: dict):
        self.op, self.values = "update", values
        return self

    def delete(self):
        self.op = "delete"
        return self

    # ---- filters
    def _add(self, col: str, op: str, value: Any):
        c = _col(col)
        if op == "is":
            if value in (None, "null"):
                self.where.append((f"{c} is null", []))
            elif value in (True, "true"):
                self.where.append((f"{c} is true", []))
            elif value in (False, "false"):
                self.where.append((f"{c} is false", []))
            else:
                raise ValueError(f"unsupported is value: {value!r}")
        elif op == "in":
            vals = list(value)
            self.where.append((f"{c} in ({', '.join(['%s'] * len(vals))})", vals) if vals else ("false", []))
        elif op in _OPS:
            self.where.append((f"{c} {_OPS[op]} %s", [value]))
        else:
            raise ValueError(f"unsupported operator: {op}")
        return self

    def eq(self, col, v): return self._add(col, "eq", v)
    def neq(self, col, v): return self._add(col, "neq", v)
    def gt(self, col, v): return self._add(col, "gt", v)
    def gte(self, col, v): return self._add(col, "gte", v)
    def lt(self, col, v): return self._add(col, "lt", v)
    def lte(self, col, v): return self._add(col, "lte", v)
    def like(self, col, v): return self._add(col, "like", v)
    def ilike(self, col, v): return self._add(col, "ilike", v)
    def is_(self, col, v): return self._add(col, "is", v)
    def in_(self, col, vals): return self._add(col, "in", vals)

    def filter(self, col: str, op: str, value: Any):
        return self._add(col, op, value)

    def or_(self, expr: str):
        """PostgREST syntax: 'hold_id.eq.h1,idempotency_key.eq.k1'."""
        parts, params = [], []
        for item in expr.split(","):
            col, op, value = item.split(".", 2)
            if op not in _OPS:
                raise ValueError(f"unsupported operator in or_: {op}")
            parts.append(f"{_col(col)} {_OPS[op]} %s")
            params.append(value)
        self.where.append(("(" + " or ".join(parts) + ")", params))
        return self

    def order(self, col: str, desc: bool = False, **_ignored):
        self._order.append(f"{_col(col)} {'desc' if desc else 'asc'}")
        return self

    def limit(self, n: int):
        self._limit = int(n)
        return self

    # ---- SQL
    def _where_sql(self) -> tuple[str, list]:
        if not self.where:
            return "", []
        return " where " + " and ".join(w for w, _ in self.where), [p for _, ps in self.where for p in ps]

    def build(self) -> tuple[str, list]:
        """(sql with %s placeholders, params). Every statement returns its rows as one jsonb value per row."""
        where, wparams = self._where_sql()
        if self.op == "select":
            sql = f"select {self.cols} from {self.table}{where}"
            if self._order:
                sql += " order by " + ", ".join(self._order)
            if self._limit is not None:
                sql += f" limit {self._limit}"
            return f"select to_jsonb(t) from ({sql}) t", wparams
        if self.op in ("insert", "upsert"):
            return self._build_insert(self.op == "upsert")
        if not self.where:
            raise ValueError(f"refusing {self.op} on {self.table} without a filter")
        if self.op == "update":
            if not self.values:
                raise ValueError("update needs at least one column")
            types = self.db.column_types(self.table_name)
            sets = ", ".join(f"{_q(k)} = %s" for k in self.values)
            return (f"with q as (update {self.table} set {sets}{where} returning *) select to_jsonb(q) from q",
                    [_param(v, types.get(k)) for k, v in self.values.items()] + wparams)
        return f"with q as (delete from {self.table}{where} returning *) select to_jsonb(q) from q", wparams

    def _build_insert(self, upsert: bool) -> tuple[str, list]:
        keys = list(dict.fromkeys(k for r in self.rows for k in r))
        if not keys:
            raise ValueError("insert needs at least one column")
        types = self.db.column_types(self.table_name)
        tuples, params = [], []
        for r in self.rows:
            ph = []
            for k in keys:
                if k in r:
                    ph.append("%s")
                    params.append(_param(r[k], types.get(k)))
                else:
                    ph.append("default")
            tuples.append("(" + ", ".join(ph) + ")")
        sql = f"insert into {self.table} ({', '.join(_q(k) for k in keys)}) values {', '.join(tuples)}"
        if upsert:
            target = ", ".join(_q(c.strip()) for c in self.conflict.split(",")) if self.conflict else None
            if self.ignore_dups:
                sql += f" on conflict ({target}) do nothing" if target else " on conflict do nothing"
            else:
                if not target:
                    raise ValueError("upsert needs on_conflict")
                keyset = {c.strip() for c in self.conflict.split(",")}
                upd = [k for k in keys if k not in keyset]
                sql += (f" on conflict ({target}) do update set " + ", ".join(f"{_q(k)} = excluded.{_q(k)}" for k in upd)
                        if upd else f" on conflict ({target}) do nothing")
        return f"with q as ({sql} returning *) select to_jsonb(q) from q", params

    def execute(self) -> Response:
        sql, params = self.build()
        return Response(self.db.run(sql, params, retry=self.op == "select"))


class Rpc:
    def __init__(self, db: "PgDB", name: str, args: dict | None):
        self.db, self.name, self.args = db, _q(name), args or {}
        self.plain_name = name

    def execute(self) -> Response:
        retset, rettype = self.db.function_info(self.plain_name)
        call = f"{self.name}({', '.join(f'{_q(k)} => %s' for k in self.args)})"
        params = [_param(v) for v in self.args.values()]  # function arguments here are plain scalars
        if rettype == "void":
            self.db.run(f"select {call}", params, fetch=False)
            return Response(None)
        if retset:
            return Response(self.db.run(f"select to_jsonb(t) from {call} t", params))
        rows = self.db.run(f"select to_jsonb({call})", params)
        return Response(rows[0] if rows else None)  # a scalar/record comes back as itself, like PostgREST


class PgDB:
    """A thread-safe connection pool exposing table() and rpc() like the supabase client."""

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 12):
        self.pool = ConnectionPool(
            dsn, min_size=min_size, max_size=max_size, timeout=20, max_idle=120, open=False, name="errand-pg",
            kwargs={"autocommit": True, "prepare_threshold": None,  # None: also safe behind a transaction pooler
                    "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3,
                    "connect_timeout": 15},
            configure=self._configure)
        self.pool.open(wait=False)  # warm connections in the background: the first request must not pay the handshake
        self._fn: dict[str, tuple[bool, str]] = {}
        self._cols: dict[str, dict[str, str]] = {}

    @staticmethod
    def _configure(conn) -> None:
        conn.execute("set time zone 'UTC'")  # timestamps in the same form PostgREST returns

    def table(self, name: str) -> Query:
        return Query(self, name)

    def rpc(self, name: str, args: dict | None = None) -> Rpc:
        return Rpc(self, name, args)

    def column_types(self, table: str) -> dict[str, str]:
        """{column: data_type} for a table (json/jsonb/ARRAY matter when binding values), looked up once per process."""
        if table not in self._cols:
            rows = self.run_raw("select column_name, data_type from information_schema.columns "
                                "where table_schema = 'public' and table_name = %s", [table])
            self._cols[table] = {r[0]: r[1] for r in rows}
        return self._cols[table]

    def function_info(self, name: str) -> tuple[bool, str]:
        """(returns a set?, return type), looked up once per process."""
        if name not in self._fn:
            rows = self.run_raw("select p.proretset, format_type(p.prorettype, null) from pg_proc p "
                                "join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and p.proname = %s",
                                [name])
            if not rows:
                raise APIError({"message": f"function {name} does not exist", "code": "42883", "hint": None, "details": None})
            self._fn[name] = (bool(rows[0][0]), rows[0][1])
        return self._fn[name]

    def run_raw(self, sql: str, params: list) -> list[tuple]:
        try:
            with self.pool.connection() as conn:
                cur = conn.execute(sql, params, prepare=False)
                return cur.fetchall()
        except psycopg.OperationalError:
            raise  # connectivity: callers see a retryable outage (503), not a database error
        except psycopg.Error as e:
            raise api_error(e) from e

    def run(self, sql: str, params: list, fetch: bool = True, retry: bool = False) -> list:
        attempts = 1 + (_READ_RETRIES if retry else 0)
        for i in range(attempts):
            try:
                rows = self.run_raw(sql, params)
                return [r[0] for r in rows] if fetch else []
            except psycopg.OperationalError:
                if i + 1 >= attempts:
                    raise
                log.warning("database connection was stale; retrying the read once")
        return []

    def close(self) -> None:
        self.pool.close()


_pg: PgDB | None = None
_pg_lock = threading.Lock()


def get_pg(dsn: str, min_size: int = 2, max_size: int = 12) -> PgDB:
    global _pg
    if _pg is None:
        with _pg_lock:
            if _pg is None:
                _pg = PgDB(dsn, min_size=min_size, max_size=max_size)
    return _pg
