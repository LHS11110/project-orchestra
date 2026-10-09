import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from orchestra.startup import DB_SERVICES, Runner, create_plan, main, read_settings, repositories


class StartupTests(unittest.TestCase):
    def setUp(self): self.repos = repositories(Path('/workspace'))
    def test_each_storage_waits_before_next_node(self):
        plan = create_plan(self.repos, 'development', initialize=True)
        starts = [step for step in plan if step.name.startswith('Start and wait:')]
        self.assertEqual([step.name.split(': ', 1)[1] for step in starts[:8]], list(DB_SERVICES))
        for step in starts[:8]: self.assertIn('--wait', step.command)
        self.assertFalse(any('redisinsight' in arg for step in plan for arg in step.command))
    def test_backend_order_and_tls_initialization(self):
        plan = create_plan(self.repos, 'development', sql_ca=True)
        names = [step.name for step in plan]
        self.assertLess(names.index('Refresh backend and frontend TLS volumes'), names.index('Start and wait: spring'))
        self.assertLess(names.index('Start and wait: spring'), names.index('Start and wait: cpp'))
        self.assertLess(names.index('Start and wait: cpp'), names.index('Start and wait: Phoenix broker'))
        for step in plan:
            if step.name in ('Start and wait: spring', 'Start and wait: cpp'):
                self.assertIn('--no-deps', step.command)
                self.assertIn('--force-recreate', step.command)
                self.assertTrue(any(arg.endswith('docker-compose.sql-ca.yml') for arg in step.command))
    def test_search_waits_for_tls_and_storage_before_gateway(self):
        plan = create_plan(self.repos, "production", build=False)
        names = [step.name for step in plan]
        search = next(step for step in plan if step.name == "Start and wait: E5 search projection")
        self.assertLess(names.index("Refresh backend and frontend TLS volumes"), names.index(search.name))
        self.assertLess(names.index("Start storage broker and service network"), names.index(search.name))
        self.assertLess(names.index(search.name), names.index("Start and verify HTTPS Nginx: production"))
        self.assertIn("--no-build", search.command)
        self.assertIn("Ensure search projection schema and account", names)

    def test_development_starts_vite_before_gateway(self):
        names = [step.name for step in create_plan(self.repos, 'development')]
        self.assertLess(names.index('Prepare gateway-owned web network if missing'), names.index('Start and wait: Vite frontend'))
        self.assertLess(names.index('Start and wait: Vite frontend'), names.index('Start and verify HTTPS Nginx: development'))
    def test_production_exports_static_files_before_switch_and_stop(self):
        plan = create_plan(self.repos, 'production', build=False)
        names = [step.name for step in plan]
        self.assertLess(names.index('Export static frontend release'), names.index('Start and verify HTTPS Nginx: production'))
        self.assertLess(names.index('Start and verify HTTPS Nginx: production'), names.index('Stop managed Vite frontend if running'))
        self.assertFalse(any('Initialize' in name or 'Build' in name or 'Vite frontend' == name for name in names))
        exporter = next(step for step in plan if step.name == 'Export static frontend release')
        self.assertNotIn('--build', exporter.command)
        self.assertNotIn('--no-build', exporter.command) # Compose run has no such option
        self.assertIn('Verify static exporter image exists', names)
    def test_plan_never_executes_commands_or_creates_lock(self):
        with tempfile.TemporaryDirectory() as directory, patch('orchestra.startup.ROOT', Path(directory)), \
             patch.object(Runner, 'call', side_effect=AssertionError('Must not execute')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['development', '--workspace', directory, '--plan']), 0)
            self.assertFalse(Path(directory, 'results').exists())
    def test_failure_stops_before_later_nodes_and_releases_lock(self):
        calls = []
        def execute(runner, step):
            calls.append(step.name)
            if step.name == 'Start and wait: spring': raise RuntimeError('fixture failed')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); be = root / 'project-agora-BE'; be.mkdir()
            (be / '.env').write_text('CPP_SQL_CA_CERT_HOST_PATH=\n'); (be / '.env').chmod(0o600)
            with patch('orchestra.startup.ROOT', root), patch.object(Runner, 'preflight'), \
                 patch.object(Runner, 'execute', execute), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['production', '--workspace', directory]), 1)
            self.assertNotIn('Start and wait: cpp', calls)
            self.assertFalse((root / 'results/.startup-lock').exists())
    def test_missing_web_network_creates_but_does_not_start_gateway(self):
        runner = Runner(self.repos, 'development', 300, True); commands = []
        def call(command, cwd, quiet=False, optional=False):
            commands.append(command)
            return subprocess.CompletedProcess(command, 1 if optional else 0, '', '')
        runner.call = call
        step = next(step for step in create_plan(self.repos, 'development') if step.action == 'web-network')
        runner.execute(step)
        self.assertIn('create', commands[-1]); self.assertNotIn('up', commands[-1])
    def test_gateway_is_recreated_and_checked_with_tls(self):
        runner = Runner(self.repos, 'production', 123, True); commands = []
        runner.call = lambda command, cwd, **kwargs: commands.append(command)
        plan = create_plan(self.repos, 'production')
        runner.execute(next(step for step in plan if step.action == 'gateway-start'))
        runner.execute(next(step for step in plan if step.action == 'gateway-probe'))
        self.assertTrue(any('--force-recreate' in command and '--wait' in command for command in commands))
        probes = [command for command in commands if command[:2] == ('docker', 'exec')]
        self.assertEqual(len(probes), 2)
        self.assertTrue(all('--cacert' in command and command[-1].startswith('https://') for command in probes))
    def test_env_values_are_not_shell_evaluated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, '.env'); path.write_text('ORCHESTRA_TEST_LITERAL=$(echo forbidden)\n'); path.chmod(0o600)
            self.assertEqual(read_settings(path)['ORCHESTRA_TEST_LITERAL'], '$(echo forbidden)')


if __name__ == '__main__': unittest.main()
