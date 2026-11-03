"""Local health endpoint (stdlib http.server) bound to loopback only. Exposes no secrets and accepts no input."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def make_server(port: int, status_fn: Callable[[], dict], host: str = "127.0.0.1") -> ThreadingHTTPServer:
    if host not in LOOPBACK:
        raise ValueError("health endpoint must bind to loopback")

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.split("?")[0] not in {"/", "/healthz"}:
                self.send_error(404)
                return
            body = json.dumps(status_fn()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _deny(self):
            self.send_error(405)

        do_POST = do_PUT = do_DELETE = do_PATCH = _deny  # noqa: N815

        def log_message(self, *a):  # silence default stderr logging
            pass

    srv = ThreadingHTTPServer((host, port), H)
    srv.daemon_threads = True
    return srv


def serve_in_thread(srv: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="kvl-health")
    t.start()
    return t
