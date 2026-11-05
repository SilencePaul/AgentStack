import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from router.store import Store
from router.operations import OperationsRouter, Controller, MaintenanceStore, validate_runtimes


class OperationsTests(unittest.TestCase):
    def test_runtime_overlap_is_rejected_before_opening_databases(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for other in (root,root/'child'):
                with self.assertRaises(ValueError):validate_runtimes(root,other)
            self.assertEqual(list(root.iterdir()),[])
            validate_runtimes(root/'business',root/'operations')
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'ops.sqlite3')
        self.store.add_project('agentstack-maintenance', self.root)
        self.business = Store(self.root / 'router.sqlite3')
        self.business.add_project('business', self.root)
        self.controller = Controller(self.store, self.root, self.root / 'config.json', self.root)
        self.router = OperationsRouter(self.store, self.controller, self.root / 'router.sqlite3')
        self.route = {'channel_id':'c','root_id':'r','source_user_id':'u'}

    def tearDown(self):
        self.business.close()
        self.store.close()
        self.tmp.cleanup()

    def test_repair_is_separate_task_and_deduplicated(self):
        for _ in range(2):
            answer = self.router.handle('/ops repair Fix status formatting', 'post', self.route)
        self.assertEqual(len(self.store.tasks()), 1)
        self.assertEqual(self.business.tasks(), [])
        self.assertIn('agentstack-maintenance', answer)

    def test_repeated_control_message_creates_only_one_action(self):
        for _ in range(2):
            self.router.handle('/ops restart', 'post', self.route)
        self.assertEqual(len(self.controller.actions()), 1)

    def test_control_result_has_transport_safe_delivery_key(self):
        import re
        self.controller.finish('ops.action:abc',{'route':self.route},'done')
        self.assertRegex(self.store.outbox_pending()[-1]['dedup_key'],r'^[A-Za-z0-9:_-]+$')

    def test_unowned_process_cannot_be_restarted(self):
        with self.assertRaisesRegex(ValueError, '托管'):
            self.controller.begin('restart')

    def test_running_business_task_blocks_restart(self):
        self.business.submit('business','goal')
        self.business.claim(1)
        self.controller.child = Mock()
        self.controller.child.poll.return_value = None
        with self.assertRaisesRegex(ValueError, '运行'):
            self.controller.begin('restart')
        self.assertNotEqual(self.business.meta('shutdown'), '1')

    def test_shutdown_or_pause_prevents_claim_after_scheduler_precheck(self):
        self.business.submit('business','goal')
        for key in ('paused','shutdown'):
            self.business.set_meta(key,'1')
            self.assertIsNone(self.business.claim(1))
            self.business.set_meta(key,'0')
        self.assertIsNotNone(self.business.claim(1))

    def test_report_stays_bound_to_requester_and_channel(self):
        task = self.store.submit('agentstack-maintenance','secret',reply_to=self.route)
        text = self.router.handle('/ops report '+task['id'],reply_to={**self.route,'source_user_id':'other'})
        self.assertIn('原发信人',text)

    def test_diagnostics_work_without_business_process(self):
        answer = self.router.handle('/diag status',reply_to=self.route)
        self.assertIn('独立诊断',answer)
        self.assertIn('心跳',answer)

    def test_maintenance_notices_use_operations_commands(self):
        store = MaintenanceStore(self.root/'notices.sqlite3')
        try:
            store.add_project('agentstack-maintenance',self.root)
            task=store.submit('agentstack-maintenance','goal',reply_to=self.route)
            store.claim(1)
            store.wait_input(task['id'],{'question':'Which?','question_id':'q','index':0},None)
            self.assertIn('/ops answer',store.outbox_pending()[-1]['text'])
            store.db.execute("UPDATE tasks SET status='running' WHERE id=?",(task['id'],))
            store.finish(task['id'],'failed','reason')
            self.assertIn('/ops report',store.outbox_pending()[-1]['text'])
        finally:
            store.close()
