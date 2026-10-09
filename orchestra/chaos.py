import json
import os
from pathlib import Path
import random
import signal
import time
import uuid
from .docker import Docker


def journal_write(path, data):
    temporary = path.with_suffix('.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(data, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    parent = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(parent)
    finally: os.close(parent)


def plan(config, node, fault, duration, network=None, cpus=0.25):
    target = config['nodes'].get(node)
    if not target or fault not in target['faults']: raise ValueError('Node or fault is not in the inventory allowlist')
    if not 1 <= duration <= 300: raise ValueError('Fault duration must be between 1 and 300 seconds')
    if fault == 'disconnect' and network not in target.get('networks', []):
        raise ValueError('Network is not in the node allowlist')
    if not 0.05 <= cpus <= 4: raise ValueError('CPU limit must be between 0.05 and 4 cores')
    return {'node': node, 'container': target['container'], 'compose_project': target['compose_project'],
            'fault': fault, 'duration': duration, 'network': network, 'cpus': cpus}


def run(config, spec, output='results', docker=None, clock=time.monotonic, sleep=time.sleep):
    docker = docker or Docker()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    lock = output / '.chaos-lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    path = output / ('chaos-' + uuid.uuid4().hex + '.json')
    with os.fdopen(descriptor, 'w') as stream: stream.write(str(path))
    record = {'version': 1, 'experiment': spec, 'started_at': time.time(), 'status': 'preflight'}
    state = None
    previous_handlers = {}
    def interrupted(signum, frame): raise InterruptedError('Experiment interrupted')
    try:
        state = docker.inspect(spec['container'])
        if state['labels'].get('com.docker.compose.project') != spec['compose_project']:
            raise ValueError('Container Compose project does not match the inventory')
        if set(state['networks']) != set(config['nodes'][spec['node']].get('networks', [])):
            raise ValueError('Container networks do not match the reviewed inventory')
        if not docker.ready(state['id']): raise ValueError('Target must be running, unpaused and healthy before the fault')
        if spec['fault'] == 'disconnect' and spec['network'] not in state['networks']:
            raise ValueError('Target is not attached to the selected network')
        existing_cpus = state['nano_cpus'] / 1_000_000_000 or (state.get('cpu_quota', 0) / (state.get('cpu_period') or 100000) if state.get('cpu_quota', 0) > 0 else 0)
        if spec['fault'] == 'cpu' and existing_cpus and spec['cpus'] >= existing_cpus:
            raise ValueError('CPU fault must reduce the existing quota')
        record['snapshot'] = state
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, interrupted)
        # Persist rollback metadata before mutation, including uncertain outcomes.
        record['status'] = 'injected_or_pending'
        journal_write(path, record)
        docker.fault(state, spec['fault'], spec['network'], spec['cpus'])
        deadline = clock() + spec['duration']
        observations = []
        while clock() < deadline:
            observations.append({'at': time.time(), 'target_ready': docker.ready(state['id'])})
            sleep(min(1, max(0, deadline - clock())))
        record['observations'] = observations
    except BaseException as error:
        record['error_type'] = type(error).__name__
        raise
    finally:
        try:
            if state and record['status'] == 'injected_or_pending':
                # Restore even if Docker accepted the fault but its response was lost.
                docker.restore(state, spec['fault'], spec['network'])
                timeout = clock() + config.get('recovery_timeout', 120)
                while not docker.ready(state['id']):
                    if clock() >= timeout: raise TimeoutError('Target did not recover')
                    sleep(1)
                record['status'] = 'restored' if 'error_type' not in record else 'restored_after_error'
                record['recovered_at'] = time.time()
            elif record['status'] == 'preflight': record['status'] = 'rejected'
        except BaseException as error:
            record['status'] = 'recovery_failed'
            record['recovery_error_type'] = type(error).__name__
            raise
        finally:
            try: journal_write(path, record)
            finally:
                for signum, handler in previous_handlers.items(): signal.signal(signum, handler)
                # Leave the lock after an incomplete rollback; recover is explicit.
                if record['status'] != 'recovery_failed': lock.unlink(missing_ok=True)
    return path


def recover(config, path, docker=None):
    docker = docker or Docker()
    path = Path(path).resolve()
    record = json.loads(path.read_text())
    spec = record['experiment']
    checked = plan(config, spec['node'], spec['fault'], spec['duration'], spec['network'], spec['cpus'])
    if checked != spec: raise ValueError('Journal does not match the configured inventory')
    state = record['snapshot']
    current = docker.inspect(spec['container'])
    if current['id'] != state['id'] or current['labels'].get('com.docker.compose.project') != spec['compose_project']:
        raise ValueError('Container was replaced; recovery must not modify its replacement')
    docker.restore(state, spec['fault'], spec['network'])
    deadline = time.monotonic() + config.get('recovery_timeout', 120)
    while not docker.ready(state['id']):
        if time.monotonic() >= deadline: raise TimeoutError('Recovery did not reach readiness')
        time.sleep(1)
    record['status'] = 'restored_manually'
    journal_write(path, record)
    lock = path.parent / '.chaos-lock'
    if lock.exists() and Path(lock.read_text()) == path: lock.unlink()
    return path


def monkey(config, candidates, fault, duration, iterations, seed, output='results'):
    if not 1 <= iterations <= 20: raise ValueError('Iterations must be between 1 and 20')
    randomizer = random.Random(seed)
    eligible = [node for node in candidates if node in config['nodes'] and fault in config['nodes'][node]['faults']]
    if not eligible or len(eligible) != len(candidates): raise ValueError('Every monkey candidate must support the selected fault')
    for _ in range(iterations):
        node = randomizer.choice(eligible)
        yield run(config, plan(config, node, fault, duration), output)
