"""Per-task Git clones. Each task keeps its branch and files across continuations."""
import os
from pathlib import Path
import shutil
import subprocess

from .worker import clean_environment


def git(root, *args):
    result = subprocess.run(
        ['git', '-c', 'core.hooksPath=' + os.devnull, '-C', str(root), *args],
        capture_output=True, text=True, encoding='utf-8', timeout=90,
        env=clean_environment(),
    )
    if result.returncode:
        raise ValueError('Workspace Git operation failed: ' + result.stderr[-1000:])
    return result.stdout.strip()


def prepare(task, runtime):
    project = dict(task['project'])
    if project.get('workspace_mode', 'shared') == 'shared':
        return project
    if project.get('source_path'):
        if not Path(project['path']).is_dir():
            raise ValueError('Task workspace is missing; restore it before continuing')
        return project
    source = Path(project['path']).resolve(strict=True)
    root = Path(runtime).resolve() / 'workspaces' / task['id']
    root.parent.mkdir(parents=True, exist_ok=True)
    if root.exists():
        raise ValueError('Unrecorded task workspace exists; inspect interrupted preparation')
    if project['workspace_mode'] == 'git':
        top = Path(git(source, 'rev-parse', '--show-toplevel')).resolve()
        base = git(source, 'rev-parse', 'HEAD')
        relative = source.relative_to(top)
        git(root.parent, 'clone', '--no-local', '--no-checkout', '--', str(top), str(root))
        branch = 'codex/task-' + task['id']
        git(root, 'checkout', '-b', branch, base)
        project.update(branch=branch, base_sha=base, checkout_root=str(root))
        target = root / relative
    else:
        if root.is_relative_to(source):
            raise ValueError('Copy workspace must be outside the project source')
        # Copy mode is for explicitly configured non-Git workspaces.
        def ignore(directory, names):
            excluded = {'.git', '.runtime', '.venv', 'node_modules', '__pycache__', 'config.local.json'}
            return [n for n in names if n in excluded or n == '.env' or n.startswith('.env.')]
        shutil.copytree(source, root, ignore=ignore, symlinks=True)
        target = root
        git(root, 'init', '-q')
        git(root, 'add', '.')
        git(root, '-c', 'user.name=AgentStack', '-c', 'user.email=router@localhost',
            'commit', '--allow-empty', '-qm', 'Task baseline')
        project.update(base_sha=git(root, 'rev-parse', 'HEAD'), checkout_root=str(root),
                       branch=git(root, 'branch', '--show-current'))
    for path in root.rglob('*'):
        if path.is_symlink() and not path.resolve().is_relative_to(root):
            raise ValueError('Workspace contains a symlink outside its task directory')
    if not target.resolve().is_relative_to(root):
        raise ValueError('App path escapes task directory')
    project.update(source_path=str(source), path=str(target), task_id=task['id'])
    return project


def diff(project):
    if not project.get('checkout_root'):
        return '该任务尚未创建 Git 工作目录。'
    root = Path(project['checkout_root'])
    # Include committed task changes plus index and worktree changes relative to baseline.
    changes = git(root, 'diff', '--no-ext-diff', '--no-textconv', '--stat', project['base_sha'])
    patch = git(root, 'diff', '--no-ext-diff', '--no-textconv', project['base_sha'])
    untracked = git(root, 'ls-files', '--others', '--exclude-standard')
    return (changes + '\n\n' + patch + '\n新增未跟踪文件\n' + untracked)[:60000]
