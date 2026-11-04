import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from router.store import Store

class StoreTests(unittest.TestCase):
    def test_durable_claims_and_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a,b=root/'a',root/'b'
            a.mkdir(); b.mkdir()
            db=root/'state.db'
            s=Store(db, common_rules='base')
            s.add_project('a',a,rules='A'); s.add_project('b',b,rules='B')
            first=s.submit('a','one',source_key='key',reply_to={'channel':'x'})
            self.assertEqual(first['id'],s.submit('a','duplicate',source_key='key')['id'])
            s.submit('a','two'); s.submit('b','three')
            def claim(_):
                c=Store(db)
                try: return c.claim(2)
                finally: c.close()
            with ThreadPoolExecutor(max_workers=4) as pool:
                claimed=[t for t in pool.map(claim,range(4)) if t]
            self.assertEqual({'a','b'}, {t['project_id'] for t in claimed})
            self.assertIn('base',first['rules_snapshot'])
            s.close(); s=Store(db)
            self.assertEqual(2,s.recover())
            self.assertEqual(0,s.recover())
            self.assertEqual(1,len(s.outbox_pending()))
            self.assertIsNone(s.claim())
            s.set_meta('project_paused:a', False)
            self.assertEqual('two',s.claim()['goal'])
            s.enqueue_notice({'channel':'x'},'ok','notice')
            s.enqueue_notice({'channel':'x'},'ok','notice')
            self.assertEqual(2,len(s.outbox_pending()))
            s.close()

    def test_overlap_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'child').mkdir()
            s=Store(':memory:'); s.add_project('a',root)
            with self.assertRaises(ValueError): s.add_project('b',root/'child')
            s.close()

    def test_updates_and_failure_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); a=root/'a'; b=root/'b'; a.mkdir(); b.mkdir()
            s=Store(':memory:', common_rules='base')
            s.add_project('a',a,rules='old'); s.add_project('b',b)
            old=s.submit('a','old goal')
            s.update_project('a',rules='new',context='context',model='new-model',timeout=25)
            new=s.submit('a','new goal')
            self.assertEqual('old',s.get(old['id'])['project']['rules'])
            self.assertEqual('new-model',new['project']['model'])
            self.assertNotIn('context',new['rules_snapshot'])
            self.assertIn('context',new['project']['context'])
            s.claim(); s.finish(old['id'],'failed','failed')
            s.submit('b','unrelated')
            self.assertEqual('b',s.claim()['project_id'])
            self.assertIsNone(s.claim())
            self.assertTrue(s.meta('project_paused:a'))
            for goal in ('', ' ', 'x'*100001):
                with self.assertRaises(ValueError): s.submit('a',goal)
            with self.assertRaises(ValueError): s.add_project('../bad',root)
            with self.assertRaises(ValueError): s.update_project('a',timeout=-1)
            s.close()

    def test_new_task_context_excludes_history_and_is_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); a=root/'a'; b=root/'b'; a.mkdir(); b.mkdir()
            s=Store(':memory:'); s.add_project('a',a,context='registered'); s.add_project('b',b)
            for i in range(4):
                task=s.submit('a',f'goal {i}'); s.claim()
                s.finish(task['id'],'reported_unverified',f'report {i} ' + 'z'*3000)
            other=s.submit('b','private goal'); s.claim(); s.finish(other['id'],'succeeded','private summary')
            current=s.submit('a','current')
            history=current['project']['context']
            self.assertIn('registered',history)
            self.assertNotIn('report 3',history)
            self.assertNotIn('report 3',current['rules_snapshot'])
            self.assertNotIn('registered',current['rules_snapshot'])
            self.assertNotIn('report 0',history)
            self.assertNotIn('private summary',history)
            self.assertLess(len(history),8000)
            s.claim(); s.finish(current['id'],'succeeded','new report')
            self.assertEqual(history,s.get(current['id'])['project']['context'])
            with self.assertRaises(ValueError): s.finish(current['id'],'arbitrary','bad')
            s.close()

    def test_nested_command_transaction_rollback(self):
        s=Store(':memory:')
        with self.assertRaises(RuntimeError):
            with s.transaction():
                s.set_meta('paused','0')
                s.enqueue_notice({'channel':'x'},'resumed','resume:1')
                s.set_meta('processed:1', True)
                raise RuntimeError('simulated delivery persistence failure')
        self.assertIsNone(s.meta('paused'))
        self.assertIsNone(s.meta('processed:1'))
        self.assertEqual([],s.outbox_pending())
        with s.transaction():
            s.set_meta('paused','0')
            s.enqueue_notice({'channel':'x'},'resumed','resume:1')
            s.set_meta('processed:1', True)
        s.set_meta('paused','1')
        with s.transaction():
            if not s.meta('processed:1'):
                s.set_meta('paused','0')
        self.assertEqual('1',s.meta('paused'))
        s.close()
