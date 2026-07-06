#!/usr/bin/env python3
"""
mock_receiver.py
-----------------
Stand-in for a subscriber's real webhook endpoint. Listens on a port,
pretty-prints every incoming request's JSON body as it arrives, and
replies 200 OK - so you can watch, in real time, the exact payload
Ticket Bridge's dispatcher sends during /api/v1/sync, instead of just
reading a description of it.

Used by live_delivery_demo.sh, which points system_b's base_url at this
receiver temporarily (the seed data's example.local URLs are fictional
and will never actually respond).

Usage:
    python3 examples/mock_receiver.py [port]   # default port 9000
"""
import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

# Force line-buffered output: this is normally run as a backgrounded
# process piped through a parent script/log file, and Python fully
# buffers stdout (instead of line-buffering) whenever it isn't a TTY -
# without this, nothing printed below would actually appear before the
# process is killed.
sys.stdout.reconfigure(line_buffering=True)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length)
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

        print(f"\n[{timestamp}] {self.command} {self.path}")
        for header in ("Authorization", "X-Api-Key"):
            if header in self.headers:
                print(f"  {header}: {self.headers[header]}")
        try:
            body = json.loads(raw_body)
            print(json.dumps(body, indent=2))
        except json.JSONDecodeError:
            print(raw_body.decode(errors="replace"))

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"received": true}')

    def log_message(self, format, *args):
        pass  # keep output focused on the payloads themselves, not HTTP access logs


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Mock receiver listening on http://localhost:{port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
