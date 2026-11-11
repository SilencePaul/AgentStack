"""Local operator workbench: same-origin commands and redacted execution history."""
import hashlib
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
from urllib.parse import parse_qs, urlsplit

from .diagnostic_reports import redact
from .monitor import snapshot
from .store import Store
from .workbench_commands import command


def task_view(store, tid):
    task = store.get(tid)
    if not task:
        raise ValueError('Unknown task')
    checkpoint = task.get('continuation') or {}
    project = task['project']
    return {
        'id': tid, 'project_id': task['project_id'], 'goal': redact(task['goal']),
        'goal_digest': hashlib.sha256(task['goal'].encode()).hexdigest(),
        'status': task['status'], 'summary': redact(task.get('summary') or ''),
        'workspace': project['path'], 'branch': project.get('branch'),
        'base_sha': project.get('base_sha'), 'attempt': task['attempt'],
        'source': task.get('reply_to'), 'cancel_requested': bool(store.meta('cancel:' + tid)),
        'question': redact(checkpoint.get('question', '')),
        'question_id': checkpoint.get('question_id'), 'input_kind': checkpoint.get('input_kind', 'answer'),
    }


def events(store, tid, after=0):
    if not store.get(tid):
        raise ValueError('Unknown task')
    rows = store.db.execute(
        "SELECT id,kind,detail,created_at FROM events WHERE task_id=? AND id>? "
        "AND kind NOT IN ('output','progress') ORDER BY id LIMIT 150", (tid, after)).fetchall()
    result = []
    for row in rows:
        detail = json.loads(row['detail'])
        if row['kind'] == 'public_output':
            # Only generated public observations are persisted under this kind.
            value = detail.get('detail', detail)
            text = value.get('text', '')
        elif row['kind'] in ('role_started', 'role_finished', 'started', 'tool_activity',
                             'waiting_input', 'answer_recorded', 'operator_command',
                             'approval_requested', 'workspace_ready', 'workspace_preparing'):
            text = json.dumps(detail, ensure_ascii=False)
        elif row['kind'] in ('failed', 'interrupted', 'cancelled', 'succeeded', 'reported_unverified', 'timed_out'):
            text = str(detail)
        else:
            text = row['kind']
        result.append({'id': row['id'], 'kind': row['kind'], 'time': row['created_at'],
                       'role': detail.get('role') if isinstance(detail, dict) else None,
                       'text': redact(text)[:8000]})
    return {'events': result, 'cursor': rows[-1]['id'] if rows else after}


def make_server(path, port=8787):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def local(self):
            host = self.headers.get('Host')
            allowed = ('127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port))
            origin = self.headers.get('Origin')
            return host in allowed and (origin is None or origin == 'http://' + host)

        def respond(self, status, data, html=False):
            body = data if html else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.local():
                self.respond(403, {'error': 'Local origin required'})
                return
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            if url.path == '/':
                self.respond(200, Path(__file__).with_name('workbench.html').read_bytes(), html=True)
                return
            if url.path == '/api/session':
                self.respond(200, {'token': token, 'operator': 'Windows 本机操作员'})
                return
            # Private task data requires the token even for reads.
            if not hmac.compare_digest(self.headers.get('X-Workbench-Token', ''), token):
                self.respond(403, {'error': 'Reload workbench to authenticate'})
                return
            store = Store(path)
            try:
                if url.path == '/api/status':
                    data = snapshot(path)
                    projects = store.projects()
                    data['projects'] = [{'id': p['id'], 'path': p['path'],
                        'workspace_mode': p.get('workspace_mode', 'shared'), 'concurrency': p.get('concurrency', 1)}
                        for p in projects]
                    data['all_tasks'] = [{'id': t['id'], 'project_id': t['project_id'], 'status': t['status'],
                        'goal': redact(t['goal'])[:160], 'created_at': t['created_at'],
                        'cancel_requested': bool(store.meta('cancel:' + t['id']))} for t in store.tasks()[-300:][::-1]]
                else:
                    tid = query.get('task', [''])[0]
                    if not re.fullmatch('[a-f0-9]{32}', tid):
                        raise ValueError('Invalid task')
                    if url.path == '/api/task':
                        data = task_view(store, tid)
                    elif url.path == '/api/events':
                        after = int(query.get('after', ['0'])[0])
                        if after < 0:
                            raise ValueError('Invalid event cursor')
                        data = events(store, tid, after)
                    elif url.path == '/api/diff':
                        from .workspaces import diff
                        data = {'diff': redact(diff(store.get(tid)['project']))}
                    else:
                        self.respond(404, {'error': 'Unknown endpoint'})
                        return
                self.respond(200, data)
            except (ValueError, TypeError) as exc:
                self.respond(400, {'error': redact(str(exc))})
            except Exception:
                self.respond(500, {'error': '读取失败，请重试并检查本机运行记录。'})
            finally:
                store.close()

        def do_POST(self):
            if (not self.local() or self.headers.get('Origin') is None
                    or not hmac.compare_digest(self.headers.get('X-Workbench-Token', ''), token)):
                self.respond(403, {'error': 'Authenticated same-origin request required'})
                return
            if self.path != '/api/command':
                self.respond(404, {'error': 'Unknown endpoint'})
                return
            store = None
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 110000 or self.headers.get_content_type() != 'application/json':
                    raise ValueError('Invalid command body')
                body = self.rfile.read(size)
                if len(body) != size:
                    raise ValueError('Incomplete command')
                store = Store(path)
                self.respond(200, command(store, json.loads(body)))
            except (ValueError, TypeError) as exc:
                self.respond(409, {'error': redact(str(exc))})
            except Exception:
                self.respond(500, {'error': '操作未确认，请刷新核对状态。'})
            finally:
                if store:
                    store.close()

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)
