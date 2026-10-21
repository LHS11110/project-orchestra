"""Real faults on disposable fixtures only. Never targets Agora containers."""
import ipaddress
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestra.chaos import plan, run
from orchestra.docker import Docker


def exercise(docker, subnet=None):
    suffix = uuid.uuid4().hex[:12]
    network, container = 'orchestra-lab-net-' + suffix, 'orchestra-lab-' + suffix
    mode = 'static' if subnet else 'dynamic'
    image = 'python:3.14-alpine'
    # Let Docker choose an unused subnet for the dynamic fixture. Reuse that
    # subnet for an explicitly configured static fixture after removing this one.
    docker.call('network', 'create', *(['--subnet', subnet] if subnet else []), network)
    try:
        configs = json.loads(docker.call('network', 'inspect', '--format', '{{json .IPAM.Config}}', network))
        subnet = next(entry['Subnet'] for entry in configs if ipaddress.ip_network(entry['Subnet']).version == 4)
        address = str(ipaddress.ip_network(subnet).network_address + 10)
        docker.call('run', '-d', '--name', container, '--network', network, '--network-alias', 'fixture',
                    *(['--ip', address] if mode == 'static' else []),
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
        try:
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
                    assert state['cpu_quota'] in (original['cpu_quota'], -1)  # Both mean unlimited.
                    endpoint = state['networks'][network]
                    assert ipaddress.ip_address(endpoint['IPAddress']) in ipaddress.ip_network(subnet)
                    configured = (endpoint.get('IPAMConfig') or {}).get('IPv4Address')
                    if mode == 'static':
                        assert endpoint['IPAddress'] == address and configured == address
                    else:
                        assert not configured, 'Restoration must not turn dynamic IPAM into static IPAM'
                    assert 'fixture' in endpoint['Aliases']
                    # Retrying recovery after success must be harmless.
                    docker.restore(original, fault, spec['network'])
                    print(f'{mode} / {fault}: injected and restored', flush=True)
        finally:
            docker.fault = inject
        return subnet
    finally:
        subprocess.run(['docker', 'rm', '-f', container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        docker.call('network', 'rm', network)


def main():
    docker = Docker()
    print('Docker Engine: ' + docker.call('version', '--format', '{{.Server.Version}}').strip(), flush=True)
    subnet = exercise(docker)
    exercise(docker, subnet=subnet)


if __name__ == '__main__': main()
