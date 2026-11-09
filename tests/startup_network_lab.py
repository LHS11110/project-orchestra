"""Reproduce the old IPAM collision and verify both cold-start orders in isolation."""
import ipaddress
import json
import subprocess
import uuid


def command(*args, check=True):
    result = subprocess.run(('docker', *args), capture_output=True, text=True, timeout=120)
    if check and result.returncode:
        raise RuntimeError('Docker network lab failed at ' + args[0])
    return result


def main():
    image = 'nginx:stable-alpine'
    if command('image', 'inspect', image, check=False).returncode:
        command('pull', image)
    suffix = uuid.uuid4().hex[:12]
    network = 'orchestra-ipam-' + suffix
    names = {key: network + '-' + key for key in ['broker', 'insight', *[str(i) for i in range(2, 8)]]}
    command('network', 'create', '--label', 'orchestra.lab=startup-ipam', network)
    try:
        info = json.loads(command('network', 'inspect', network).stdout)[0]
        subnet = next(ipaddress.ip_network(c['Subnet']) for c in info['IPAM']['Config'] if ':' not in c['Subnet'])
        address = lambda offset: str(subnet.network_address + offset)

        def create(key, offset=None):
            return command('run', '-d', '--name', names[key], '--label', 'com.docker.compose.project=orchestra-lab',
                           '--network', network, *(['--ip', address(offset)] if offset is not None else []),
                           image, 'sleep', '300', check=False)

        def endpoint(key):
            return json.loads(command('inspect', names[key]).stdout)[0]['NetworkSettings']['Networks'][network]

        for offset in range(2, 8):
            assert create(str(offset), offset).returncode == 0
        assert create('broker').returncode == 0
        assert endpoint('broker')['IPAddress'] == address(8)
        failed = create('insight', 8)
        assert failed.returncode and 'address already in use' in failed.stderr.lower()
        print('PASS old layout: dynamic storage broker takes .8 and prevents static RedisInsight startup', flush=True)
        for key in ('broker', 'insight'):
            command('rm', '-f', names[key])

        for order in [('broker', 'insight'), ('insight', 'broker')]:
            for key in order:
                assert create(key, {'broker': 21, 'insight': 20}[key]).returncode == 0
            endpoints = {key: endpoint(key) for key in order}
            for key, offset in [('broker', 21), ('insight', 20)]:
                assert endpoints[key]['IPAddress'] == address(offset)
                assert endpoints[key]['IPAMConfig']['IPv4Address'] == address(offset)
            assert len({e['IPAddress'] for e in endpoints.values()}) == 2
            command('stop', *[names[key] for key in order])
            command('start', *[names[key] for key in reversed(order)])
            assert all(endpoint(key)['IPAddress'] == endpoints[key]['IPAddress'] for key in order)
            print('PASS fixed static layout: ' + ' -> '.join(order) + ', including reversed restart order', flush=True)
            for key in order:
                command('rm', '-f', names[key])
    finally:
        for name in names.values():
            command('rm', '-f', name, check=False)
        command('network', 'rm', network)


if __name__ == '__main__':
    main()
