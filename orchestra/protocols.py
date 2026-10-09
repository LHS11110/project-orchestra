import json
import socket
import ssl
import time
from .config import secret


def tls_context(ca):
    context = ssl.create_default_context(cafile=ca)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    return context


class RespClient:
    """Small bounded RESP2 client; original identity is independent of Wall's dial address."""
    def __init__(self, endpoint, ca, username='', password='', timeout=3):
        self.endpoint, self.ca, self.username, self.password = endpoint, ca, username, password
        self.timeout, self.socket, self.stream = timeout, None, None
        self.bytes_read = 0

    def close(self):
        if self.stream: self.stream.close()
        if self.socket: self.socket.close()
        self.socket = self.stream = None

    def connect(self):
        plain = socket.create_connection((self.endpoint['dial_host'], self.endpoint['dial_port']), self.timeout)
        try:
            self.socket = tls_context(self.ca).wrap_socket(plain, server_hostname=self.endpoint['identity'])
            self.socket.settimeout(self.timeout)
            self.stream = self.socket.makefile('rb')
            if self.password:
                self.command(*(['AUTH', self.username, self.password] if self.username else ['AUTH', self.password]))
        except BaseException:
            plain.close(); self.close(); raise

    def read(self, length):
        if length < 0 or self.bytes_read + length > 8 * 1024 * 1024: raise ValueError('RESP response exceeds limit')
        data = self.stream.read(length)
        if len(data) != length: raise ConnectionError('RESP connection closed')
        self.bytes_read += len(data)
        return data

    def line(self):
        result = self.stream.readline(4097)
        if len(result) > 4096 or not result.endswith(b'\r\n'): raise ValueError('Invalid RESP line')
        self.bytes_read += len(result)
        if self.bytes_read > 8 * 1024 * 1024: raise ValueError('RESP response exceeds limit')
        return result[:-2]

    def response(self, depth=0):
        if depth > 16: raise ValueError('RESP nesting exceeds limit')
        kind = self.read(1)
        if kind == b'+': return self.line().decode()
        if kind == b'-': self.line(); raise RuntimeError('Redis rejected command')
        if kind == b':': return int(self.line())
        if kind == b'$':
            size = int(self.line())
            if size == -1: return None
            data = self.read(size)
            if self.read(2) != b'\r\n': raise ValueError('Invalid RESP bulk terminator')
            return data.decode()
        if kind == b'*':
            size = int(self.line())
            if size == -1: return None
            if not 0 <= size <= 1024: raise ValueError('RESP array exceeds limit')
            return [self.response(depth + 1) for _ in range(size)]
        raise ValueError('Unsupported RESP response')

    def command(self, *parts):
        if self.socket is None: self.connect()
        values = [str(value).encode() for value in parts]
        payload = b'*' + str(len(values)).encode() + b'\r\n'
        payload += b''.join(b'$' + str(len(value)).encode() + b'\r\n' + value + b'\r\n' for value in values)
        self.bytes_read = 0
        try:
            self.socket.sendall(payload)
            return self.response()
        except BaseException:
            self.close(); raise


def discover_primary(redis_config, ca):
    allowed = {(endpoint['identity'], endpoint['port']): endpoint for endpoint in redis_config['nodes']}
    for endpoint in redis_config['sentinels']:
        client = RespClient(endpoint, ca, secret('ORCHESTRA_SENTINEL_USER', False), secret('ORCHESTRA_SENTINEL_PASSWORD'))
        try:
            result = client.command('SENTINEL', 'get-master-addr-by-name', redis_config['master_name'])
            if not isinstance(result, list) or len(result) != 2: continue
            primary = allowed.get((result[0], int(result[1])))
            if primary is None: raise ValueError('Sentinel advertised an endpoint outside Wall routes')
            return primary
        except (OSError, ConnectionError, RuntimeError): continue
        finally: client.close()
    raise ConnectionError('No reachable Sentinel primary')


def ws_receive(ws, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    for _ in range(256):
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise TimeoutError('WSS response timed out')
        ws.settimeout(remaining)
        raw = ws.recv()
        if not raw: raise ConnectionError('WSS closed')
        if len(raw) > 12 * 1024 * 1024: raise ValueError('WSS frame exceeds limit')
        message = json.loads(raw)
        if message.get('type') == 'ping': ws.send(json.dumps({'type': 'pong'})); continue
        if predicate(message): return message
    raise RuntimeError('Too many unrelated WSS messages')


def odbc_value(value):
    return '{' + str(value).replace('}', '}}') + '}'
