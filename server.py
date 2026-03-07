#!/usr/bin/env python3
"""
Meal System local server.
Serves the app and handles read/write for JSON data files.

Usage:
  python3 server.py          # runs on port 8080
  python3 server.py 9090     # custom port

Access from phone via Tailscale: http://<macbook-tailscale-hostname>:8080
"""
import json
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)

ALLOWED_FILES = {'ms2.json', 'plan.json', 'pantry.json'}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path.startswith('/data/'):
            self._serve_data()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith('/data/'):
            self._save_data()
        else:
            self.send_error(404)

    def _filename(self):
        name = self.path[len('/data/'):]
        if name not in ALLOWED_FILES:
            return None
        return DATA_DIR / name

    def _serve_data(self):
        filepath = self._filename()
        if filepath is None:
            self.send_error(400)
            return
        if not filepath.exists():
            self._json_response(b'null')
            return
        self._json_response(filepath.read_bytes())

    def _save_data(self):
        filepath = self._filename()
        if filepath is None:
            self.send_error(400)
            return
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        # Validate it's valid JSON before writing
        try:
            json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, 'Invalid JSON')
            return
        filepath.write_bytes(body)
        self._json_response(b'{"ok":true}')

    def _json_response(self, body: bytes):
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, fmt, *args):
        # Only log data writes, suppress static file noise
        if '/data/' in (args[0] if args else ''):
            print(f'[data] {args[0]} {args[1]}')


if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    print(f'Meal System → http://localhost:{PORT}')
    print(f'Tailscale   → http://<your-macbook-hostname>:{PORT}')
    print('Ctrl+C to stop')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
