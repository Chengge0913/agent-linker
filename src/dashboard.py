"""Serve a token-protected, read-only agent activity view on loopback."""
import argparse
import hmac
import json
import re
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from progress import snapshot
from timeline import timeline

PAGE = Path(__file__).with_name('dashboard.html')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--url-file', required=True, type=Path)
    parser.add_argument('--reuse-url', action='store_true', help='Reuse a previously generated local URL from --url-file')
    args = parser.parse_args()
    port = 0
    token = secrets.token_urlsafe(32)
    if args.reuse_url:
        try:
            previous = urlsplit(args.url_file.read_text(encoding='utf-8').strip())
            candidate = previous.path.strip('/')
            if (previous.scheme != 'http' or previous.hostname != '127.0.0.1' or previous.username or
                    previous.password or previous.query or previous.fragment or not previous.port or
                    not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', candidate)):
                raise ValueError('invalid stored loopback URL')
            port, token = previous.port, candidate
        except (OSError, ValueError) as exc:
            parser.error(f'cannot reuse URL: {exc}')
    db, output = args.db.resolve(), args.output_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, body, content_type):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'self'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parts = urlsplit(self.path).path.strip('/').split('/')
            host = self.headers.get('Host', '').split(':', 1)[0]
            if host not in ('127.0.0.1', 'localhost') or not parts or not hmac.compare_digest(parts[0], token):
                self.reply(404, b'Not found', 'text/plain')
                return
            route = parts[1:]
            if not route or route == ['']:
                self.reply(200, PAGE.read_bytes(), 'text/html; charset=utf-8')
                return
            try:
                tasks = snapshot(db, output)
                if route == ['api', 'tasks']:
                    payload = tasks
                elif len(route) == 3 and route[:2] == ['api', 'task'] and re.fullmatch(r'[0-9a-f]{32}', route[2]):
                    task = next((row for row in tasks if row['id'] == route[2]), None)
                    if not task:
                        self.reply(404, b'Not found', 'text/plain')
                        return
                    payload = {'task': task, 'timeline': timeline(output / task['id'] / 'events.jsonl')}
                else:
                    self.reply(404, b'Not found', 'text/plain')
                    return
                self.reply(200, json.dumps(payload, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.reply(503, json.dumps({'error': str(exc)}, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    args.url_file.write_text(f'http://127.0.0.1:{server.server_port}/{token}/', encoding='utf-8')
    try:
        server.serve_forever(poll_interval=.5)
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
