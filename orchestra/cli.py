import argparse
import json
from .config import load_config, load_env
from . import chaos
from .docker import Docker


def main():
    parser = argparse.ArgumentParser(description='Bounded Docker faults against an explicit Agora inventory')
    parser.add_argument('--config')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('inventory')
    for command in ('plan', 'run'):
        p = sub.add_parser(command)
        p.add_argument('--node', required=True)
        p.add_argument('--fault', choices=['pause', 'stop', 'disconnect', 'cpu'], required=True)
        p.add_argument('--duration', type=int, default=15)
        p.add_argument('--network')
        p.add_argument('--cpus', type=float, default=.25)
        p.add_argument('--output', default='results')
    p = sub.add_parser('recover'); p.add_argument('--journal', required=True)
    p = sub.add_parser('monkey')
    p.add_argument('--nodes', required=True, help='Comma-separated explicit node allowlist')
    p.add_argument('--fault', choices=['pause', 'stop', 'cpu'], default='pause')
    p.add_argument('--duration', type=int, default=10)
    p.add_argument('--iterations', type=int, default=3)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--output', default='results')
    args = parser.parse_args()
    try:
        load_env(); config = load_config(args.config)
        if args.command == 'inventory':
            docker, valid = Docker(), True
            for name, node in config['nodes'].items():
                try:
                    state = docker.inspect(node['container'])
                    matches = state['labels'].get('com.docker.compose.project') == node['compose_project'] and set(state['networks']) == set(node.get('networks', []))
                    ready = docker.ready(state['id'])
                    valid = valid and matches and ready
                    print(json.dumps({'node': name, 'matches': matches, 'ready': ready, 'networks': sorted(state['networks'])}))
                except (RuntimeError, OSError):
                    valid = False
                    print(json.dumps({'node': name, 'available': False}))
            if not valid: parser.exit(1, 'Inventory mismatch or unavailable target; review before injecting faults.\n')
        elif args.command in ('plan', 'run'):
            spec = chaos.plan(config, args.node, args.fault, args.duration, args.network, args.cpus)
            print(json.dumps(spec, indent=2) if args.command == 'plan' else chaos.run(config, spec, args.output))
        elif args.command == 'recover': print(chaos.recover(config, args.journal))
        else:
            for journal in chaos.monkey(config, args.nodes.split(','), args.fault, args.duration, args.iterations, args.seed, args.output): print(journal)
    except (OSError, ValueError, RuntimeError, InterruptedError, TimeoutError) as error:
        # Never render driver exceptions or environment values, which can contain credentials.
        parser.exit(1, f'{type(error).__name__}: experiment failed; inspect the journal and configured inventory.\n')


if __name__ == '__main__': main()
