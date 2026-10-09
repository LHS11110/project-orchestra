import copy
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

from orchestra import chaos
from orchestra.config import secure_url, load_env, load_config
from orchestra.docker import Docker
from orchestra.protocols import RespClient, discover_primary, odbc_value, ws_receive


class FakeDocker:
    def __init__(self, fault_error=False, restore_error=False):
        self.state = {'id': 'original', 'labels': {'com.docker.compose.project': 'lab'},
                      'networks': {'lab-net': {'IPAddress': '172.30.0.5', 'Aliases': ['fixture']}},
                      'nano_cpus': 0, 'state': {'Running': True, 'Paused': False}}
        self.calls, self.fault_error, self.restore_error = [], fault_error, restore_error
    def inspect(self, identifier): return copy.deepcopy(self.state)
    def ready(self, identifier): return self.state['state']['Running'] and not self.state['state']['Paused']
    def fault(self, state, fault, network, cpus):
        self.calls.append('fault')
        self.state['state']['Paused'] = True
        if self.fault_error: raise RuntimeError('Response lost after mutation')
    def restore(self, state, fault, network):
        self.calls.append('restore')
        if self.restore_error: raise RuntimeError('Daemon unavailable')
        self.state = copy.deepcopy(state)


CONFIG = {'recovery_timeout': 2, 'nodes': {'fixture': {'container': 'fixture', 'compose_project': 'lab',
             'networks': ['lab-net'], 'faults': ['pause', 'stop', 'cpu', 'disconnect']}}}


class ChaosTests(unittest.TestCase):
    def exercise(self, docker):
        spec = chaos.plan(CONFIG, 'fixture', 'pause', 1)
        ticks = [0]
        def clock(): return ticks[0]
        def sleep(seconds): ticks[0] += seconds
        with tempfile.TemporaryDirectory() as directory:
            try:
                path = chaos.run(CONFIG, spec, directory, docker, clock, sleep)
                return json.loads(path.read_text()), Path(directory, '.chaos-lock').exists()
            except BaseException:
                self.last_record = json.loads(next(Path(directory).glob('chaos-*.json')).read_text())
                self.lock_remains = Path(directory, '.chaos-lock').exists()
                raise
    def test_fault_restores_snapshot(self):
        docker = FakeDocker()
        record, locked = self.exercise(docker)
        self.assertEqual(record['status'], 'restored'); self.assertFalse(locked)
        self.assertEqual(docker.calls, ['fault', 'restore'])
        self.assertNotIn('Env', record['snapshot'])
    def test_uncertain_fault_response_still_rolls_back(self):
        docker = FakeDocker(fault_error=True)
        with self.assertRaises(RuntimeError): self.exercise(docker)
        self.assertEqual(docker.calls, ['fault', 'restore'])
        self.assertEqual(self.last_record['status'], 'restored_after_error')
        self.assertFalse(self.lock_remains)
    def test_incomplete_rollback_keeps_lock(self):
        with self.assertRaises(RuntimeError): self.exercise(FakeDocker(restore_error=True))
        self.assertTrue(self.lock_remains)
        self.assertEqual(self.last_record['status'], 'recovery_failed')
    def test_network_and_project_mismatch_reject_before_fault(self):
        for field, value in [('networks', {}), ('labels', {'com.docker.compose.project': 'production'})]:
            docker = FakeDocker(); docker.state[field] = value
            with self.assertRaises(ValueError): self.exercise(docker)
            self.assertEqual(docker.calls, [])
            self.assertEqual(self.last_record['status'], 'rejected')
    def test_interrupted_experiment_rolls_back(self):
        docker = FakeDocker()
        original = docker.fault
        def fault(*args):
            original(*args)
            raise InterruptedError('Interrupted')
        docker.fault = fault
        with self.assertRaises(InterruptedError): self.exercise(docker)
        self.assertEqual(self.last_record['status'], 'restored_after_error')
        self.assertEqual(docker.calls, ['fault', 'restore'])
    def test_cpu_fault_must_reduce_quota(self):
        docker = FakeDocker(); docker.state['nano_cpus'] = 100000000
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                chaos.run(CONFIG, chaos.plan(CONFIG, 'fixture', 'cpu', 1, cpus=.25), directory, docker)
        self.assertEqual(docker.calls, [])
    def test_plan_rejects_unknown_or_unbounded_targets(self):
        for args in [('unknown', 'pause', 1), ('fixture', 'pause', 301), ('fixture', 'disconnect', 1)]:
            with self.assertRaises(ValueError): chaos.plan(CONFIG, *args)
    def test_recovery_refuses_replacement(self):
        spec = chaos.plan(CONFIG, 'fixture', 'pause', 1)
        docker = FakeDocker(); old = docker.inspect('fixture'); old['id'] = 'replaced'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'journal.json')
            path.write_text(json.dumps({'experiment': spec, 'snapshot': old}))
            with self.assertRaises(ValueError): chaos.recover(CONFIG, path, docker)
        self.assertEqual(docker.calls, [])
    def test_monkey_is_bounded_and_reproducible(self):
        def run_sequence():
            with patch('orchestra.chaos.run', side_effect=lambda c, s, o: s['node']):
                return list(chaos.monkey(CONFIG, ['fixture'], 'pause', 1, 3, 42))
        self.assertEqual(run_sequence(), run_sequence())
        with self.assertRaises(ValueError): list(chaos.monkey(CONFIG, ['fixture'], 'pause', 1, 21, 42))


class ProtocolTests(unittest.TestCase):
    def response(self, payload):
        client = RespClient({}, 'unused'); client.stream = io.BytesIO(payload)
        return client.response()
    def test_bounded_resp_and_error_redaction(self):
        self.assertEqual(self.response(b'*2\r\n$6\r\nmaster\r\n:1\r\n'), ['master', 1])
        self.assertIsNone(self.response(b'$-1\r\n'))
        for bad in [b'*1025\r\n', b'$8388609\r\n', b'$-2\r\n', b'$3\r\nab', b'+' + b'x' * 4097]:
            with self.assertRaises((ValueError, ConnectionError)): self.response(bad)
        with self.assertRaisesRegex(RuntimeError, '^Redis rejected command$'):
            self.response(b'-ERR password-secret\r\n')
    def test_unknown_sentinel_primary_fails_closed(self):
        config = {'nodes': [{'identity': 'allowed', 'port': 6379}], 'sentinels': [{}], 'master_name': 'm'}
        with patch.dict(os.environ, ORCHESTRA_SENTINEL_PASSWORD='test'), patch('orchestra.protocols.RespClient') as client:
            client.return_value.command.return_value = ['unexpected', '6379']
            with self.assertRaises(ValueError): discover_primary(config, 'ca')
            client.return_value.close.assert_called_once()
    def test_websocket_filters_other_requests_and_answers_ping(self):
        class WebSocket:
            messages = iter(['{"type":"ping"}', '{"request_id":"other"}', '{"request_id":"ours"}'])
            sent = []
            def recv(self): return next(self.messages)
            def settimeout(self, value): pass
            def send(self, value): self.sent.append(json.loads(value))
        ws = WebSocket()
        self.assertEqual(ws_receive(ws, lambda m: m.get('request_id') == 'ours')['request_id'], 'ours')
        self.assertEqual(ws.sent, [{'type': 'pong'}])
    def test_odbc_escapes_delimiters(self): self.assertEqual(odbc_value('a};PWD=secret'), '{a}};PWD=secret}')
    def test_recovery_timeout_is_bounded(self):
        for timeout in [0, 601, float('nan'), float('inf')]:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory, 'config.json')
                path.write_text(json.dumps(dict(CONFIG, environment='lab', recovery_timeout=timeout)))
                with self.assertRaises(ValueError): load_config(path)
    def test_https_only_config(self):
        for bad in ['http://localhost', 'ws://localhost', 'https://user:pass@localhost', 'https://localhost/?token=x']:
            with self.assertRaises(ValueError): secure_url(bad)
    def test_env_requires_owner_only_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, '.env'); path.write_text('ORCHESTRA_UNIT_VALUE="test value"\n')
            path.chmod(0o644)
            with self.assertRaises(ValueError): load_env(path)
            path.chmod(0o600)
            with patch.dict(os.environ, {}, clear=True):
                load_env(path); self.assertEqual(os.environ['ORCHESTRA_UNIT_VALUE'], 'test value')
    def test_resource_stats_keep_valid_rows_when_a_sibling_is_missing(self):
        result = subprocess.CompletedProcess([], 1, '{"Name":"available","CPUPerc":"1%"}\n', 'missing sibling')
        with patch('orchestra.docker.subprocess.run', return_value=result):
            rows = Docker().resource_stats(['available', 'missing'])
        self.assertEqual(rows['available']['CPUPerc'], '1%')
        self.assertNotIn('missing', rows)
    def test_network_restore_preserves_identity(self):
        docker = Docker(); docker.inspect = lambda _: {'networks': {}, 'state': {}}
        calls = []; docker.call = lambda *args: calls.append(args)
        state = FakeDocker().state
        docker.restore(state, 'disconnect', 'lab-net')
        self.assertEqual(calls[0], ('network', 'connect', '--ip', '172.30.0.5', '--alias', 'fixture', 'lab-net', 'original'))
        docker.restore(state, 'cpu')
        self.assertEqual(calls[1], ('update', '--cpu-period', '100000', '--cpu-quota', '-1', 'original'))


if __name__ == '__main__': unittest.main()
