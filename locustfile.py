"""Explicit read-only Agora workloads. Select user classes; no implicit mixed traffic."""
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from locust import HttpUser, User, between, events, task
from orchestra.http import VerifiedTLSAdapter
from gevent.threadpool import ThreadPool
import websocket

from orchestra.config import load_config, load_env, secret
from orchestra.protocols import RespClient, discover_primary, odbc_value, tls_context, ws_receive

load_env()
CONFIG = load_config()


class HttpNode(HttpUser):
    abstract = True
    wait_time = between(.1, .5)
    profile = 'gateway'

    def on_start(self):
        self.endpoint = CONFIG['load'][self.profile]
        self.base = self.endpoint['url']
        self.ca = secret(self.endpoint['ca_env'])
        if not Path(self.ca).is_file(): raise ValueError('Configured public CA does not exist')
        self.client.trust_env = False
        self.client.verify = self.ca
        self.client.mount('https://', VerifiedTLSAdapter(self.ca, self.endpoint.get('tls_server_name')))

    def get_ok(self, path, name, **kwargs):
        with self.client.get(self.base + path, name=name, timeout=5, allow_redirects=False, catch_response=True, **kwargs) as response:
            if response.status_code != 200: response.failure('Unexpected HTTPS status')

    def on_stop(self): self.client.close()


class GatewayUser(HttpNode):
    host = CONFIG['load']['gateway']['url']
    @task(2)
    def frontend(self): self.get_ok('/', 'gateway.frontend')
    @task
    def spring(self): self.get_ok('/api/auth/health', 'gateway.spring-health')


class FrontendUser(HttpNode):
    profile = 'frontend'
    host = CONFIG['load']['frontend']['url']
    @task
    def page(self): self.get_ok('/', 'frontend.document')


class SpringUser(HttpNode):
    profile = 'spring'
    host = CONFIG['load']['spring']['url']
    @task
    def me(self):
        with self.client.post(self.base + '/api/auth/me', name='spring.me',
            json={'token': secret('ORCHESTRA_TEST_BEARER_TOKEN')}, timeout=5,
            allow_redirects=False, catch_response=True) as response:
            if response.status_code != 200: response.failure('User lookup rejected')


class CppUser(HttpNode):
    profile = 'cpp'
    host = CONFIG['load']['cpp']['url']
    @task
    def count(self):
        server_id = int(self.endpoint['server_id'])
        self.get_ok(f'/internal/cpp/servers/{server_id}/api/canvas/count', 'cpp.canvas-count-via-wall',
                    headers={'X-Agora-Internal-Token': secret('CPP_INTERNAL_API_TOKEN')})


class PhoenixUser(HttpNode):
    profile = 'phoenix'
    host = CONFIG['load']['phoenix']['url']
    @task
    def health(self): self.get_ok('/health', 'phoenix.health')


class ElasticsearchUser(HttpNode):
    profile = 'elasticsearch'
    host = CONFIG['load']['elasticsearch']['url']
    @task
    def count(self):
        index = quote(self.endpoint['index'], safe='')
        self.get_ok('/' + index + '/_count', 'elasticsearch.count-via-wall',
                    auth=(secret('ORCHESTRA_ES_USER'), secret('ORCHESTRA_ES_PASSWORD')))


class ProtocolUser(User):
    abstract = True
    wait_time = between(.1, .5)

    def measure(self, protocol, name, operation):
        started, result, error = time.perf_counter(), None, None
        try: result = operation()
        except Exception as exception:
            # Driver errors may include passwords, JWT query strings or connection strings.
            error = RuntimeError(type(exception).__name__)
        self.environment.events.request.fire(request_type=protocol, name=name,
            response_time=(time.perf_counter() - started) * 1000,
            response_length=len(json.dumps(result, default=str).encode()) if error is None else 0,
            exception=error, context={})
        return result, error


class RedisNodeUser(ProtocolUser):
    abstract = True
    node_index = 0
    def on_start(self): self.connection = None
    def endpoint(self): return CONFIG['load']['redis']['nodes'][self.node_index]
    def execute(self):
        if self.connection is None:
            self.connection = RespClient(self.endpoint(), secret('ORCHESTRA_REDIS_CA'),
                                         secret('ORCHESTRA_REDIS_USER'), secret('ORCHESTRA_REDIS_PASSWORD'))
        if self.connection.command('PING') != 'PONG': raise RuntimeError('Invalid Redis reply')
        return self.connection.command('ROLE')
    @task
    def read(self):
        _, error = self.measure('REDIS', self.__class__.__name__ + '.ping-role', self.execute)
        if error and self.connection: self.connection.close(); self.connection = None
    def on_stop(self):
        if self.connection: self.connection.close()


class RedisPrimaryUser(RedisNodeUser):
    def endpoint(self): return discover_primary(CONFIG['load']['redis'], secret('ORCHESTRA_REDIS_CA'))
    def execute(self):
        result = super().execute()
        if not result or result[0] != 'master':
            self.connection.close(); self.connection = None
            raise RuntimeError('Sentinel returned a non-primary')
        return result


class RedisJsonUser(RedisPrimaryUser):
    def execute(self):
        super().execute()
        key = CONFIG['load']['redis'].get('document_key', 'canvas:1')
        reply = self.connection.command('JSON.GET', key, '$')
        if reply is None: raise RuntimeError('Configure an existing dedicated canvas document')
        return reply


class RedisReplica1User(RedisNodeUser): node_index = 1
class RedisReplica2User(RedisNodeUser): node_index = 2


class SentinelUser(ProtocolUser):
    def on_start(self): self.index = 0; self.clients = {}
    @task
    def discover(self):
        index = self.index % len(CONFIG['load']['redis']['sentinels']); self.index += 1
        if index not in self.clients:
            self.clients[index] = RespClient(CONFIG['load']['redis']['sentinels'][index], secret('ORCHESTRA_REDIS_CA'),
                  secret('ORCHESTRA_SENTINEL_USER'), secret('ORCHESTRA_SENTINEL_PASSWORD'))
        def query():
            result = self.clients[index].command('SENTINEL', 'get-master-addr-by-name', CONFIG['load']['redis']['master_name'])
            allowed = {(node['identity'], node['port']) for node in CONFIG['load']['redis']['nodes']}
            if not isinstance(result, list) or len(result) != 2 or (result[0], int(result[1])) not in allowed:
                raise ValueError('Sentinel primary is outside the configured routes')
            return result
        _, error = self.measure('SENTINEL', f'sentinel-{index + 1}.discovery', query)
        if error: self.clients[index].close(); del self.clients[index]
    def on_stop(self):
        for client in self.clients.values(): client.close()


class MssqlUser(ProtocolUser):
    pool = None
    def on_start(self):
        if MssqlUser.pool is None:
            threads = int(os.environ.get('ORCHESTRA_ODBC_THREADS', '16'))
            if not 1 <= threads <= 128: raise ValueError('ODBC threads must be 1..128')
            MssqlUser.pool = ThreadPool(threads)
        self.connection = None
    def query(self):
        # pyodbc uses native blocking I/O: offload it instead of blocking gevent's hub.
        import pyodbc
        endpoint = CONFIG['load']['mssql']
        if self.connection is None:
            values = {'DRIVER': 'ODBC Driver 18 for SQL Server', 'SERVER': endpoint['host'] + ',' + str(endpoint['port']),
                'DATABASE': endpoint['database'], 'UID': secret('ORCHESTRA_SQL_USER'), 'PWD': secret('ORCHESTRA_SQL_PASSWORD'),
                'Encrypt': 'yes', 'TrustServerCertificate': 'no', 'HostNameInCertificate': endpoint['tls_server_name']}
            connection_string = ';'.join(key + '=' + odbc_value(value) for key, value in values.items())
            self.connection = pyodbc.connect(connection_string, timeout=5, autocommit=True)
            self.connection.timeout = 5
        cursor = self.connection.cursor()
        try:
            row = cursor.execute('SELECT 1 AS orchestra_probe').fetchone()
            if row is None or row[0] != 1: raise RuntimeError('Invalid SQL result')
            return list(row)
        finally: cursor.close()
    @task
    def read(self):
        _, error = self.measure('TDS', 'mssql.select-one-via-wall', lambda: self.pool.apply(self.query))
        if error and self.connection:
            self.pool.apply(self.connection.close); self.connection = None
    def on_stop(self):
        if self.connection: self.pool.apply(self.connection.close)


class CanvasUser(HttpNode, ProtocolUser):
    """Real Spring access -> Nginx/Phoenix upgrade -> C++ read -> correlated ACK."""
    profile = 'canvas'
    host = CONFIG['load']['canvas']['url']
    def on_start(self):
        super().on_start(); self.ws = None
    def connect_canvas(self):
        canvas = int(self.endpoint['canvas_id'])
        with self.client.post(self.base + f'/api/canvases/{canvas}/access',
            name='canvas.access', headers={'Authorization': 'Bearer ' + secret('ORCHESTRA_TEST_BEARER_TOKEN')},
            json={'password': secret('ORCHESTRA_CANVAS_PASSWORD', False) or None}, timeout=5,
            allow_redirects=False, catch_response=True) as reply:
            if reply.status_code != 200:
                reply.failure('Canvas access rejected'); raise RuntimeError('Canvas access rejected')
            access = reply.json()
        token = access['canvas_access_token']; port = int(access['ws_port'])
        url = self.base.replace('https://', 'wss://', 1) + f'/wss/port/{port}/canvas/{canvas}?token=' + quote(token, safe='')
        self.ws = websocket.create_connection(url, timeout=5, sslopt={'context': tls_context(self.ca)},
                                              origin=self.base, http_no_proxy=['*'], enable_multithread=False)
        return ws_receive(self.ws, lambda message: message.get('type') == 'init_items')
    @task
    def read_item(self):
        if self.ws is None:
            _, error = self.measure('WSS', 'canvas.upgrade-and-snapshot', self.connect_canvas)
            if error: self.close_ws(); return
        request_id = uuid.uuid4().hex
        def query():
            self.ws.send(json.dumps({'type': 'crud', 'action': 'read', 'item_id': self.endpoint['item_id'], 'request_id': request_id}))
            response = ws_receive(self.ws, lambda message: message.get('request_id') == request_id)
            if response.get('type') != 'crud_result' or response.get('ok') is not True: raise RuntimeError('Canvas query rejected')
            return response
        _, error = self.measure('WSS', 'canvas.crud-read-ack', query)
        if error: self.close_ws()
    def close_ws(self):
        if self.ws:
            try: self.ws.close()
            except Exception: pass
        self.ws = None
    def on_stop(self): self.close_ws(); super().on_stop()


@events.quitting.add_listener
def enforce_thresholds(environment, **kwargs):
    total = environment.stats.total
    if total.num_requests == 0:
        environment.process_exit_code = 1
    elif total.fail_ratio > float(os.environ.get('ORCHESTRA_MAX_FAILURE_RATIO', '.01')) or \
         total.get_response_time_percentile(.95) > float(os.environ.get('ORCHESTRA_MAX_P95_MS', '1000')):
        environment.process_exit_code = 1
