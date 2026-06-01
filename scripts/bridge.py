import http.server
import json
import os
import subprocess

PORT = int(os.environ.get('BRIDGE_PORT', '18789'))
SCRIPT = os.environ.get('BRIDGE_SCRIPT', '')


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'ok')

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        try:
            data = json.loads(self.rfile.read(length)) if length else {}
        except Exception:
            data = {}

        env = {**os.environ}
        for k, v in data.items():
            env[k.upper().replace('-', '_').replace('.', '_')] = str(v)

        r = subprocess.run(
            ['bash', '-l', SCRIPT],
            env=env,
            capture_output=True,
            text=True,
        )

        out = r.stdout.encode()
        self.send_response(200 if r.returncode == 0 else 500)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_):
        pass


print(f'[bridge] 127.0.0.1:{PORT}', flush=True)
http.server.HTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
