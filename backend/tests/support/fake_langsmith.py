"""A local stand-in for LangSmith's ingestion API, so the REAL langsmith SDK can be tested without an account.

It accepts the multipart batches the SDK sends and rebuilds each run (name, type, parent, inputs, outputs, tags,
metadata, error) the way LangSmith would show it.
"""
import json
import threading
import time
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, HTTPServer


class FakeLangSmith:
    project_id = "11111111-2222-4333-8444-555555555555"

    def __init__(self):
        self.runs: dict[str, dict] = {}
        self.feedback: list[dict] = []
        self.requests: list[tuple[str, str, dict]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _read(self):
                return self.rfile.read(int(self.headers.get("content-length", 0)))

            def _ok(self, body=b"{}"):
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                raw = self._read()
                outer.requests.append(("POST", self.path, dict(self.headers)))
                if self.path.endswith("/runs/multipart"):
                    outer._ingest(self.headers["content-type"], raw)
                elif self.path.endswith("/feedback"):
                    outer.feedback.append(json.loads(raw))
                self._ok()

            do_PATCH = do_POST

            def do_GET(self):
                if self.path.startswith("/sessions"):  # project lookup: read_project(project_name=...)
                    body = json.dumps([{"id": outer.project_id, "name": "errand-test", "tenant_id": outer.project_id,
                                        "start_time": "2026-11-02T00:00:00Z",
                                        "reference_dataset_id": None}]).encode()
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _ingest(self, content_type: str, body: bytes) -> None:
        msg = BytesParser().parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
        for part in msg.get_payload():
            name = part.get_param("name", header="content-disposition") or ""
            kind, _, rest = name.partition(".")  # post.<id> | patch.<id> | post.<id>.inputs ...
            run_id, _, field = rest.partition(".")
            payload = part.get_payload(decode=True) or b""
            try:
                data = json.loads(payload)
            except ValueError:
                continue
            run = self.runs.setdefault(run_id, {"id": run_id})
            if field:
                run[field] = data
            else:
                run.update(data)

    def settle(self, runs: int = 1, timeout: float = 8.0) -> dict[str, dict]:
        """Wait for the SDK's background thread to deliver, then return runs by id."""
        end = time.time() + timeout
        while time.time() < end and len(self.runs) < runs:
            time.sleep(0.05)
        time.sleep(0.2)
        return self.runs

    def by_name(self, name: str) -> list[dict]:
        return [r for r in self.runs.values() if r.get("name") == name]

    def children_of(self, run: dict) -> list[dict]:
        return sorted((r for r in self.runs.values() if r.get("parent_run_id") == run["id"]),
                      key=lambda r: r.get("start_time", ""))

    def close(self):
        self.server.shutdown()
