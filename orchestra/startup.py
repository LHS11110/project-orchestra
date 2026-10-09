"""Sequential single-engine Agora startup; configuration remains owned by each repository."""
import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DB_SERVICES = ('mssql', 'elasticsearch', 'redis-primary', 'redis-replica-1', 'redis-replica-2',
               'redis-sentinel-1', 'redis-sentinel-2', 'redis-sentinel-3')


@dataclass(frozen=True)
class Step:
    name: str
    cwd: Path
    command: tuple = ()
    action: str = 'command'


def compose(repo, filename, *args, project=None, extra=()):
    command = ['docker', 'compose', '--project-directory', str(repo)]
    if filename != 'docker-compose.yml': command += ['--env-file', str(repo / '.env')]
    if project: command += ['--project-name', project]
    command += ['-f', str(repo / filename)]
    for file in extra: command += ['-f', str(repo / file)]
    return tuple(command + list(args))


def read_settings(path):
    if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError(f'Require an owner-only regular environment file: {path}')
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        key, separator, value = line.partition('=')
        if not separator: raise ValueError('Invalid environment assignment')
        if value.strip().startswith(('"', "'")):
            parts = shlex.split(value.strip(), comments=True)
            if len(parts) != 1: raise ValueError('Invalid quoted environment assignment')
            value = parts[0]
        values[key.strip()] = value.strip()
    return {**values, **os.environ}


def repositories(workspace):
    return {name: workspace / ('project-agora-' + suffix) for name, suffix in
            [('fe', 'FE'), ('be', 'BE'), ('db', 'DB'), ('wall', 'Wall')]}


def create_plan(repos, mode, wait=300, build=True, initialize=False, sql_ca=False):
    db, wall, be, fe = (repos[key] for key in ('db', 'wall', 'be', 'fe'))
    steps = [Step('Prepare Elasticsearch storage permissions', db,
                  ('bash', str(db / 'elasticsearch/prepare-storage.sh')))]
    for service in DB_SERVICES:
        steps.append(Step('Start and wait: ' + service, db, compose(db, 'docker-compose.yml',
            'up', '-d', '--wait', '--wait-timeout', str(wait),
            *(['--build' if build else '--no-build'] if service == 'elasticsearch' else []), service)))
    if initialize:
        steps.extend([
            Step('Initialize SQL schema and application account', db,
                compose(db, 'docker-compose.yml', 'run', '--rm', '--no-deps', 'mssql-init')),
            Step('Initialize Redis ACL/index and SQL registry', db, ('bash', str(db / 'redis/init-redis-sentinel.sh'))),
            Step('Initialize Elasticsearch accounts and indexes', db, ('bash', str(db / 'elasticsearch/init-elasticsearch.sh'))),
        ])
    if not initialize:
        steps.append(Step('Ensure search projection schema and account', db,
            ('bash', str(db / 'elasticsearch/init-search-index.sh'))))
    steps.append(Step('Start storage broker and service network', wall,
                      ('bash', str(wall / 'scripts/storage-broker-docker.sh'), 'up')))
    if build: steps.append(Step('Build backend images', be, ('bash', str(be / 'scripts/backend-docker.sh'), 'build')))
    steps.append(Step('Refresh backend and frontend TLS volumes', be, compose(be, 'docker-compose.backend.yml',
        'run', '--rm', '--no-deps', 'service-tls-init', project='agora-backend',
        extra=('docker-compose.sql-ca.yml',) if sql_ca else ())))
    for service in ('spring', 'cpp'):
        steps.append(Step('Start and wait: ' + service, be, compose(be, 'docker-compose.backend.yml',
            'up', '-d', '--no-build', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', str(wait), service,
            project='agora-backend', extra=('docker-compose.sql-ca.yml',) if sql_ca else ())))
    steps.append(Step('Start and wait: E5 search projection', be, compose(be, 'docker-compose.search.yml',
        'up', '-d', '--wait', '--wait-timeout', str(wait), '--build' if build else '--no-build', 'search-embedding')))
    steps.append(Step('Verify backend HTTPS and log account', be,
                      ('bash', str(be / 'scripts/backend-docker.sh'), 'health')))
    steps.append(Step('Refresh Phoenix TLS volume', wall, compose(wall, 'docker-compose.broker.yml',
        'run', '--rm', '--no-deps', 'broker-tls-init')))
    steps.append(Step('Start and wait: Phoenix broker', wall, compose(wall, 'docker-compose.broker.yml',
        'up', '-d', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', str(wait), '--build' if build else '--no-build', 'broker')))
    if mode == 'development':
        steps.extend([
            Step('Validate gateway TLS and service network', wall, (sys.executable, str(wall / 'scripts/preflight.py'))),
            Step('Prepare gateway-owned web network if missing', wall, action='web-network'),
            Step('Start and wait: Vite frontend', fe, compose(fe, 'compose.dev.yaml',
                'up', '-d', '--force-recreate', '--wait', '--wait-timeout', str(wait), '--build' if build else '--no-build', 'frontend')),
        ])
    else:
        if build: steps.append(Step('Build static frontend exporter', fe, compose(fe, 'compose.yaml', 'build', 'frontend-build')))
        steps.extend([
            Step('Verify static exporter image exists', fe, ('docker', 'image', 'inspect', '--format', '{{.Id}}', 'project-agora-frontend:local')),
            Step('Export static frontend release', fe, compose(fe, 'compose.yaml', 'run', '--rm', '--no-deps', 'frontend-build')),
        ])
    steps.append(Step('Start and verify HTTPS Nginx: ' + mode, wall, action='gateway-start'))
    if mode == 'production': steps.append(Step('Stop managed Vite frontend if running', fe, action='stop-vite'))
    steps.append(Step('Verify gateway frontend and Spring route', wall, action='gateway-probe'))
    return steps


class Runner:
    def __init__(self, repos, mode, wait, build):
        self.repos, self.mode, self.wait, self.build = repos, mode, wait, build
        self.environment = dict(os.environ, FRONTEND_MODE=mode, AGORA_BE_DIR=str(repos['be']),
            BACKEND_DEPENDENCY_WAIT_SECONDS=str(wait), BACKEND_START_WAIT_SECONDS=str(wait))

    def call(self, command, cwd, quiet=False, optional=False):
        result = subprocess.run(command, cwd=cwd, env=self.environment, text=True,
            stdout=subprocess.PIPE if quiet else None, stderr=subprocess.PIPE if quiet else None, timeout=3600)
        if result.returncode and not optional:
            # Do not render captured config output or secret-bearing driver errors.
            raise RuntimeError('Command failed for ' + cwd.name + '; inspect the named stage and service configuration')
        return result

    def preflight(self):
        for tool in ('docker', 'bash', 'curl', 'openssl'):
            if not shutil.which(tool): raise ValueError(f'Required dependency: {tool}')
        for path in [self.repos[k] / '.env' for k in ('fe', 'be', 'wall')] + [self.repos['db'] / k / '.env' for k in ('mssql', 'redis', 'elasticsearch')]:
            read_settings(path)
        backend, wall = (read_settings(self.repos[k] / '.env') for k in ('be', 'wall'))
        for key in ('JWT_SECRET', 'CPP_INTERNAL_API_TOKEN'):
            if len(backend.get(key, '').encode()) < 32 or backend.get(key) != wall.get(key):
                raise ValueError(key + ' must match between BE and Wall (at least 32 bytes)')
        self.call(('docker', 'compose', 'version'), ROOT, quiet=True)
        self.call(('docker', 'info'), ROOT, quiet=True)
        self.call((sys.executable, str(self.repos['db'] / 'ops/configure-db.py'), 'validate', '--profile', 'development'), self.repos['db'], quiet=True)
        self.call((sys.executable, str(self.repos['be'] / 'scripts/docker-preflight.py'), *(['--build'] if self.build else [])), self.repos['be'], quiet=True)
        for key, filename in [('db', 'docker-compose.yml'), ('wall', 'docker-compose.storage-broker.yml'),
                              ('wall', 'docker-compose.broker.yml'), ('wall', 'docker-compose.nginx.yml'),
                              ('be', 'docker-compose.search.yml'),
                              ('fe', 'compose.dev.yaml' if self.mode == 'development' else 'compose.yaml')]:
            self.call(compose(self.repos[key], filename, 'config', '--quiet'), self.repos[key], quiet=True)
        if not self.build:
            images = ['project-agora-search:local', 'project-agora-elasticsearch:8.19.22-nori', 'agora-spring:local', 'agora-cpp:local', 'project-agora-broker:local',
                      'project-agora-frontend:dev' if self.mode == 'development' else 'project-agora-frontend:local']
            for image in images:
                result = self.call(('docker', 'image', 'inspect', '--format', '{{.Id}}', image), ROOT, quiet=True, optional=True)
                if result.returncode: raise ValueError('Missing prebuilt image: ' + image)
        # Verify bind inputs before starting DB, without requiring an existing service network.
        result = self.call(compose(self.repos['wall'], 'docker-compose.nginx.yml', 'config', '--format', 'json'), self.repos['wall'], quiet=True)
        config = json.loads(result.stdout)
        for mount in config['services']['nginx'].get('volumes', []):
            if mount['type'] == 'bind' and not Path(mount['source']).is_file():
                raise ValueError('Missing gateway bind file for ' + mount['target'])
        broker = self.call(compose(self.repos['wall'], 'docker-compose.broker.yml', 'config', '--format', 'json'), self.repos['wall'], quiet=True)
        for definition in json.loads(broker.stdout)['services'].values():
            for mount in definition.get('volumes', []):
                if mount['type'] == 'bind' and not Path(mount['source']).exists():
                    raise ValueError('Missing broker bind input for ' + mount['target'])

    def execute(self, step):
        if step.action == 'command': self.call(step.command, step.cwd)
        elif step.action == 'web-network':
            exists = self.call(('docker', 'network', 'inspect', 'agora-web'), step.cwd, quiet=True, optional=True)
            if exists.returncode:
                # Compose owns the network; create an unstarted gateway only on a fresh network.
                self.call(compose(step.cwd, 'docker-compose.nginx.yml', 'create', '--no-deps', 'nginx'), step.cwd)
        elif step.action == 'stop-vite':
            found = self.call(('docker', 'ps', '-a', '--filter', 'name=^/agora-frontend-dev$', '--format', '{{.Names}}'), step.cwd, quiet=True)
            if found.stdout.strip():
                metadata = self.call(('docker', 'inspect', '--format', '{{index .Config.Labels "com.docker.compose.project"}}', 'agora-frontend-dev'), step.cwd, quiet=True)
                if metadata.stdout.strip() != 'project-agora-fe': raise ValueError('Refusing to stop a frontend owned by a different Compose project')
                self.call(compose(step.cwd, 'compose.dev.yaml', 'stop', 'frontend'), step.cwd)
        elif step.action == 'gateway-start':
            self.call((sys.executable, str(step.cwd / 'scripts/preflight.py')), step.cwd)
            if self.mode == 'production':
                self.call(compose(step.cwd, 'docker-compose.nginx.yml', 'run', '--rm', '--no-deps', '--entrypoint', 'sh',
                    'nginx', '-c', 'test -f /usr/share/nginx/html/current/index.html'), step.cwd)
            self.call(compose(step.cwd, 'docker-compose.nginx.yml', 'up', '-d', '--force-recreate', '--wait',
                '--wait-timeout', str(self.wait), 'nginx'), step.cwd)
            self.call(compose(step.cwd, 'docker-compose.nginx.yml', 'exec', '-T', 'nginx', 'nginx', '-t'), step.cwd)
        elif step.action == 'gateway-probe':
            for path in ('/', '/api/auth/health'):
                self.call(('docker', 'exec', 'agora-nginx', 'curl', '--fail', '--silent', '--show-error',
                    '--connect-timeout', '3', '--max-time', '10', '--cacert', '/etc/nginx/ssl/agora/ca.pem',
                    '--output', '/dev/null', 'https://localhost' + path), step.cwd)
        else: raise ValueError('Unknown startup action')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['development', 'production'])
    parser.add_argument('--workspace', type=Path, default=ROOT.parent, help='Parent of the four Agora repositories')
    parser.add_argument('--tls-dir', type=Path, help='Run existing configuration setup with manually supplied certificates first')
    parser.add_argument('--public-origin', default='https://localhost:8443')
    parser.add_argument('--wait-timeout', type=int, default=300)
    parser.add_argument('--no-build', action='store_true', help='Require existing backend, broker and FE images')
    parser.add_argument('--initialize', action='store_true', help='Apply DB schema/account/index initialization (production opt-in)')
    parser.add_argument('--skip-initialize', action='store_true', help='Skip default development initialization')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--plan', action='store_true', help='Print order; no Docker/config/file mutations')
    group.add_argument('--check', action='store_true', help='Validate inputs without starting services')
    args = parser.parse_args(argv)
    if not 30 <= args.wait_timeout <= 1800: parser.error('--wait-timeout must be 30..1800 seconds')
    if args.initialize and args.skip_initialize: parser.error('Choose only one initialization option')
    if args.check and args.tls_dir: parser.error('--check cannot run configuration setup; omit --tls-dir')
    repos = repositories(args.workspace.expanduser().resolve())
    initialize = args.initialize or (args.mode == 'development' and not args.skip_initialize)
    runner = Runner(repos, args.mode, args.wait_timeout, not args.no_build)
    stage = 'preflight'
    try:
        if args.plan:
            ca = False
            if (repos['be'] / '.env').is_file():
                ca = bool(read_settings(repos['be'] / '.env').get('CPP_SQL_CA_CERT_HOST_PATH'))
            if args.tls_dir: print('0. Configure repositories using supplied certificates; no certificates generated')
            for index, step in enumerate(create_plan(repos, args.mode, args.wait_timeout, not args.no_build, initialize, ca), 1):
                print(f'{index}. {step.name}')
                if step.command: print('   ' + shlex.join(step.command))
            return 0
        # This lock covers development/production mode switches from this workspace.
        lock = ROOT / 'results/.startup-lock'
        lock.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, 'w') as stream: stream.write(str(os.getpid()))
            if args.tls_dir:
                stage = 'configure repositories'
                runner.call((sys.executable, str(repos['fe'] / 'scripts/setup-projects.py'), '--backend-dir', str(repos['be']),
                    '--wall-dir', str(repos['wall']), '--db-dir', str(repos['db']), '--tls-dir', str(args.tls_dir.expanduser().resolve()),
                    '--public-origin', args.public_origin), repos['fe'])
            stage = 'preflight'; runner.preflight()
            if args.check:
                print('Startup inputs validated; no services started.'); return 0
            sql_ca = bool(read_settings(repos['be'] / '.env').get('CPP_SQL_CA_CERT_HOST_PATH'))
            steps = create_plan(repos, args.mode, args.wait_timeout, not args.no_build, initialize, sql_ca)
            for index, step in enumerate(steps, 1):
                stage = step.name; print(f'[{index}/{len(steps)}] {stage}', flush=True); runner.execute(step)
            print('Agora startup complete: ' + args.mode)
            return 0
        finally: lock.unlink(missing_ok=True)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        if isinstance(error, subprocess.SubprocessError): error = RuntimeError('Command timed out or was interrupted')
        print(f'{type(error).__name__}: startup stopped at {stage}: {error}. Completed services remain running; fix this stage and rerun.', file=sys.stderr)
        return 1


if __name__ == '__main__': sys.exit(main())
