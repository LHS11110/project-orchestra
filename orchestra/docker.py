import json
import subprocess


class Docker:
    def call(self, *args):
        result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=45)
        if result.returncode: raise RuntimeError('Docker command failed: ' + args[0])
        return result.stdout

    def resource_stats(self, identifiers):
        # Docker can return successful rows and an error for a missing sibling.
        result = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{json .}}', *identifiers],
                                capture_output=True, text=True, timeout=45)
        rows = {}
        for line in result.stdout.splitlines():
            try:
                row = json.loads(line)
                rows[row['Name']] = {key: row.get(key) for key in ['CPUPerc', 'MemUsage', 'MemPerc', 'NetIO', 'BlockIO', 'PIDs']}
            except (ValueError, KeyError, TypeError): continue
        return rows

    def inspect(self, identifier):
        raw = json.loads(self.call('inspect', identifier))[0]
        # Docker inspect contains environment secrets; expose only needed metadata.
        state = {key: raw['State'].get(key) for key in ('Running', 'Paused', 'Status', 'ExitCode')}
        state['Health'] = {'Status': raw['State'].get('Health', {}).get('Status', 'healthy')}
        return {'id': raw['Id'], 'state': state,
                'labels': {'com.docker.compose.project': (raw['Config'].get('Labels') or {}).get('com.docker.compose.project')},
                'networks': raw['NetworkSettings']['Networks'],
                'nano_cpus': raw['HostConfig'].get('NanoCpus', 0),
                'cpu_quota': raw['HostConfig'].get('CpuQuota', 0), 'cpu_period': raw['HostConfig'].get('CpuPeriod', 0)}

    def fault(self, state, kind, network=None, cpus=0.25):
        identifier = state['id']
        if kind == 'pause': self.call('pause', identifier)
        elif kind == 'stop': self.call('stop', '--time', '3', identifier)
        elif kind == 'cpu':
            if state['nano_cpus']: self.call('update', '--cpus', str(cpus), identifier)
            else:
                period = state.get('cpu_period') or 100000
                self.call('update', '--cpu-period', str(period), '--cpu-quota', str(int(period * cpus)), identifier)
        elif kind == 'disconnect': self.call('network', 'disconnect', network, identifier)
        else: raise ValueError('Unsupported fault')

    def restore(self, state, kind, network=None):
        current = self.inspect(state['id'])
        if kind == 'pause' and current['state'].get('Paused'): self.call('unpause', state['id'])
        elif kind == 'stop' and not current['state'].get('Running'): self.call('start', state['id'])
        elif kind == 'cpu':
            if state['nano_cpus']: self.call('update', '--cpus', str(state['nano_cpus'] / 1_000_000_000), state['id'])
            else:
                # Docker ignores --cpus 0. Preserve unlimited quotas using the CFS API.
                self.call('update', '--cpu-period', str(state.get('cpu_period') or 100000),
                          '--cpu-quota', str(state.get('cpu_quota') or -1), state['id'])
        elif kind == 'disconnect' and network not in current['networks']:
            endpoint = state['networks'][network]
            args = ['network', 'connect']
            if endpoint.get('IPAddress'): args += ['--ip', endpoint['IPAddress']]
            if endpoint.get('GlobalIPv6Address'): args += ['--ip6', endpoint['GlobalIPv6Address']]
            for alias in endpoint.get('Aliases') or []: args += ['--alias', alias]
            # Restore driver options where explicitly configured.
            for key, value in (endpoint.get('DriverOpts') or {}).items(): args += ['--driver-opt', key + '=' + value]
            self.call(*args, network, state['id'])

    def ready(self, identifier):
        state = self.inspect(identifier)['state']
        return bool(state.get('Running')) and not state.get('Paused') and state.get('Health', {}).get('Status', 'healthy') == 'healthy'
