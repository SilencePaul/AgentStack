import json
from pathlib import Path
import tempfile
import unittest
from router.store import Store
from router.diagnostics import DiagnosticRouter


class DiagnosticReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'router.sqlite3')
        self.store.add_project('p', str(self.root))
        self.route = {'channel_id': 'c', 'source_user_id': 'human'}
        self.task = self.store.submit('p', 'PRIVATE GOAL', reply_to=self.route)
        self.id = self.task['id']
        self.diag = DiagnosticRouter(self.root / 'router.sqlite3')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def query(self, page=1, route=None):
        return self.diag.handle(f'/diag report {self.id} {page}', reply_to=route or self.route)

    def test_stopped_router_report_is_read_only_and_redacted(self):
        self.store.set_meta('service_heartbeat', 'stopped')
        self.store.db.execute('UPDATE tasks SET status=?,summary=? WHERE id=?',
                              ('failed', 'missing ROUTER_LEAD: PLAN token=secret-value Authorization: Bearer abcdef', self.id))
        before = self.store.get(self.id)
        answer = self.query()
        self.assertIn('ROUTER_LEAD: PLAN', answer)
        self.assertNotIn('secret-value', answer)
        self.assertNotIn('abcdef', answer)
        self.assertNotIn('PRIVATE GOAL', answer)
        self.assertEqual(before, self.store.get(self.id))

    def test_legacy_logs_only_extract_errors_and_stderr(self):
        run = self.root / 'runs' / self.id / 'attempt-1' / '2-developer-round-0'
        run.mkdir(parents=True)
        events = [
            {'message': {'content': [{'type': 'thinking', 'thinking': 'PRIVATE THINKING'}, {'type': 'tool_use', 'input': {'command': 'PRIVATE COMMAND'}}]}},
            {'message': {'content': [{'type': 'tool_result', 'is_error': True, 'content': 'No such file: report.json password=hidden'}]}},
        ]
        (run / 'stdout.log').write_text('\n'.join(map(json.dumps, events)))
        (run / 'stderr.log').write_text('FileNotFoundError: missing executable')
        answer = self.query()
        self.assertIn('No such file', answer)
        self.assertIn('FileNotFoundError', answer)
        for value in ('hidden', 'PRIVATE THINKING', 'PRIVATE COMMAND'):
            self.assertNotIn(value, answer)

    def test_cross_channel_and_other_user_are_rejected(self):
        for route in ({'channel_id': 'other', 'source_user_id': 'human'}, {'channel_id': 'c', 'source_user_id': 'other'}):
            self.assertIn('原发信人', self.query(route=route))

    def test_symlink_cannot_read_outside_task(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'stderr.log').write_text('OUTSIDE SECRET')
        task_dir = self.root / 'runs' / self.id
        task_dir.mkdir(parents=True)
        (task_dir / 'attempt-1').symlink_to(outside, target_is_directory=True)
        self.assertNotIn('OUTSIDE SECRET', self.query())

    def test_bounded_pagination_and_invalid_page(self):
        self.store.db.execute('UPDATE tasks SET summary=? WHERE id=?', ('x' * 10000 + 'LAST', self.id))
        first = self.query()
        self.assertLess(len(first), 4000)
        self.assertIn('/diag report', first)
        self.assertIn('LAST', self.query(4))
        self.assertIn('页码超出', self.query(99))

    def test_missing_logs_and_corrupt_json_still_return_summary(self):
        self.store.db.execute('UPDATE tasks SET summary=? WHERE id=?', ('known failure', self.id))
        run = self.root / 'runs' / self.id
        run.mkdir(parents=True)
        (run / 'stdout.log').write_text('{incomplete')
        self.assertIn('known failure', self.query())

    def test_credentials_are_redacted_before_pagination(self):
        from router.diagnostic_reports import redact
        source = 'password="secret with spaces"\n{"api_key": "secret123"}\nhttps://user:pass@host/path?token=xyz\nfh_u_abcdef\n-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----'
        answer = redact(source)
        for value in ('secret with spaces', 'secret123', 'user:pass', 'xyz', 'fh_u_abcdef', '\nabc\n'):
            self.assertNotIn(value, answer)

    def test_report_command_routes_only_to_diagnostics_and_deduplicates(self):
        from unittest.mock import Mock
        from router.mm import Poller
        from router.diagnostics import is_diagnostic_query
        records = Store(self.root / 'diagnostic.sqlite3')
        self.store.db.execute('UPDATE tasks SET summary=? WHERE id=?', ('permission denied', self.id))
        client = Mock()
        posts = [dict(id='allowed', create_at=12, user_id='human', channel_id='c', root_id='root', message='/diag report '+self.id),
                 dict(id='denied', create_at=11, user_id='stranger', channel_id='c', message='/diag report '+self.id),
                 dict(id='old', create_at=9, user_id='human', channel_id='c', message='/diag')]
        client.get.return_value = {'order':[p['id'] for p in posts], 'posts':{p['id']:p for p in posts}}
        records.set_meta('mm.cursor:c', {'time':10,'ids':[]})
        poller = Poller(client, records, self.diag, ['c'], ['human'], 'bot', accept_query=is_diagnostic_query)
        poller.poll()
        poller.poll()
        notices = records.outbox_pending()
        self.assertEqual(len(notices), 1)
        self.assertIn('permission denied', notices[0]['text'])
        records.close()
        worker = Mock()
        self.store.set_meta('mm.cursor:c', {'time':10,'ids':[]})
        Poller(client, self.store, worker, ['c'], ['human'], 'bot').poll()
        worker.handle.assert_not_called()

    def test_old_task_without_source_is_local_only(self):
        self.store.db.execute('UPDATE tasks SET reply_to=NULL WHERE id=?', (self.id,))
        self.assertIn('历史任务', self.query())
        self.assertIn('独立诊断报告', self.diag.handle('/diag report '+self.id))
