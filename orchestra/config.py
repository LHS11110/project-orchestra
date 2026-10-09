import json
import math
import os
from pathlib import Path
import shlex
from urllib.parse import urlsplit


def load_env(path=Path('.env')):
    if not path.exists(): return
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError('.env must be a regular owner-only file (0600)')
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        key, separator, value = line.partition('=')
        if not separator: raise ValueError('Invalid environment assignment')
        value = value.strip()
        if value.startswith(('"', "'")):
            parts = shlex.split(value, comments=True)
            if len(parts) != 1: raise ValueError('Invalid quoted environment assignment')
            value = parts[0]
        os.environ.setdefault(key.strip(), value)


def secret(name, required=True):
    value = os.environ.get(name, '')
    if required and not value: raise ValueError(f'Configure {name}')
    return value


def secure_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('https', 'wss') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Endpoints must use HTTPS/WSS without URL credentials')
    if parsed.query: raise ValueError('Configure credentials separately from endpoint URLs')
    return url.rstrip('/')


def load_config(path=None):
    file = Path(path or os.environ.get('ORCHESTRA_CONFIG', 'config/orchestra.json'))
    data = json.loads(file.read_text())
    if data.get('environment') not in ('lab', 'staging'):
        raise ValueError('Experiments require an explicitly configured lab or staging inventory')
    recovery = data.get('recovery_timeout', 120)
    if not isinstance(recovery, (int, float)) or not math.isfinite(recovery) or not 1 <= recovery <= 600:
        raise ValueError('Recovery timeout must be 1..600 seconds')
    nodes = data.get('nodes', {})
    if not nodes: raise ValueError('Inventory cannot be empty')
    containers = set()
    for name, node in nodes.items():
        container = node.get('container', '')
        if not container or container.startswith('-') or container in containers:
            raise ValueError('Container names must be unique and explicit')
        containers.add(container)
        networks = node.get('networks', [])
        if not isinstance(networks, list) or len(set(networks)) != len(networks) or any(not isinstance(n, str) or not n or n.startswith('-') for n in networks):
            raise ValueError('Networks must be unique explicit names')
        if not node.get('compose_project'): raise ValueError(f'{name}: configure compose_project')
        if set(node.get('faults', [])) - {'pause', 'stop', 'disconnect', 'cpu'}:
            raise ValueError('Unsupported fault')
    for endpoint in data.get('load', {}).values():
        if isinstance(endpoint, dict) and 'url' in endpoint: secure_url(endpoint['url'])
    return data
