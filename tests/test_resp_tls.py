"""Exercise the custom RESP client over a real TLS socket, without a live Redis."""
import io
import json
import os
from pathlib import Path
import socketserver
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from orchestra.protocols import RespClient, discover_primary


class RespHandler(socketserver.StreamRequestHandler):
    def handle(self):
        decoder = RespClient({}, 'unused'); decoder.stream = self.rfile
        authenticated = False
        while True:
            try:
                decoder.bytes_read = 0
                command = decoder.response()
            except (ConnectionError, OSError): return
            if command == ['AUTH', 'fixture', 'fixture-password']:
                authenticated = True; reply = b'+OK\r\n'
            elif not authenticated: reply = b'-NOAUTH\r\n'
            elif command == ['PING']: reply = b'+PONG\r\n'
            elif command == ['ROLE']: reply = b'*1\r\n$6\r\nmaster\r\n'
            elif command[:2] == ['SENTINEL', 'get-master-addr-by-name']:
                reply = b'*2\r\n$9\r\nlocalhost\r\n$4\r\n6379\r\n'
            elif command[:2] == ['JSON.GET', 'canvas:1']:
                reply = b'$2\r\n{}\r\n'
            else: reply = b'-ERR\r\n'
            try: self.wfile.write(reply); self.wfile.flush()
            except OSError: return


class TlsRespTests(unittest.TestCase):
    def test_identity_dial_split_auth_sentinel_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            ca, key = Path(directory, 'ca.pem'), Path(directory, 'key.pem')
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                '-keyout', str(key), '-out', str(ca), '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            class Server(socketserver.ThreadingTCPServer):
                daemon_threads = True
                def get_request(self):
                    socket, address = super().get_request()
                    try: return context.wrap_socket(socket, server_side=True), address
                    except BaseException: socket.close(); raise
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.minimum_version = ssl.TLSVersion.TLSv1_3
            context.load_cert_chain(ca, key)
            server = Server(('127.0.0.1', 0), RespHandler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            endpoint = {'identity': 'localhost', 'port': 6379, 'dial_host': '127.0.0.1', 'dial_port': server.server_address[1]}
            try:
                config = {'nodes': [endpoint], 'sentinels': [endpoint], 'master_name': 'fixture-master'}
                with patch.dict(os.environ, ORCHESTRA_SENTINEL_USER='fixture', ORCHESTRA_SENTINEL_PASSWORD='fixture-password'):
                    self.assertEqual(discover_primary(config, str(ca)), endpoint)
                client = RespClient(endpoint, str(ca), 'fixture', 'fixture-password')
                try:
                    self.assertEqual(client.command('PING'), 'PONG')
                    self.assertEqual(client.command('ROLE'), ['master'])
                    self.assertEqual(json.loads(client.command('JSON.GET', 'canvas:1', '$')), {})
                    self.assertEqual(client.socket.version(), 'TLSv1.3')
                finally: client.close()
                wrong = RespClient(dict(endpoint, identity='wrong-name'), str(ca))
                with self.assertRaises(ssl.SSLCertVerificationError): wrong.command('PING')
                self.assertIsNone(wrong.socket)
            finally: server.shutdown(); server.server_close()


if __name__ == '__main__': unittest.main()
