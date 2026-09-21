"""Local-only UI fixture for testAgentIdentity. No account or production calls."""
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        now = int(time.time())
        sessions = {
            "fixture-codex": {"engine": "codex", "source": "desktop", "project": "demo",
                              "detail": "Desktop icon verification", "status": "running", "since": now - 30,
                              "token_usage": {"input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500}},
            "fixture-claude": {"project": "demo", "detail": "Legacy Claude task", "status": "waiting", "since": now - 60},
            "fixture-done": {"engine": "codex", "project": "demo", "detail": "A completed Codex task with a long descriptive title", "status": "done", "since": now - 100},
        }
        data = {"Demo Mac": {"ts": now, "sessions": sessions}} if self.path.split("?")[0] == "/api/state" else {}
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8879), Handler).serve_forever()
