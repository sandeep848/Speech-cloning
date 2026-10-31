"""Capture exact installed package pins after a real-model verification succeeds.

Run in the intended deployment environment, then review/commit the output. This
never presents the API-only test environment as a validated TTS environment.
"""
import argparse
from importlib.metadata import distributions
from pathlib import Path
import re


def capture(destination):
    pins = {}
    for package in distributions():
        name = package.metadata['Name']
        version = package.version
        if not name or not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or not re.fullmatch(r'[A-Za-z0-9_.+!-]+', version):
            raise RuntimeError('Cannot safely represent package as a version pin')
        key = name.lower().replace('_', '-')
        if key in pins and pins[key] != version:
            raise RuntimeError('Conflicting installed versions: ' + name)
        pins[key] = version
    with Path(destination).open('x', encoding='utf-8') as output:
        output.write('# Captured installed versions. Validate real synthesis before deployment.\n')
        output.writelines(name + '==' + pins[name] + '\n' for name in sorted(pins))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    capture(args.output)
