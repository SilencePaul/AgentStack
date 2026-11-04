"""Explicitly marked Multica issues, durable claim intents and verified writeback.

Single router owns the configured workspace; this is not a distributed lease.
All remote operations are delegated to the extended ai_crew.py CLI.
"""
import hashlib
import re
from urllib.parse import quote
from .mm import TransportRejected


def terminal_report(task):
    """Public notifications contain deterministic supervisor fields only."""
    descriptions = {
        'succeeded': 'Configured independent acceptance checks passed.',
        'failed': 'NEEDS ATTENTION: execution or acceptance checks failed.',
        'timed_out': 'NEEDS ATTENTION: execution exceeded its deadline.',
        'interrupted': 'NEEDS ATTENTION: execution was interrupted.',
        'reported_unverified': 'NEEDS ATTENTION: completion was reported but independent acceptance was not established.',
    }
    status = task['status'] if task['status'] in descriptions else 'unknown'
    fields = {'version': 2, 'task_id': task['id'], 'project_id': task['project_id'],
              'status': status, 'description': descriptions.get(status, 'NEEDS ATTENTION: inspect local task record.'),
              'evidence_reference': 'Local task record ' + task['id']}
    text = ('Router task: ' + fields['task_id'] + '\nProject: ' + fields['project_id'] +
            '\nStatus: ' + status + '\n' + fields['description'] +
            '\nFull reports and evidence: ' + fields['evidence_reference'])
    if status != 'succeeded':
        text += '\nIssue remains open; no automatic rerun.'
    return fields, text


class MulticaClient:
    def __init__(self, crew):
        self.crew = crew
        self.identity = None

    def get(self, path):
        return self.crew.call('mc-get', path, '--scrub')['data']

    def issue(self, workspace, issue_id):
        return self.get('/issues/' + quote(issue_id, safe='') + '?workspace_slug=' + quote(workspace, safe=''))

    def issues(self, workspace):
        result, seen = [], set()
        for offset in range(0, 100000, 100):
            data = self.get('/issues?workspace_slug=' + quote(workspace, safe='') + '&limit=100&offset=' + str(offset))
            if not isinstance(data, dict) or not isinstance(data.get('issues'), list) or not isinstance(data.get('total'), int):
                raise RuntimeError('Incomplete Multica issue listing')
            batch = data['issues']
            if any(i['id'] in seen for i in batch):
                raise RuntimeError('Repeated Multica issue page')
            seen.update(i['id'] for i in batch)
            result.extend(batch)
            if len(result) >= data['total']:
                return result
            if not batch:
                raise RuntimeError('Truncated Multica issue listing')
        raise RuntimeError('Multica issue listing exceeds safety limit')

    def status(self, workspace, issue_id, value):
        current = self.issue(workspace, issue_id)
        if current.get('status') != value:
            self.crew.call('issue-status', issue_id, value, '--workspace', workspace)
        if self.issue(workspace, issue_id).get('status') != value:
            raise RuntimeError('Multica status update not verified')

    def comment(self, workspace, issue_id, text, key):
        marker = '[router-delivery:' + key + ']'
        if not self.identity:
            self.identity = self.get('/me')['id']
        def find():
            data = self.get('/issues/' + quote(issue_id, safe='') + '/comments?workspace_slug=' + quote(workspace, safe=''))
            # Unbounded default API returns a list. Refuse newer capped envelopes.
            if not isinstance(data, list):
                raise RuntimeError('Cannot verify complete comment history')
            return any(c.get('author_id') == self.identity and c.get('content', '').endswith(marker) for c in data)
        if find():
            return
        self.crew.call('issue-comment', issue_id, text.replace('@', '＠') + '\n\n' + marker, '--workspace', workspace)
        if not find():
            raise RuntimeError('Multica comment delivery not verified')


class MulticaPoller:
    def __init__(self, client, store, workspace, project_ids, report_route=None):
        self.client, self.store, self.workspace = client, store, workspace
        self.project_ids = set(project_ids)
        self.report_route = report_route
        self.index_key = 'mc.intents:' + workspace
        self.workspace_id = None

    def _scope(self):
        if self.workspace_id is None:
            workspaces = self.client.get('/workspaces')
            matches = [w for w in workspaces if w.get('slug') == self.workspace]
            if len(matches) != 1:
                raise RuntimeError('Configured Multica workspace not uniquely accessible')
            self.workspace_id = matches[0]['id']

    @staticmethod
    def _fingerprint(issue):
        return hashlib.sha256((issue.get('title', '') + '\n' + (issue.get('description') or '')).encode('utf-8')).hexdigest()

    def _record_rejection(self, stage, reference):
        key = 'mc-' + stage + '-rejected:' + reference
        reason = 'HTTP 403 WAF rejection; explicit content review is required before retry.'
        rejections = self.store.meta('mc.rejections', {})
        rejections[key] = {'workspace': self.workspace, 'stage': stage,
                           'reference': reference, 'reason': reason}
        self.store.set_meta('mc.rejections', rejections)
        self.store.set_meta('mc.rejection_count', len(rejections))
        if self.report_route:
            self.store.enqueue_notice(self.report_route,
                'ACTION REQUIRED: Multica ' + stage + ' was rejected.\nReference: ' + reference +
                '\nAutomatic retries are paused. Review the local router record before explicitly retrying.', key)

    def poll(self):
        self._scope()
        issues = self.client.issues(self.workspace)
        for issue in issues:
            if issue.get('workspace_id') != self.workspace_id:
                raise RuntimeError('Issue escaped configured workspace')
            markers = re.findall(r'(?m)^\[router-project:([A-Za-z0-9_-]+)\]\s*$', issue.get('description') or '')
            if issue.get('status') != 'todo' or len(markers) != 1 or markers[0] not in self.project_ids:
                continue
            iid = issue['id']
            with self.store.transaction():
                intents = self.store.meta(self.index_key, {})
                if iid not in intents:
                    intents[iid] = {'project': markers[0], 'fingerprint': self._fingerprint(issue),
                                    'goal': issue.get('title', '') + '\n\n' + (issue.get('description') or ''), 'task_id': None}
                    self.store.set_meta(self.index_key, intents)
        failures = []
        for iid, intent in self.store.meta(self.index_key, {}).items():
            if intent['task_id'] or intent.get('rejected'):
                continue
            try:
                registered = {project['id'] for project in self.store.projects()}
                if intent['project'] not in self.project_ids or intent['project'] not in registered:
                    raise RuntimeError('Pending issue project is no longer authorized or registered')
                current = self.client.issue(self.workspace, iid)
                if current.get('workspace_id') != self.workspace_id or self._fingerprint(current) != intent['fingerprint']:
                    raise RuntimeError('Issue changed since claim intent; requires manual review')
                if current.get('status') not in ('todo', 'in_progress'):
                    raise RuntimeError('Issue is no longer available for router execution')
                self.client.comment(self.workspace, iid,
                    'Local Qoder router accepted this explicitly marked issue. Execution will use project ' + intent['project'] + '.', 'mc-claim:' + iid)
                self.client.status(self.workspace, iid, 'in_progress')
                with self.store.transaction():
                    task = self.store.submit(intent['project'], intent['goal'], source_key='mc:' + iid, reply_to=None)
                    intents = self.store.meta(self.index_key, {})
                    intents[iid]['task_id'] = task['id']
                    self.store.set_meta(self.index_key, intents)
            except Exception as exc:
                if isinstance(exc, TransportRejected):
                    with self.store.transaction():
                        intents = self.store.meta(self.index_key, {})
                        intents[iid]['rejected'] = True
                        intents[iid]['rejection_reason'] = 'HTTP 403 WAF; explicit content review required'
                        self.store.set_meta(self.index_key, intents)
                        self._record_rejection('claim', iid)
                failures.append(exc)
        if failures:
            raise RuntimeError('One or more Multica claims need retry or attention') from failures[0]

    def flush_writebacks(self):
        self._scope()
        errors = []
        for iid, intent in self.store.meta(self.index_key, {}).items():
            if not intent['task_id']:
                continue
            task = self.store.get(intent['task_id'])
            if task is None or task['status'] in ('queued', 'running', 'waiting_input'):
                continue
            fields, summary = terminal_report(task)
            self.store.set_meta('mc.report:' + task['id'], fields)
            if self.report_route:
                self.store.enqueue_notice(self.report_route, summary, 'mc-notice:' + task['id'])
            key = 'mc.writeback:' + task['id']
            if self.store.meta(key) in ('sent', 'rejected'):
                continue
            try:
                current = self.client.issue(self.workspace, iid)
                if current.get('workspace_id') != self.workspace_id:
                    raise RuntimeError('Writeback workspace mismatch')
                if self._fingerprint(current) != intent['fingerprint']:
                    raise RuntimeError('Issue changed during execution; refusing stale completion')
                own_close_retry = self.store.meta(key) == 'closing' and task['status'] == 'succeeded'
                if current.get('status') != 'in_progress' and not (current.get('status') == 'done' and own_close_retry):
                    raise RuntimeError('Issue status changed outside router; refusing writeback')
                if not own_close_retry:
                    self.store.set_meta(key, 'pending')
                self.client.comment(self.workspace, iid, summary, 'mc-result:' + task['id'])
                if task['status'] == 'succeeded':
                    self.store.set_meta(key, 'closing')
                    self.client.status(self.workspace, iid, 'done')
                self.store.set_meta(key, 'sent')
            except Exception as exc:
                if isinstance(exc, TransportRejected):
                    with self.store.transaction():
                        self.store.set_meta(key, 'rejected')
                        self._record_rejection('writeback', task['id'])
                errors.append(exc)
        if errors:
            raise RuntimeError('Multica writeback remains pending') from errors[0]
