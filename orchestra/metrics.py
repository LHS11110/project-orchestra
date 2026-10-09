"""Sample public Docker resource counters without rendering container environments."""
import argparse
import json
from pathlib import Path
import time
from .config import load_config
from .docker import Docker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    parser.add_argument('--duration', type=int, default=120)
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('--output', default='results/resources.jsonl')
    args = parser.parse_args()
    if not 1 <= args.duration <= 3600 or args.interval < 1: parser.error('Duration must be 1..3600s; interval >=1s')
    nodes = load_config(args.config)['nodes']
    docker = Docker()
    destination = Path(args.output); destination.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.duration
    with destination.open('w') as output:
        while time.monotonic() < deadline:
            try:
                rows = docker.resource_stats([node['container'] for node in nodes.values()])
            except (RuntimeError, OSError, ValueError, TimeoutError): rows = {}
            timestamp = time.time()
            for name, node in nodes.items():
                row = rows.get(node['container'])
                resources = {key: row.get(key) for key in ['CPUPerc', 'MemUsage', 'MemPerc', 'NetIO', 'BlockIO', 'PIDs']} if row else {'unavailable': True}
                output.write(json.dumps({'at': timestamp, 'node': name, 'resources': resources}) + '\n')
            output.flush()
            time.sleep(min(args.interval, max(0, deadline - time.monotonic())))


if __name__ == '__main__': main()
