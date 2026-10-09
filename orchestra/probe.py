"""Read-only business probes; run from the service network with public CAs."""
import argparse
import json
import requests
from .config import load_config, load_env, secret
from .http import VerifiedTLSAdapter
from .protocols import RespClient, discover_primary, odbc_value


def probe(config, profile):
    if profile == 'redis':
        endpoint = discover_primary(config['load']['redis'], secret('ORCHESTRA_REDIS_CA'))
        client = RespClient(endpoint, secret('ORCHESTRA_REDIS_CA'), secret('ORCHESTRA_REDIS_USER'), secret('ORCHESTRA_REDIS_PASSWORD'))
        try:
            role = client.command('ROLE')
            if not role or role[0] != 'master' or client.command('PING') != 'PONG': raise RuntimeError('Primary validation failed')
            return {'profile': profile, 'primary': endpoint['identity'], 'role': 'master'}
        finally: client.close()
    if profile == 'mssql':
        import pyodbc
        endpoint = config['load']['mssql']
        values = {'DRIVER': 'ODBC Driver 18 for SQL Server', 'SERVER': endpoint['host'] + ',' + str(endpoint['port']),
             'DATABASE': endpoint['database'], 'UID': secret('ORCHESTRA_SQL_USER'), 'PWD': secret('ORCHESTRA_SQL_PASSWORD'),
             'Encrypt': 'yes', 'TrustServerCertificate': 'no', 'HostNameInCertificate': endpoint['tls_server_name']}
        connection = pyodbc.connect(';'.join(key + '=' + odbc_value(value) for key, value in values.items()), timeout=5, autocommit=True)
        try:
            cursor = connection.cursor()
            try:
                if cursor.execute('SELECT 1').fetchone()[0] != 1: raise RuntimeError('SQL probe failed')
            finally: cursor.close()
        finally: connection.close()
        return {'profile': profile, 'ok': True}
    endpoint = config['load'][profile]
    ca = secret(endpoint['ca_env'])
    with requests.Session() as session:
        session.trust_env = False
        session.mount('https://', VerifiedTLSAdapter(ca, endpoint.get('tls_server_name')))
        path, kwargs = '/', {}
        if profile == 'gateway': path = '/api/auth/health'
        elif profile == 'spring': path = '/api/auth/health'
        elif profile == 'phoenix': path = '/health'
        elif profile == 'cpp':
            path = f'/internal/cpp/servers/{int(endpoint["server_id"])}/api/canvas/count'
            kwargs['headers'] = {'X-Agora-Internal-Token': secret('CPP_INTERNAL_API_TOKEN')}
        elif profile == 'elasticsearch':
            from urllib.parse import quote
            path = '/' + quote(endpoint['index'], safe='') + '/_count'
            kwargs['auth'] = (secret('ORCHESTRA_ES_USER'), secret('ORCHESTRA_ES_PASSWORD'))
        result = session.get(endpoint['url'] + path, verify=ca, timeout=5, allow_redirects=False, **kwargs)
        if result.status_code != 200: raise RuntimeError('HTTPS probe failed')
        return {'profile': profile, 'ok': True, 'status': 200}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['gateway', 'frontend', 'spring', 'cpp', 'phoenix', 'mssql', 'redis', 'elasticsearch'], required=True)
    parser.add_argument('--config')
    args = parser.parse_args()
    try:
        load_env(); print(json.dumps(probe(load_config(args.config), args.profile)))
    except Exception as error: parser.exit(1, type(error).__name__ + ': read-only probe failed; check configuration.\n')


if __name__ == '__main__': main()
