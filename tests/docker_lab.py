"""Real faults on a disposable fixture only. Never targets Agora containers."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestra.chaos import plan, run
from orchestra.docker import Docker


def main():
    docker = Docker()
    suffix = uuid.uuid4().hex[:12]
    network, container = 'orchestra-lab-net-' + suffix, 'orchestra-lab-' + suffix
    image = 'python:3.14-alpine'
    # Image has no data volumes or published ports; uses only a temporary network.
    docker.call('network', 'create', network)
    try:
        docker.call('run', '-d', '--name', container, '--network', network, '--network-alias', 'fixture',
                    '--label', 'com.docker.compose.project=orchestra-lab', image, 'sleep', '3600')
        original = docker.inspect(container)
        inject = docker.fault
        def verified_fault(state, fault, selected_network, cpus):
            inject(state, fault, selected_network, cpus)
            changed = docker.inspect(container)
            if fault == 'pause': assert changed['state']['Paused']
            if fault == 'stop': assert not changed['state']['Running']
            if fault == 'disconnect': assert network not in changed['networks']
            if fault == 'cpu': assert changed['cpu_quota'] / changed['cpu_period'] == cpus
        docker.fault = verified_fault
        config = {'recovery_timeout': 20, 'nodes': {'fixture': {'container': container,
            'compose_project': 'orchestra-lab', 'networks': [network], 'faults': ['pause', 'stop', 'disconnect', 'cpu']}}}
        with tempfile.TemporaryDirectory() as directory:
            for fault in ['pause', 'stop', 'disconnect', 'cpu']:
                spec = plan(config, 'fixture', fault, 1, network if fault == 'disconnect' else None)
                journal = run(config, spec, directory, docker)
                state = docker.inspect(container)
                assert json.loads(journal.read_text())['status'] == 'restored'
                assert state['id'] == original['id'] and docker.ready(container)
                assert state['nano_cpus'] == original['nano_cpus']
                assert state['cpu_quota'] in (original['cpu_quota'], -1) # 0 and -1 both mean unlimited
                assert state['networks'][network]['IPAddress'] == original['networks'][network]['IPAddress']
                assert 'fixture' in state['networks'][network]['Aliases']
                print(f'{fault}: injected and restored')
    finally:
        subprocess.run(['docker', 'rm', '-f', container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        docker.call('network', 'rm', network)


if __name__ == '__main__': main()
