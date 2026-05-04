#!/usr/bin/env python3
"""
Election Dashboard — Local Proxy Server
Solves the iPhone 'file://' CORS problem.

Usage:
  python3 serve.py

Then open the printed URL on your iPhone (same WiFi network).
"""

import socket, subprocess, sys, os, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from urllib.request import urlopen, Request
from urllib.error import URLError

PORT     = 8080
ECI_BASE = "https://results.eci.gov.in"
HTML     = os.path.join(os.path.dirname(__file__), "tn_election_results.html")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile Safari/604.1"),
    "Accept":     "*/*",
    "Referer":    f"{ECI_BASE}/",
}

def fetch_eci(url):
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=15) as r:
        return r.read()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # quiet

    def do_GET(self):
        parsed = urlparse(self.path)

        # ── Proxy endpoint: /proxy?url=https://results.eci.gov.in/...
        if parsed.path == "/proxy":
            params   = parse_qs(parsed.query)
            raw_url  = unquote(params.get("url", [""])[0])
            if not raw_url.startswith(ECI_BASE):
                self.send_error(403, "Only ECI URLs allowed"); return
            try:
                body = fetch_eci(raw_url)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", len(body))
                self.end_headers()
                self.wfile.write(body)
            except URLError as e:
                self.send_error(502, str(e))
            return

        # ── Serve the dashboard HTML
        if parsed.path in ("/", "/tn_election_results.html"):
            try:
                with open(HTML, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", len(body))
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self.send_error(404, "tn_election_results.html not found")
            return

        self.send_error(404)

def local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"

if __name__ == "__main__":
    if not os.path.exists(HTML):
        print(f"ERROR: {HTML} not found. Run from the same folder.", file=sys.stderr)
        sys.exit(1)

    ip   = local_ip()
    url  = f"http://{ip}:{PORT}/tn_election_results.html"
    loc  = f"http://localhost:{PORT}/tn_election_results.html"

    print("=" * 58)
    print("  Election Dashboard — Proxy Server")
    print("=" * 58)
    print(f"  Local:    {loc}")
    print(f"  iPhone:   {url}  ← open this on your iPhone")
    print("  (iPhone must be on the same WiFi network)")
    print("  Press Ctrl+C to stop")
    print("=" * 58)

    server = HTTPServer(("", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
