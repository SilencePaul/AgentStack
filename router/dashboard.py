"""Read-only, structured view of persisted team and worker observations."""
import json
import time
import re
from collections import deque
from datetime import datetime, timezone

ROLES = ('developer', 'reviewer', 'tester', 'lead-report')

def decode(value, default=None):
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default

ACTIVITY_KINDS = {
    'role_started', 'role_finished', 'role_verdict', 'started', 'tool_activity',
    'role_retry_requested', 'rework_requested', 'waiting_input', 'awaiting_user',
    'leader_decision', 'leader_requested', 'answer_recorded', 'finished', 'failed', 'timed_out', 'interrupted',
}
STATUSES = {'started','returned','error','running','succeeded','reported_unverified',
            'failed','timed_out','interrupted','waiting_input','queued','PASS','BLOCKED','PLAN','REWORK','STOP'}


def label(value):
    return value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',value) else None


def elapsed(timestamp):
    if not isinstance(timestamp,str):
        return None
    try:
        dt = datetime.fromisoformat(timestamp.replace('Z','+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0,int(time.time()-dt.timestamp()))
    except ValueError:
        return None


def task_events(db, task, online):
    activity, journey, roles = deque(maxlen=80), [], {}
    active_role = None
    kinds = sorted(ACTIVITY_KINDS | {'output'})
    query = 'SELECT kind,detail,created_at FROM events WHERE task_id=? AND kind IN (' + ','.join('?' for _ in kinds) + ') ORDER BY id'
    for row in db.execute(query, (task['id'],*kinds)):
        kind, when = row['kind'], row['created_at']
        detail = decode(row['detail'], {})
        if not isinstance(detail,dict):
            continue
        phase = detail.get('role')
        role = 'lead-report' if phase in ('lead-plan','lead-triage') else phase if phase in ROLES else None
        nested = detail.get('detail',{})
        if not isinstance(nested,dict):
            nested = {}
        if kind == 'role_started' and role:
            active_role = role
            old = roles.get(role,{})
            roles[role] = {'visits':old.get('visits',0)+1,'started_at':when,
                           'calls':{},'pid':None,'session_id':None,'tool':None,'tool_state':None,
                           'agent_id':label(detail.get('agent_id')),'profile_version':label(detail.get('profile_version')),'profile_digest':label(detail.get('profile_digest')),'phase':phase,'last_output':None,'finished':False,'blocked':False,'last_event':when}
            journey.append({'role':role,'phase':phase,'round':detail.get('round') if type(detail.get('round')) is int else 0,'time':when})
        if role is None and kind in ('waiting_input','awaiting_user','answer_recorded','finished','failed','timed_out','interrupted'):
            role = active_role
        worker = roles.get(role)
        tool, state = None, None
        if worker:
            worker['last_event'] = when
            if kind == 'started':
                # A fresh process/session is a hard correlation boundary even if
                # the role retry did not emit a separate role_started event.
                worker['calls'] = {}
                worker['tool'] = worker['tool_state'] = worker['last_output'] = None
                worker['pid'] = nested.get('pid') if type(nested.get('pid')) is int else None
                worker['session_id'] = label(nested.get('session_id'))
            elif kind == 'output':
                worker['last_output'] = when
            elif kind == 'role_finished':
                worker['finished'] = True
                worker['blocked'] = detail.get('status') in ('failed','timed_out','interrupted') or detail.get('verdict') == 'BLOCKED'
            elif kind in ('role_retry_requested','rework_requested','waiting_input','awaiting_user','answer_recorded'):
                worker['calls'] = {}
            elif kind == 'tool_activity':
                state = nested.get('state') if nested.get('state') in ('started','returned','error') else None
                tool_id = label(nested.get('tool_id'))
                if state == 'started':
                    tool = label(nested.get('tool'))
                    if tool_id:
                        worker['calls'][tool_id] = tool
                elif state in ('returned','error'):
                    tool = worker['calls'].pop(tool_id, None)
                worker['tool'],worker['tool_state'] = tool,state
        if kind == 'output':
            continue
        if state is None:
            candidate = detail.get('status',detail.get('verdict',detail.get('decision')))
            state = candidate if isinstance(candidate,str) and candidate in STATUSES else {
                'role_started':'started','started':'started','role_retry_requested':'retry',
                'rework_requested':'rework','waiting_input':'awaiting_user','awaiting_user':'awaiting_user',
                'answer_recorded':'answered',
            }.get(kind)
        activity.append({'task_id':task['id'],'role':role,'phase':phase if phase in (*ROLES,'lead-plan','lead-triage') else None,'kind':kind,'time':when,'tool':tool,'state':state})
    result = []
    for role, worker in roles.items():
        state = ('blocked' if worker['blocked'] else 'finished') if worker['finished'] else ('running' if online else 'unknown') if task['status']=='running' else 'inactive'
        if role == active_role:
            if task['status'] in ('failed','timed_out','interrupted'):
                state = 'blocked'
            elif task['status']=='waiting_input':
                state = 'awaiting_user'
            elif task['status']=='queued' and task.get('resume_pending'):
                state = 'resume_pending'
        result.append({'task_id':task['id'],'project_id':task['project_id'],'role':role,'state':state,
                       **{key:worker[key] for key in ('agent_id','profile_version','profile_digest','phase','pid','session_id','visits','tool','tool_state','last_event','started_at','last_output')},
                       'output_age_seconds':elapsed(worker['last_output'])})
    return result, journey, list(activity)


def dashboard(db):
    meta = {r['key']: decode(r['value']) for r in db.execute('SELECT key,value FROM metadata')}
    beat = meta.get('service_heartbeat')
    try:
        age = max(0, int(time.time()-float(beat)))
    except (ValueError, TypeError):
        age = None
    online = age is not None and age < 10
    health = 'online' if online else ('stopped' if beat == 'stopped' else 'stale')
    tasks = [dict(r) for r in db.execute('SELECT id,project_id,status,created_at FROM tasks ORDER BY rowid DESC')]
    issues = []
    for task in tasks:
        row = db.execute('SELECT continuation,summary,project FROM tasks WHERE id=?',(task['id'],)).fetchone()
        checkpoint = decode(row['continuation'],{}) or {}
        task['resume_pending'] = task['status']=='queued' and bool(checkpoint.get('answers'))
        task['queue_blockers'] = []
        if task['status']=='queued':
            if not online: task['queue_blockers'].append('router_offline')
            if meta.get('paused')=='1': task['queue_blockers'].append('scheduler_paused')
            if meta.get('project_paused:'+task['project_id']): task['queue_blockers'].append('project_paused')
            project = decode(row['project'], {})
            peers = [t for t in tasks if t['id'] != task['id'] and t['project_id'] == task['project_id'] and t['status'] in ('running','waiting_input')]
            isolated = project.get('workspace_mode', 'shared') != 'shared'
            shared_peer = any(decode(db.execute('SELECT project FROM tasks WHERE id=?', (t['id'],)).fetchone()[0], {}).get('workspace_mode', 'shared') == 'shared' for t in peers)
            if (not isolated or shared_peer) and peers or isolated and sum(t['status'] == 'running' for t in peers) >= project.get('concurrency', 1):
                task['queue_blockers'].append('project_busy')
            for reason in task['queue_blockers']:
                issues.append({'task_id':task['id'],'project_id':task['project_id'],'category':reason})
        category = None
        if task['status']=='waiting_input': category = 'needs_answer'
        if task['status'] in ('failed','timed_out','interrupted') and not meta.get('incident_resolved:'+task['id']):
            summary = (row['summary'] or '').lower()
            category = {'timed_out':'timeout','interrupted':'interrupted'}.get(task['status'],'execution_failed')
            for needle, kind in [('workspace changed since checkpoint','checkpoint_changed'),('permission denied','permission_denied'),('permission denial','permission_denied'),('receipt','receipt_invalid')]:
                if needle in summary:
                    category = kind
                    break
        if category: issues.append({'task_id':task['id'],'project_id':task['project_id'],'category':category})
        if task['status'] == 'waiting_input':
            row = db.execute('SELECT continuation FROM tasks WHERE id=?',(task['id'],)).fetchone()
            checkpoint = decode(row['continuation'],{}) if row else {}
            task['question'] = checkpoint.get('question','')
            task['question_id'] = checkpoint.get('question_id','')
    workers, activity = [], []
    journeys = {}
    for task in tasks[:100]:
        observed, journey, recent = task_events(db, task, online)
        workers.extend(observed)
        activity.extend(recent)
        journeys[task['id']] = journey
    teams = []
    for row in db.execute('SELECT id FROM projects ORDER BY id'):
        pid = row['id']; own = [t for t in tasks if t['project_id']==pid]
        current = next((t for status in ('running','waiting_input','queued') for t in own if t['status']==status), own[0] if own else None)
        teams.append({'id':pid, 'paused':bool(meta.get('project_paused:'+pid)), 'task':current,
                      'journey':journeys.get(current['id'],[]) if current else [],
                      'roster':[next((w for w in workers if current and w['task_id']==current['id'] and w['role']==role),
                                     {'role':role,'state':'not_started','visits':0}) for role in ROLES],
                      'workers':[w for w in workers if current and w['task_id']==current['id']],
                      'queued':sum(t['status']=='queued' for t in own)})
    attention = [t for t in tasks if t['status']=='waiting_input' or (t['status'] in ('failed','timed_out','interrupted') and not meta.get('incident_resolved:'+t['id']))]
    for task in tasks:
        task['resolved'] = bool(meta.get('incident_resolved:'+task['id']))
    return {'health':{'router':health,'heartbeat_age':age,'mm':meta.get('transport_health','unconfigured'),
                       'multica':meta.get('multica_health','unconfigured')},
            'metrics':{'teams':len(teams),'active_teams':len({t['project_id'] for t in tasks if t['status']=='running'}) if online else None,
                       'running_workers':sum(w['state']=='running' for w in workers) if online else None,
                       'attention':len({i['task_id'] for i in issues}),'completed':sum(t['status']=='succeeded' for t in tasks)},
            'teams':teams,'workers':workers,'tasks':tasks[:100],'activity':activity,'issues':issues}
