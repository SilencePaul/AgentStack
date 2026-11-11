"""Run an app suite on the target and trial merge in disposable local clones."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def git(directory, *args):
    result = subprocess.run(
        ['git', '-c', 'core.hooksPath=' + os.devnull, '-C', str(directory), *args],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def compare(repo, base, head, app, command):
    if not command or not all(re.fullmatch('[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', s) for s in (base, head)):
        raise ValueError('Provide exact target and source commit IDs and a test command')
    if Path(app).is_absolute() or '..' in Path(app).parts:
        raise ValueError('Provide a relative app path')
    changed = git(repo, 'diff', '--name-only', base + '...' + head, '--', app)
    if not changed:
        raise ValueError('MR has no changes under configured app path')
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        for stage in ('baseline', 'trial_merge'):
            checkout = Path(tmp).resolve() / stage
            git(tmp, 'clone', '--no-local', '--no-checkout', '--', str(Path(repo).resolve()), str(checkout))
            git(checkout, 'fetch', '--no-tags', 'origin', base, head)
            git(checkout, 'checkout', '--detach', base)
            if stage == 'trial_merge':
                git(checkout, '-c', 'user.name=AgentStack', '-c', 'user.email=router@localhost',
                    'merge', '--no-commit', '--no-ff', head)
            cwd = (checkout / app).resolve()
            if not cwd.is_relative_to(checkout):
                raise ValueError('App directory escapes checkout')
            print(f'[{stage}] {app} {command!r}', flush=True)
            result = subprocess.run(command, cwd=cwd, timeout=900, check=False)
            results[stage] = result.returncode
    return {'base': base, 'head': head, 'app': app, **results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'base', 'head', 'app'):
        parser.add_argument(name)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    result = compare(args.repo, args.base, args.head, args.app, args.command)
    print(json.dumps(result), flush=True)
    raise SystemExit(int(bool(result['trial_merge'])))
