"""Disposable database durability experiments. Never targets existing DB containers.

Only Python stdlib and Docker are required. Uses unique labelled containers and
volumes, no published ports, and an internal network for Redis replication.
SIGKILL is a process crash test, NOT a power-loss/storage-controller test.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import statistics
import subprocess
import tempfile
import threading
import time
import uuid

STOP = threading.Event()
LABEL = 'orchestra.durability.run'

def check_stop():
    if STOP.is_set():
        raise RuntimeError('Experiment interrupted; cleaning disposable resources')

def docker(*args, data=None, timeout=120, check=True):
    result = subprocess.run(['docker', *map(str, args)], input=data, capture_output=True,
                            timeout=timeout)
    if check and result.returncode:
        # Do not print command arguments, environment files or SQL credentials.
        raise RuntimeError('Docker operation failed: ' + str(args[0]) + ': ' +
                           result.stderr.decode(errors='replace')[-600:])
    return result

def progress(db, message):
    print(json.dumps({'database': db, 'phase': message}), flush=True)

class Lab:
    def __init__(self, run_id, database):
        self.run_id, self.database = run_id, database
        self.prefix = 'orchestra-durability-' + run_id + '-' + database
        self.container = self.prefix
        self.containers, self.volumes, self.networks = [], [], []
        self.directory = tempfile.TemporaryDirectory(prefix=self.prefix)
        self.config = Path(self.directory.name)

    def volume(self, suffix):
        name = self.prefix + '-' + suffix
        docker('volume', 'create', '--label', LABEL + '=' + self.run_id, name)
        self.volumes.append(name)
        return name

    def network(self):
        name = self.prefix + '-net'
        docker('network', 'create', '--internal', '--label', LABEL + '=' + self.run_id, name)
        self.networks.append(name)
        return name

    def start_container(self, name, arguments):
        check_stop()
        if name not in self.containers:
            self.containers.append(name)
        docker('run', '-d', '--name', name, '--label', LABEL + '=' + self.run_id,
               '--cpus', '2', *arguments)

    def cleanup(self):
        failures = []
        for kind, names in [('container', self.containers), ('volume', self.volumes), ('network', self.networks)]:
            for name in reversed(names):
                # Exact random-run prefix and label guard before every removal.
                if not name.startswith(self.prefix):
                    raise RuntimeError('Cleanup target escaped disposable prefix')
                inspected = docker(kind, 'inspect', name, check=False)
                if inspected.returncode:
                    continue
                info = json.loads(inspected.stdout)[0]
                labels = info.get('Config', {}).get('Labels', {}) if kind == 'container' else info.get('Labels', {})
                if (labels or {}).get(LABEL) != self.run_id:
                    failures.append(name)
                    continue
                args = ['rm', '-f', name] if kind == 'container' else [kind, 'rm', name]
                if docker(*args, check=False).returncode:
                    failures.append(name)
        self.directory.cleanup()
        return failures

    def wait(self, probe, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            check_stop()
            try:
                value = probe()
                if value:
                    return value
            except (RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
                pass
            STOP.wait(.25)
        raise RuntimeError(self.database + ' readiness timeout')

    def fault(self, mode, create, ready, graceful_stop=None):
        check_stop()
        start = time.perf_counter()
        if mode == 'graceful_restart':
            if graceful_stop:
                graceful_stop()
            else:
                docker('stop', '--time', '30', self.container, timeout=45)
            state = json.loads(docker('inspect', self.container).stdout)[0]['State']
            if state['ExitCode'] == 137:
                raise RuntimeError('Graceful stop exceeded its timeout and was killed')
        else:
            docker('kill', '--signal', 'KILL', self.container)
            state = json.loads(docker('inspect', self.container).stdout)[0]['State']
            if state['ExitCode'] != 137:
                raise RuntimeError('SIGKILL did not produce expected exit code')
        if mode == 'kill_and_recreate':
            docker('rm', self.container)
            create()
        else:
            docker('start', self.container)
        self.wait(ready)
        return (time.perf_counter() - start) * 1000


def resp(*parts):
    values = [str(part).encode() for part in parts]
    return b'*' + str(len(values)).encode() + b'\r\n' + b''.join(
        b'$' + str(len(part)).encode() + b'\r\n' + part + b'\r\n' for part in values)


def redis_lab(lab, records):
    image = 'redis:8.6.7'
    volume, network = lab.volume('data'), lab.network()
    def create():
        lab.start_container(lab.container, ['--memory', '512m', '--network', network,
            '-v', volume + ':/data', image, 'redis-server', '--appendonly', 'yes',
            '--appendfsync', 'everysec', '--no-appendfsync-on-rewrite', 'no',
            '--save', '3600 1 300 100 60 10000', '--min-replicas-to-write', '0',
            '--bind', '0.0.0.0', '--protected-mode', 'no'])
    def cli(*args, target=None):
        return docker('exec', target or lab.container, 'redis-cli', *args, timeout=15).stdout
    def ready():
        return cli('PING').strip() == b'PONG'
    expected = {}
    def write(first, count):
        commands = []
        for i in range(first, first + count):
            key = 'durability:' + str(i)
            value = {'id': i, 'payload': hashlib.sha256(str(i).encode()).hexdigest() * 4, 'points': [], 'x': i / 1000}
            expected[key] = value
            commands.append(resp('JSON.SET', key, '$', json.dumps(value, separators=(',', ':'))))
        start = time.perf_counter()
        output = docker('exec', '-i', lab.container, 'redis-cli', '--pipe', data=b''.join(commands)).stdout.decode()
        match = re.search(r'errors:\s*(\d+),\s*replies:\s*(\d+)', output)
        if not match or int(match[1]) != 0 or int(match[2]) != count:
            raise RuntimeError('Redis did not acknowledge every JSON.SET')
        return (time.perf_counter() - start) * 1000
    def verify(target=None):
        keys = list(expected)
        values = json.loads(cli('--json', 'JSON.MGET', *keys, '$', target=target))
        if not isinstance(values, list) or len(values) != len(keys):
            raise RuntimeError('Redis verification response malformed')
        missing = mismatched = 0
        for key, value in zip(keys, values):
            if value is None:
                missing += 1
            elif json.loads(value) != [expected[key]]:
                mismatched += 1
        return {'acknowledgedTotal': len(keys), 'recovered': len(keys) - missing,
                'missing': missing, 'payloadMismatches': mismatched}
    create(); lab.wait(ready)
    cases = []
    for mode in ['graceful_restart', 'sigkill_restart', 'kill_and_recreate']:
        progress('redis', mode)
        elapsed = write(len(expected), records)
        recovery = lab.fault(mode, create, ready)
        result = verify()
        cases.append({'fault': mode, 'newAcknowledged': records, 'writeBatchMs': elapsed,
                      'faultToQueryableMs': recovery, **result})
    # A stale-replica promotion is a separate, deliberately partitioned experiment.
    baseline = len(expected)
    replica, replica_volume = lab.prefix + '-replica', lab.volume('replica-data')
    lab.start_container(replica, ['--memory', '512m', '--network', network,
        '-v', replica_volume + ':/data', image, 'redis-server', '--appendonly', 'yes',
        '--appendfsync', 'everysec', '--replicaof', lab.container, '6379',
        '--bind', '0.0.0.0', '--protected-mode', 'no'])
    def replica_ready():
        info = cli('--raw', 'INFO', 'replication', target=replica).decode()
        return 'master_link_status:up' in info and verify(replica)['missing'] == 0
    lab.wait(replica_ready)
    progress('redis', 'replica_offline_then_stale_replica_promotion')
    docker('stop', '--time', '15', replica, timeout=30)
    gap_records = min(records, 250)
    write(baseline, gap_records)
    fault_start = time.perf_counter()
    docker('kill', '--signal', 'KILL', lab.container)
    docker('start', replica)
    lab.wait(lambda: cli('PING', target=replica).strip() == b'PONG')
    if cli('REPLICAOF', 'NO', 'ONE', target=replica).strip() != b'OK':
        raise RuntimeError('Replica promotion failed')
    outcome = verify(replica)
    replication = {'scenario': 'all_available_replicas_offline_during_acknowledged_writes_then_manual_stale_promotion',
        'baselineRecords': baseline, 'acknowledgedDuringReplicationGap': gap_records,
        'faultToPromotedQueryableMs': (time.perf_counter() - fault_start) * 1000,
        **outcome, 'automaticSentinelFailoverTested': False}
    return {'image': image, 'settings': {'appendonly': 'yes', 'appendfsync': 'everysec', 'min-replicas-to-write': 0},
            'cases': cases, 'replicationGap': replication}


def es_lab(lab, records):
    image, index = 'project-agora-elasticsearch:8.19.22-nori', 'durability-lab'
    volume = lab.volume('data')
    def create():
        lab.start_container(lab.container, ['--memory', '2g', '--network', 'none', '--hostname', 'localhost',
            '-v', volume + ':/usr/share/elasticsearch/data', '-e', 'discovery.type=single-node',
            '-e', 'xpack.security.enabled=false', '-e', 'network.host=127.0.0.1',
            '-e', 'ingest.geoip.downloader.enabled=false', '-e', 'ES_JAVA_OPTS=-Xms512m -Xmx512m', image])
    def http(path, method='GET', body=None):
        args = ['exec', '-i', lab.container, 'curl', '-sS', '-f', '--max-time', '10',
                '-X', method, '-H', 'Content-Type: application/x-ndjson', 'http://127.0.0.1:9200/' + path]
        if body is not None:
            args += ['--data-binary', '@-']
        return json.loads(docker(*args, data=body, timeout=15).stdout)
    def ready():
        return http('_cluster/health?wait_for_status=yellow&timeout=1s')['status'] in ['yellow', 'green']
    create(); lab.wait(ready)
    http(index, 'PUT', json.dumps({'settings': {'number_of_shards': 1, 'number_of_replicas': 0,
          'index.translog.durability': 'request'}}).encode())
    expected = {}
    cases = []
    for mode in ['graceful_restart', 'sigkill_restart', 'kill_and_recreate']:
        progress('elasticsearch', mode)
        lines = []
        for i in range(len(expected), len(expected) + records):
            value = {'id': i, 'payload': hashlib.sha256(str(i).encode()).hexdigest() * 4}
            expected[str(i)] = value
            lines.extend([json.dumps({'index': {'_index': index, '_id': str(i)}}), json.dumps(value)])
        start = time.perf_counter()
        ack = http('_bulk?refresh=false', 'POST', ('\n'.join(lines) + '\n').encode())
        if ack.get('errors') or len(ack['items']) != records or any(x['index']['status'] not in [200, 201] for x in ack['items']):
            raise RuntimeError('Elasticsearch did not acknowledge all bulk items')
        write_ms = (time.perf_counter() - start) * 1000
        # No explicit flush or refresh before the fault: exercise translog recovery.
        recovery = lab.fault(mode, create, ready)
        docs = http(index + '/_mget', 'POST', json.dumps({'ids': list(expected)}).encode())['docs']
        missing = sum(not doc.get('found') for doc in docs)
        mismatched = sum(doc.get('found') and doc.get('_source') != expected[doc['_id']] for doc in docs)
        cases.append({'fault': mode, 'newAcknowledged': records, 'acknowledgedTotal': len(expected),
            'recovered': len(expected) - missing, 'missing': missing, 'payloadMismatches': mismatched,
            'writeBatchMs': write_ms, 'faultToQueryableMs': recovery})
    return {'image': image, 'settings': {'translog.durability': 'request', 'number_of_replicas': 0}, 'cases': cases}


def sql_lab(lab, records):
    image, database = 'mcr.microsoft.com/mssql/server:2022-CU27-ubuntu-22.04', 'durability_lab'
    volume = lab.volume('data')
    env = lab.config / 'sql.env'
    env.write_text('ACCEPT_EULA=Y\nMSSQL_PID=Express\nMSSQL_MEMORY_LIMIT_MB=1024\nMSSQL_SA_PASSWORD=Durability!' + secrets.token_hex(24) + '\n')
    env.chmod(0o600)
    def create():
        lab.start_container(lab.container, ['--memory', '3g', '--network', 'none', '--env-file', str(env),
                                         '-v', volume + ':/var/opt/mssql', image])
    def sql(query, db='master', timeout=40):
        return docker('exec', '-i', lab.container, 'bash', '-lc',
            'exec /opt/mssql-tools18/bin/sqlcmd -S tcp:127.0.0.1,1433 -U sa -P "$MSSQL_SA_PASSWORD" -C -b -l 3 -t 30 -h -1 -w 65535 -d ' + db,
            data=('SET NOCOUNT ON;\n' + query + '\nGO\n').encode(), timeout=timeout).stdout.decode().strip()
    def ready():
        return sql("SELECT 'ready';", timeout=6) == 'ready'
    def graceful_stop():
        # SQL Server's SHUTDOWN checkpoints databases before exiting. A dropped
        # sqlcmd connection is expected; separately verify the process stopped.
        try:
            sql('SHUTDOWN;', timeout=45)
        except RuntimeError:
            pass
        lab.wait(lambda: not json.loads(docker('inspect', lab.container).stdout)[0]['State']['Running'], timeout=45)
    create(); lab.wait(ready)
    sql('CREATE DATABASE ' + database + ';')
    sql('ALTER DATABASE ' + database + ' SET RECOVERY FULL; ALTER DATABASE ' + database + ' SET DELAYED_DURABILITY=DISABLED;')
    sql('CREATE TABLE Probe(id int NOT NULL PRIMARY KEY, payload varchar(512) NOT NULL);', database)
    accepted, cases = 0, []
    def verify():
        return json.loads(sql("SELECT COUNT(*) AS recovered, SUM(CASE WHEN payload=REPLICATE('x',256)+CONVERT(varchar(20),id) THEN 0 ELSE 1 END) AS payloadMismatches FROM Probe WHERE id>=0 AND id<" + str(accepted) + ' FOR JSON PATH, WITHOUT_ARRAY_WRAPPER;', database))
    for mode in ['graceful_restart', 'sigkill_restart', 'kill_and_recreate']:
        progress('mssql', mode)
        query = "DECLARE @i int=" + str(accepted) + '; WHILE @i<' + str(accepted + records) + " BEGIN BEGIN TRAN; INSERT INTO Probe VALUES(@i,REPLICATE('x',256)+CONVERT(varchar(20),@i)); COMMIT TRAN; PRINT CONCAT('ACK:',@i); SET @i=@i+1; END;"
        start = time.perf_counter()
        output = sql(query, database)
        acknowledgements = [int(x) for x in re.findall(r'^ACK:(\d+)\s*$', output, re.MULTILINE)]
        if acknowledgements != list(range(accepted, accepted + records)):
            raise RuntimeError('SQL did not acknowledge every committed transaction')
        write_ms = (time.perf_counter() - start) * 1000
        accepted += records
        recovery = lab.fault(mode, create, ready, graceful_stop)
        outcome = lab.wait(lambda: verify())
        cases.append({'fault': mode, 'newAcknowledged': records, 'acknowledgedTotal': accepted,
            'missing': accepted - outcome['recovered'], 'writeBatchMs': write_ms,
            'faultToQueryableMs': recovery, **outcome})
    # A separate transaction never reaches COMMIT; crash recovery must undo it.
    progress('mssql', 'uncommitted_transaction_rollback')
    query = "SET NOCOUNT ON; BEGIN TRAN; INSERT INTO Probe VALUES(-1,'uncommitted'); WAITFOR DELAY '00:01:00'; COMMIT;"
    process = subprocess.Popen(['docker', 'exec', '-i', lab.container, 'bash', '-lc',
        'exec /opt/mssql-tools18/bin/sqlcmd -S tcp:127.0.0.1,1433 -U sa -P "$MSSQL_SA_PASSWORD" -C -b -d ' + database],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    process.stdin.write((query + '\nGO\n').encode()); process.stdin.close()
    lab.wait(lambda: sql('SELECT COUNT(*) FROM Probe WITH(NOLOCK) WHERE id=-1;', database).strip() == '1')
    recovery = lab.fault('sigkill_restart', create, ready)
    process.wait(timeout=10)
    uncommitted = int(sql('SELECT COUNT(*) FROM Probe WHERE id=-1;', database).strip())
    return {'image': image, 'settings': {'edition': 'Express', 'recovery_model': 'FULL', 'delayed_durability': 'DISABLED'},
        'gracefulStopMethod': 'SQL SHUTDOWN (engine checkpoint and exit)', 'cases': cases, 'uncommittedRollback': {'uncommittedRowsAfterRecovery': uncommitted,
            'committed': verify(), 'faultToQueryableMs': recovery}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', choices=['all', 'redis', 'elasticsearch', 'mssql'], default='all')
    parser.add_argument('--records', type=int, default=1000)
    parser.add_argument('--output', type=Path, default=Path('results/database-durability.json'))
    args = parser.parse_args()
    if not 1 <= args.records <= 10000:
        parser.error('records must be between 1 and 10000')
    run_id = uuid.uuid4().hex[:12]
    report = {'runId': run_id, 'startedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'recordsPerCase': args.records, 'productionFaultsInjected': False,
        'scope': 'Acknowledged-write retention after clean restart, process SIGKILL and container replacement on the same Docker volume',
        'limitations': ['Host OS page cache survives SIGKILL; this is not a power-loss test',
            'No physical disk loss, bit rot, backup restore or whole-host failover test',
            'Redis stale promotion is manual and deliberately follows a replication outage; Sentinel RTO not measured',
            'Lab uses private loopback/internal networking; TLS and app end-to-end crash semantics are outside this DB-only experiment'],
        'databases': {}, 'cleanupFailures': []}
    def execute(name, function):
        lab = Lab(run_id, name)
        try:
            return function(lab, args.records), None
        finally:
            failures = lab.cleanup()
            if failures:
                report['cleanupFailures'].extend(failures)
    functions = {'redis': redis_lab, 'elasticsearch': es_lab, 'mssql': sql_lab}
    selected = functions if args.database == 'all' else {args.database: functions[args.database]}
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(execute, name, fn): name for name, fn in selected.items()}
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                report['databases'][name] = future.result()[0]
                progress(name, 'completed_and_cleaned')
            except Exception as error:
                report['databases'][name] = {'errorType': type(error).__name__, 'error': str(error)}
                progress(name, 'failed_and_cleaned')
            args.output.write_text(json.dumps(report, indent=2) + '\n')
    report['finishedAt'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'report': str(args.output), 'databases': list(report['databases']), 'cleanupFailures': report['cleanupFailures']}), flush=True)
    return 1 if report['cleanupFailures'] or any('error' in result for result in report['databases'].values()) else 0

if __name__ == '__main__':
    raise SystemExit(main())
