"""Durable queue. Every mutation is serialized in a SQLite write transaction."""
import json
import re
import math
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path, common_rules=''):
        self.common_rules = common_rules
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), timeout=30, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, config TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
          goal TEXT NOT NULL, status TEXT NOT NULL, source_key TEXT UNIQUE, reply_to TEXT,
          project TEXT NOT NULL, rules_snapshot TEXT NOT NULL, summary TEXT, evidence TEXT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT,
          detail TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY, task_id TEXT, reply_to TEXT NOT NULL,
          text TEXT NOT NULL, dedup_key TEXT UNIQUE, sent INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);
        ''')
        with self.transaction():
            columns = {r[1] for r in self.db.execute('PRAGMA table_info(tasks)')}
            if 'continuation' not in columns:
                self.db.execute('ALTER TABLE tasks ADD COLUMN continuation TEXT')
            if 'attempt' not in columns:
                self.db.execute('ALTER TABLE tasks ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0')
            if 'original_user_goal' not in columns:
                self.db.execute('ALTER TABLE tasks ADD COLUMN original_user_goal TEXT')
            has_planning = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='planning'").fetchone()
            if has_planning:
                self.db.execute("UPDATE tasks SET original_user_goal=COALESCE((SELECT goal FROM planning WHERE planning.id=substr(tasks.source_key,6,32)),goal) WHERE original_user_goal IS NULL")
            else:
                self.db.execute('UPDATE tasks SET original_user_goal=goal WHERE original_user_goal IS NULL')

    @contextmanager
    def transaction(self):
        # RLock is held throughout, so another thread cannot share this transaction.
        # Savepoints let public mutation methods compose into one atomic command.
        with self.lock:
            nested = self.db.in_transaction
            savepoint = 'nested_' + uuid.uuid4().hex
            self.db.execute('SAVEPOINT ' + savepoint if nested else 'BEGIN IMMEDIATE')
            try:
                yield
                self.db.execute('RELEASE SAVEPOINT ' + savepoint if nested else 'COMMIT')
            except BaseException:
                if nested:
                    self.db.execute('ROLLBACK TO SAVEPOINT ' + savepoint)
                    self.db.execute('RELEASE SAVEPOINT ' + savepoint)
                else:
                    self.db.execute('ROLLBACK')
                raise

    def close(self):
        with self.lock:
            self.db.close()

    def add_project(self, project_id, path, rules='', context='', model='Qwen3.8-Flash', timeout=1800, idle_timeout=300):
        path = Path(path).resolve(strict=True)
        if not path.is_dir():
            raise ValueError('Project path must be a directory')
        if not isinstance(project_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', project_id):
            raise ValueError('Project id must be a safe slug of 1-64 characters')
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (timeout, idle_timeout)):
            raise ValueError('Project id and positive timeouts required')
        config = dict(id=project_id, path=str(path), rules=rules, context=context,
                      model=model, timeout=timeout, idle_timeout=idle_timeout)
        with self.transaction():
            for p in self.projects():
                other = Path(p['path'])
                if path == other or path in other.parents or other in path.parents:
                    raise ValueError('Project directories must be distinct and non-overlapping')
            self.db.execute('INSERT INTO projects VALUES (?,?)', (project_id,json.dumps(config)))
        return config

    def update_project(self, project_id, rules=None, context=None, model=None, timeout=None, idle_timeout=None, agent_profiles=None, workspace_mode=None, concurrency=None):
        changes = dict(workspace_mode=workspace_mode, concurrency=concurrency, rules=rules, context=context, model=model, timeout=timeout, idle_timeout=idle_timeout, agent_profiles=agent_profiles)
        if workspace_mode is not None and workspace_mode not in ('shared', 'git', 'copy'):
            raise ValueError('Unknown workspace mode')
        if concurrency is not None and (type(concurrency) is not int or not 1 <= concurrency <= 8):
            raise ValueError('Team concurrency must be 1-8')
        for key in ('timeout', 'idle_timeout'):
            value = changes[key]
            if value is not None and not (isinstance(value, (int, float)) and math.isfinite(value) and value > 0):
                raise ValueError('Timeouts must be finite positive numbers')
        with self.transaction():
            row = self.db.execute('SELECT config FROM projects WHERE id=?', (project_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown project: ' + project_id)
            config = json.loads(row[0])
            config.update({k:v for k,v in changes.items() if v is not None})
            if config.get('workspace_mode', 'shared') == 'shared' and config.get('concurrency', 1) > 1:
                raise ValueError('Parallel tasks require isolated workspaces')
            self.db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(config), project_id))
            return config

    def projects(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute('SELECT config FROM projects ORDER BY id')]

    @staticmethod
    def decode(row):
        if row is None:
            return None
        result = dict(row)
        for key in ('project','reply_to','evidence','continuation'):
            if key in result and result[key] is not None:
                result[key] = json.loads(result[key])
        return result

    def submit(self, project_id, goal, source_key=None, reply_to=None, original_user_goal=None):
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 100000:
            raise ValueError('Goal must contain 1-100000 characters and not be blank')
        original_user_goal = goal if original_user_goal is None else original_user_goal
        if not isinstance(original_user_goal, str) or not original_user_goal.strip() or len(original_user_goal) > 100000:
            raise ValueError('Original user goal must contain 1-100000 nonblank characters')
        with self.transaction():
            if source_key is not None:
                existing = self.db.execute('SELECT * FROM tasks WHERE source_key=?',(source_key,)).fetchone()
                if existing:
                    return self.decode(existing)
            row = self.db.execute('SELECT config FROM projects WHERE id=?',(project_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown project: '+project_id)
            project = json.loads(row[0])
            snapshot = '\n\n'.join(x for x in (self.common_rules,project['rules']) if x)
            task_id = uuid.uuid4().hex
            self.db.execute('INSERT INTO tasks(id,project_id,goal,status,source_key,reply_to,project,rules_snapshot,original_user_goal) VALUES (?,?,?,\'queued\',?,?,?,?,?)',
                            (task_id,project_id,goal,source_key,json.dumps(reply_to) if reply_to is not None else None,json.dumps(project),snapshot,original_user_goal))
            return self.get(task_id)

    def get(self, task_id):
        with self.lock:
            return self.decode(self.db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone())

    def tasks(self):
        with self.lock:
            return [self.decode(r) for r in self.db.execute('SELECT * FROM tasks ORDER BY rowid')]

    def claim(self, limit=2):
        with self.transaction():
            if self.meta('paused','0') == '1' or self.meta('shutdown','0') == '1':
                return None
            if self.db.execute("SELECT COUNT(*) FROM tasks WHERE status='running'").fetchone()[0] >= limit:
                return None
            rows = self.db.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY CASE WHEN continuation IS NULL THEN 1 ELSE 0 END, rowid").fetchall()
            row = next((r for r in rows if self.can_claim(self.decode(r))), None)
            if row is None:
                return None
            self.db.execute("UPDATE tasks SET status='running',attempt=attempt+1 WHERE id=?",(row['id'],))
            return self.get(row['id'])

    def can_claim(self, task):
        if self.meta('project_paused:' + task['project_id'], False):
            return False
        others = [self.decode(r) for r in self.db.execute(
            "SELECT * FROM tasks WHERE project_id=? AND id!=? AND status IN ('running','waiting_input')",
            (task['project_id'], task['id']))]
        isolated = task['project'].get('workspace_mode', 'shared') != 'shared'
        if not isolated or any(t['project'].get('workspace_mode', 'shared') == 'shared' for t in others):
            return not others
        return sum(t['status'] == 'running' for t in others) < task['project'].get('concurrency', 1)

    def save_workspace(self, task_id, project):
        with self.transaction():
            task = self.get(task_id)
            if task['status'] != 'running':
                raise ValueError('Task no longer running')
            self.db.execute('UPDATE tasks SET project=? WHERE id=?', (json.dumps(project), task_id))
            self.event(task_id, 'workspace_ready', {'path': project['path'], 'branch': project.get('branch')})
            return self.get(task_id)

    def question_route(self, task):
        if task['reply_to'] is not None:
            return task['reply_to']
        parent = re.fullmatch(r'plan:([0-9a-f]{32}):[0-9]+', task.get('source_key') or '')
        if parent:
            with self.lock:
                row = self.db.execute('SELECT reply_to FROM planning WHERE id=?', (parent[1],)).fetchone()
            if row and row['reply_to']:
                return json.loads(row['reply_to'])
        return None

    def wait_input(self, task_id, checkpoint, evidence):
        with self.transaction():
            task=self.get(task_id)
            if not task or task['status']!='running':raise ValueError('Task is not running')
            self.db.execute("UPDATE tasks SET status='waiting_input',continuation=?,evidence=?,summary=? WHERE id=?",
                (json.dumps(checkpoint),json.dumps(evidence),checkpoint['question'],task_id))
            self.event(task_id,'waiting_input',{'question_id':checkpoint['question_id'],'phase_index':checkpoint['index']})
            route = self.question_route(task)
            if route and not route.get('final_only'):
                self._notice(route,'任务 '+task_id+' 等待回答：'+checkpoint['question']+'\n在本线程回复：/router answer '+task_id+' '+checkpoint['question_id']+' 你的答案',
                    'question:'+task_id+':'+checkpoint['question_id'],task_id)

    def answer(self, task_id, question_id, answer, reply_to=None):
        if not answer.strip() or len(answer)>4000:raise ValueError('Answer must contain 1-4000 characters')
        with self.transaction():
            task=self.get(task_id)
            if not task or task['status']!='waiting_input':raise ValueError('Task is not waiting for an answer')
            route=self.question_route(task)
            if route is None:
                authorized=reply_to is None
            else:
                authorized=reply_to is not None and all(route.get(k)==reply_to.get(k) for k in ('channel_id','root_id','source_user_id'))
            if not authorized:raise ValueError('Answer must come from the original task author and thread')
            checkpoint=task['continuation']
            if checkpoint['question_id']!=question_id:raise ValueError('Question is stale or does not belong to this task')
            checkpoint['answers'].append({'question_id':question_id,'question':checkpoint['question'],'answer':answer})
            self.db.execute("UPDATE tasks SET status='queued',continuation=? WHERE id=?",(json.dumps(checkpoint),task_id))
            self.event(task_id,'answer_recorded',{'question_id':question_id})
            return self.get(task_id)

    def _finish(self, task_id, status, summary, evidence):
        task = self.get(task_id)
        if task is None:
            raise ValueError('Unknown task')
        if task['status'] not in ('running','queued','waiting_input','awaiting_approval'):
            return
        self.db.execute('UPDATE tasks SET status=?,summary=?,evidence=? WHERE id=?',
                        (status,summary,json.dumps(evidence),task_id))
        self.db.execute('INSERT INTO events(task_id,kind,detail) VALUES (?,?,?)',(task_id,status,json.dumps(summary)))
        if status in ('failed', 'failure', 'timed_out', 'timeout', 'interrupted') and task['project'].get('workspace_mode', 'shared') == 'shared':
            self.db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                            ('project_paused:' + task['project_id'], 'true'))
        if task['reply_to'] is not None:
            text = f"项目 {task['project_id']}\n任务 {task_id}\n状态 {status}\n通过 /diag report {task_id} 查询脱敏报告和工具报错。"
            if task['reply_to'].get('final_only'):
                from .diagnostic_reports import redact
                iid = task['reply_to'].get('mr_iid')
                subject = f'MR !{iid}' if iid else task['project_id']
                text = f"{subject} · {status}\n{redact(str(summary or '无报告'))[:1800]}"
            self._notice(task['reply_to'], text, 'task:'+task_id, task_id)

    def finish(self, task_id, status, summary, evidence=None):
        if status not in ('succeeded','reported_unverified','failed','timed_out','interrupted','cancelled'):
            raise ValueError('Finish requires terminal status')
        with self.transaction():
            self._finish(task_id,status,summary,evidence)

    def recover(self):
        with self.transaction():
            ids=[r[0] for r in self.db.execute("SELECT id FROM tasks WHERE status='running'")]
            for task_id in ids:
                self._finish(task_id,'interrupted','Router restarted during execution; review external effects before resubmitting.',None)
            return len(ids)

    def event(self, task_id, kind, detail):
        with self.transaction():
            self.db.execute('INSERT INTO events(task_id,kind,detail) VALUES (?,?,?)',(task_id,kind,json.dumps(detail)))

    def _notice(self, reply_to, text, key, task_id=None):
        self.db.execute('INSERT OR IGNORE INTO outbox(task_id,reply_to,text,dedup_key) VALUES (?,?,?,?)',
                        (task_id,json.dumps(reply_to),text,key))

    def enqueue_notice(self, reply_to, text, key):
        with self.transaction():
            self._notice(reply_to,text,key)

    def outbox_pending(self):
        with self.lock:
            return [self.decode(r) for r in self.db.execute('SELECT * FROM outbox WHERE sent=0 ORDER BY id')]

    def outbox_sent(self, id):
        with self.transaction():
            self.db.execute('UPDATE outbox SET sent=1 WHERE id=?',(id,))

    def meta(self, key, default=None):
        with self.lock:
            row=self.db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.transaction():
            self.db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(value)))
