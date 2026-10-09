"""TLS 1.3 fixture verifies certificate enforcement and actual Locust WSS ACK flow."""
import base64
import csv
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

import requests
from orchestra.http import VerifiedTLSAdapter

ROOT = Path(__file__).resolve().parents[1]


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args): pass
    def reply(self, value):
        payload = json.dumps(value).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        if self.path == '/api/auth/me':
            if not payload.get('token'):
                self.send_error(401); return
            self.reply({'email': 'fixture@example.invalid'})
        else: self.reply({'canvas_access_token': 'fixture-token', 'ws_port': 8002})
    def handle(self):
        try: super().handle()
        except (ConnectionResetError, BrokenPipeError): pass
    def do_GET(self):
        if self.path == '/api/auth/me':
            self.send_error(405); return
        if self.headers.get('Upgrade', '').lower() == 'websocket':
            key = self.headers['Sec-WebSocket-Key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
            self.send_response(101); self.send_header('Upgrade', 'websocket'); self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', base64.b64encode(hashlib.sha1(key.encode()).digest()).decode())
            self.end_headers()
            def send(value):
                payload = json.dumps(value).encode()
                self.wfile.write(bytes([0x81, len(payload)]) + payload); self.wfile.flush()
            send({'type': 'ping'})
            send({'type': 'init_items', 'items': []})
            try:
                while True:
                    header = self.rfile.read(2)
                    if len(header) != 2 or header[0] & 15 == 8: return
                    length = header[1] & 127
                    if length == 126: length = struct.unpack('!H', self.rfile.read(2))[0]
                    elif length == 127: length = struct.unpack('!Q', self.rfile.read(8))[0]
                    if length > 16384: return
                    mask = self.rfile.read(4) if header[1] & 128 else b''
                    data = self.rfile.read(length)
                    if mask: data = bytes(value ^ mask[i % 4] for i, value in enumerate(data))
                    message = json.loads(data)
                    if message.get('type') == 'pong': continue
                    send({'type': 'crud_result', 'ok': True, 'request_id': 'unrelated'})
                    send({'type': 'crud_result', 'ok': True, 'request_id': message['request_id']})
            except (OSError, ValueError): pass
            finally: self.close_connection = True
        else: self.reply({'ok': True})


class TlsLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temporary.name)
        cls.ca, cls.key = cls.directory / 'ca.pem', cls.directory / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
            '-keyout', str(cls.key), '-out', str(cls.ca), '-subj', '/CN=localhost',
            '-addext', 'subjectAltName=DNS:localhost', '-addext', 'basicConstraints=critical,CA:TRUE'],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.servers = []
        def start(minimum, maximum):
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.daemon_threads = True
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version, context.maximum_version = minimum, maximum
            context.load_cert_chain(cls.ca, cls.key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            cls.servers.append(server)
            return server.server_port
        cls.port = start(ssl.TLSVersion.TLSv1_3, ssl.TLSVersion.TLSv1_3)
        cls.old_port = start(ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_2)
    @classmethod
    def tearDownClass(cls):
        for server in cls.servers: server.shutdown(); server.server_close()
        cls.temporary.cleanup()
    def session(self, name=None):
        session = requests.Session(); session.trust_env = False
        session.mount('https://', VerifiedTLSAdapter(str(self.ca), name))
        return session
    def test_custom_sni_checks_certificate_at_different_dial_address(self):
        with self.session('localhost') as session:
            self.assertEqual(session.get(f'https://127.0.0.1:{self.port}', verify=str(self.ca), timeout=3).status_code, 200)
    def test_wrong_hostname_is_rejected(self):
        with self.session() as session:
            with self.assertRaises(requests.exceptions.SSLError):
                session.get(f'https://127.0.0.1:{self.port}', verify=str(self.ca), timeout=3)
    def test_tls12_server_is_rejected(self):
        with self.session() as session:
            with self.assertRaises(requests.exceptions.SSLError):
                session.get(f'https://localhost:{self.old_port}', verify=str(self.ca), timeout=3)
    def test_real_locust_https_and_correlated_websocket(self):
        config = json.loads((ROOT / 'config/orchestra.example.json').read_text())
        for endpoint in config['load'].values():
            if 'url' in endpoint:
                endpoint['url'] = f'https://localhost:{self.port}'
                endpoint.pop('tls_server_name', None)
        path = self.directory / 'config.json'; path.write_text(json.dumps(config))
        prefix = self.directory / 'smoke'
        env = dict(os.environ, ORCHESTRA_CONFIG=str(path), ORCHESTRA_SERVICE_CA=str(self.ca), ORCHESTRA_ES_CA=str(self.ca),
            ORCHESTRA_TEST_BEARER_TOKEN='fixture-only', CPP_INTERNAL_API_TOKEN='fixture-only',
            ORCHESTRA_ES_USER='fixture', ORCHESTRA_ES_PASSWORD='fixture-only', ORCHESTRA_MAX_P95_MS='2000')
        classes = ['GatewayUser', 'FrontendUser', 'SpringUser', 'CppUser', 'PhoenixUser', 'ElasticsearchUser', 'CanvasUser']
        command = [sys.executable, '-m', 'locust', '-f', str(ROOT / 'locustfile.py'), '--headless',
            '--users', '7', '--spawn-rate', '14', '--run-time', '5s', '--csv', str(prefix), '--only-summary', *classes]
        result = subprocess.run(command, cwd=self.directory, env=env, capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr[-5000:])
        with Path(str(prefix) + '_stats.csv').open() as stream: rows = list(csv.DictReader(stream))
        names = {row['Name'] for row in rows}
        self.assertTrue({'canvas.access', 'canvas.upgrade-and-snapshot', 'canvas.crud-read-ack',
            'cpp.canvas-count-via-wall', 'elasticsearch.count-via-wall', 'phoenix.health', 'spring.me',
            'frontend.document', 'gateway.frontend'}.issubset(names), names)
        self.assertTrue(all(int(row['Failure Count']) == 0 for row in rows))
        self.assertNotIn('fixture-token', result.stdout + result.stderr)


if __name__ == '__main__': unittest.main()
