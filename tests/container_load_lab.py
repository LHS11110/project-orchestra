"""Isolated image smoke test: TLS Locust, ODBC presence, report volume, UID drop."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestra.docker import Docker

ROOT = Path(__file__).resolve().parents[1]


def main():
    docker = Docker(); suffix = uuid.uuid4().hex[:10]
    network, server, volume = 'orchestra-load-net-' + suffix, 'orchestra-load-fixture-' + suffix, 'orchestra-load-results-' + suffix
    image = 'project-orchestra:local'
    with tempfile.TemporaryDirectory() as directory:
        files = Path(directory)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
            '-keyout', str(files / 'key.pem'), '-out', str(files / 'ca.pem'), '-subj', '/CN=orchestra-fixture',
            '-addext', 'subjectAltName=DNS:orchestra-fixture'], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (files / 'handler.py').write_text((ROOT / 'tests/test_tls_load.py').read_text())
        (files / 'serve.py').write_text('''import ssl
from http.server import ThreadingHTTPServer
from handler import Handler
server = ThreadingHTTPServer(('0.0.0.0', 8443), Handler)
server.daemon_threads = True
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.minimum_version = ssl.TLSVersion.TLSv1_3
context.load_cert_chain('/fixture/ca.pem', '/fixture/key.pem')
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
''')
        config = json.loads((ROOT / 'config/orchestra.example.json').read_text())
        for endpoint in config['load'].values():
            if 'url' in endpoint:
                endpoint['url'] = 'https://orchestra-fixture:8443'
                endpoint.pop('tls_server_name', None)
        (files / 'config.json').write_text(json.dumps(config))
        docker.call('network', 'create', '--internal', network)
        docker.call('volume', 'create', volume)
        try:
            docker.call('run', '-d', '--name', server, '--network', network, '--network-alias', 'orchestra-fixture',
                '-v', str(files) + ':/fixture:ro', '--entrypoint', 'python', image, '/fixture/serve.py')
            # Only public CA and config are mounted into the load worker, never fixture private keys.
            mounts = ['-v', str(files / 'ca.pem') + ':/run/certs/ca.pem:ro',
                      '-v', str(files / 'config.json') + ':/config/orchestra.json:ro', '-v', volume + ':/results']
            environment = ['-e', 'ORCHESTRA_CONFIG=/config/orchestra.json', '-e', 'ORCHESTRA_SERVICE_CA=/run/certs/ca.pem',
                '-e', 'ORCHESTRA_SQL_CA=/run/certs/ca.pem', '-e', 'ORCHESTRA_TEST_BEARER_TOKEN=fixture-only']
            # Driver installed, privilege drop active, report directory writable.
            output = docker.call('run', '--rm', '--network', network, *mounts, *environment, image, 'python', '-c',
                'import os,pyodbc; assert os.getuid()==10001; assert "ODBC Driver 18 for SQL Server" in pyodbc.drivers(); open("/results/uid-ok","w").write("ok")')
            result = subprocess.run(['docker', 'run', '--rm', '--network', network, *mounts, *environment, image,
                'locust', '-f', 'locustfile.py', '--headless', '--users', '2', '--spawn-rate', '2', '--run-time', '5s',
                '--csv', '/results/lab', '--html', '/results/lab.html', '--only-summary', 'CanvasUser'],
                capture_output=True, text=True, timeout=90)
            if result.returncode: raise RuntimeError('Container Locust fixture failed: ' + result.stderr[-3000:])
            raw = docker.call('run', '--rm', '--network', 'none', '--entrypoint', 'cat', '-v', volume + ':/results:ro', image, '/results/lab_stats.csv')
            rows = list(csv.DictReader(raw.splitlines()))
            assert all(int(row['Failure Count']) == 0 for row in rows)
            assert any(row['Name'] == 'canvas.crud-read-ack' for row in rows)
            docker.call('run', '--rm', '--network', 'none', '--entrypoint', 'test', '-v', volume + ':/results:ro', image, '-s', '/results/lab.html')
            print('Docker image: ODBC 18, UID 10001, TLS WSS ACK, CSV and HTML reports verified')
        finally:
            subprocess.run(['docker', 'rm', '-f', server], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            docker.call('volume', 'rm', volume); docker.call('network', 'rm', network)


if __name__ == '__main__': main()
