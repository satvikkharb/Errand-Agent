"""A tiny stand-in for the Supabase client that counts round trips.

Supports exactly the query shapes SupabaseAppStore uses: table().select/insert/update/upsert with eq, is_, in_,
order, limit, and rpc(). Every call that would be a network round trip is appended to `log` as (op, table).
"""
import copy
import uuid
from datetime import datetime, timezone


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.op, self.payload, self.filters, self._order, self._limit = "select", None, [], None, None

    # ---- builders
    def select(self, cols="*"):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def upsert(self, payload, **kw):
        self.op, self.payload = "upsert", payload
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def is_(self, col, val):
        self.filters.append(lambda r: r.get(col) is None)
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r: r.get(col) in vals)
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    # ---- run
    def execute(self):
        self.db.log.append((self.op, self.table))
        if self.db.fail.get((self.op, self.table)):
            raise ConnectionError(f"{self.op} {self.table} failed")
        rows = self.db.tables.setdefault(self.table, [])
        if self.op in ("insert", "upsert"):
            out = []
            for p in (self.payload if isinstance(self.payload, list) else [self.payload]):
                row = {**self.db.defaults.get(self.table, {}), "id": str(uuid.uuid4()),
                       "created_at": datetime.now(timezone.utc).isoformat(), **copy.deepcopy(p)}
                if self.op == "upsert" and any(r["id"] == p.get("id") for r in rows):
                    continue
                rows.append(row)
                out.append(copy.deepcopy(row))
            return _Result(out)
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in hit:
                r.update(copy.deepcopy(self.payload))
            return _Result(copy.deepcopy(hit))
        if self._order:
            hit.sort(key=lambda r: r[self._order[0]], reverse=self._order[1])
        return _Result(copy.deepcopy(hit[: self._limit]))


class _Rpc:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def execute(self):
        self.db.log.append(("rpc", self.name))
        return _Result(True if self.name == "acquire_user_lock" else [])


class FakeDB:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self.log: list[tuple[str, str]] = []
        self.fail: dict[tuple[str, str], bool] = {}
        self.defaults = {"sessions": {"state": "INTAKE", "request_frame": {}, "version": 0, "title": None,
                                      "deleted_at": None, "pending_approval_id": None, "active_hold_id": None}}

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, args=None):
        return _Rpc(self, name)

    def count(self, op=None, table=None) -> int:
        return sum(1 for o, t in self.log if (op is None or o == op) and (table is None or t == table))
