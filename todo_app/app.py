"""Minimal in-memory CRUD todo app (no framework)."""

import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

import store


class TodoHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        self._send_json(store.list_all())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        title = body.get("title")
        if not title:
            self._send_json({"error": "title required"}, 400)
            return
        item = store.add(title)
        self._send_json(item, 201)

    def do_DELETE(self):
        path = self.path.strip("/")
        try:
            item_id = int(path.split("/")[-1])
        except (ValueError, IndexError):
            self._send_json({"error": "invalid id"}, 400)
            return
        if store.delete(item_id):
            self._send_json({"deleted": item_id})
        else:
            self._send_json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        pass


def run(port=8000):
    server = HTTPServer(("127.0.0.1", port), TodoHandler)
    print(f"Todo app running on http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    run()
