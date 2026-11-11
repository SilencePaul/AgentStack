"""Transactional commands from the authenticated local operator."""
import hashlib
import json
import re
from pathlib import Path

from .continuity import workspace_stamp


def command(store, body):
    if not isinstance(body, dict):
        raise ValueError('Invalid command')
    request_id = body.get('request_id', '')
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', request_id):
        raise ValueError('A unique request_id is required')
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    key = 'web-command:' + request_id
    with store.transaction():
        existing = store.meta(key)
        if existing:
            if existing['digest'] != digest:
                raise ValueError('Request id already used for different content')
            return existing['result']
        result = execute(store, body)
        store.set_meta(key, {'digest': digest, 'result': result})
        return result


def execute(store, body):
    action = body.get('action')
    if action == 'configure':
        pid = body.get('project_id')
        current = next((p for p in store.projects() if p['id'] == pid), None)
        if not current:
            raise ValueError('Unknown Team')
        mode = body.get('workspace_mode')
        if mode == 'git':
            from .workspaces import git
            git(Path(current['path']), 'rev-parse', '--verify', 'HEAD')
        project = store.update_project(pid, workspace_mode=mode, concurrency=body.get('concurrency'))
        store.event(None, 'team_configured', {'project_id': pid, 'operator': 'local-web',
                    'workspace_mode': project.get('workspace_mode'), 'concurrency': project.get('concurrency')})
        return {'project_id': pid}
    if action == 'create':
        task = store.submit(body.get('project_id'), body.get('goal'), 'web:' + body['request_id'])
        store.db.execute("UPDATE tasks SET status='awaiting_approval' WHERE id=?", (task['id'],))
        store.event(task['id'], 'approval_requested', {'operator': 'local-web', 'kind': 'start'})
        return {'task_id': task['id']}
    task = store.get(body.get('task_id'))
    if not task:
        raise ValueError('Unknown task')
    tid = task['id']
    if action == 'approve':
        if task['status'] != 'awaiting_approval':
            raise ValueError('Approval is stale')
        if body.get('goal_digest') != hashlib.sha256(task['goal'].encode()).hexdigest():
            raise ValueError('Task changed; reload before confirming')
        if type(body.get('approved')) is not bool:
            raise ValueError('Choose approve or reject')
        if body['approved']:
            store.db.execute("UPDATE tasks SET status='queued' WHERE id=?", (tid,))
        else:
            store.finish(tid, 'cancelled', '用户拒绝执行。')
    elif action == 'answer':
        checkpoint = task.get('continuation') or {}
        if checkpoint.get('input_kind') == 'confirmation':
            if body.get('question_id') != checkpoint.get('question_id') or task['status'] != 'waiting_input':
                raise ValueError('Confirmation is stale')
            if type(body.get('approved')) is not bool:
                raise ValueError('Choose approve or reject')
            if body['approved']:
                if workspace_stamp(task['project']['path']) != checkpoint['workspace']:
                    raise ValueError('Workspace changed; confirmation cannot be applied')
                answer = '确认执行本次问题中明确列出的操作，遵守原任务授权范围。'
                store.answer(tid, checkpoint['question_id'], answer, store.question_route(task))
            else:
                store.finish(tid, 'cancelled', '用户拒绝执行，工作目录保留。')
        else:
            answer = body.get('answer')
            if not isinstance(answer, str):
                raise ValueError('Provide an answer')
            store.answer(tid, body.get('question_id'), answer, store.question_route(task))
    elif action == 'stop':
        if task['status'] == 'running':
            store.set_meta('cancel:' + tid, True)
        elif task['status'] in ('queued', 'waiting_input', 'awaiting_approval'):
            store.finish(tid, 'cancelled', '用户已停止任务。')
        else:
            raise ValueError('Task has already ended')
    elif action == 'resume_project':
        store.set_meta('project_paused:' + task['project_id'], False)
    else:
        raise ValueError('Unknown command')
    store.event(tid, 'operator_command', {'action': action, 'operator': 'local-web',
                'request_id': body['request_id'], 'question_id': body.get('question_id'),
                'approved': body.get('approved')})
    return {'task_id': tid, 'status': store.get(tid)['status']}
