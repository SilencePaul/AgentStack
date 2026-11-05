import tempfile
import unittest
from pathlib import Path

from router.mock_commands import MockCommands
from router.mock_gitlab import MockGitLab
from router.store import Store


class MockCommandTests(unittest.TestCase):
    def test_receiver_restart_runs_in_queue_without_replaying_same_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=Store(root/'ops');mock=MockGitLab(root/'mock');calls=[]
            try:
                def restart():calls.append('restart');return {'generation':2}
                commands=MockCommands(store,mock,restart_receiver=restart)
                key=commands.enqueue('restart-receiver','post',{})
                self.assertEqual(calls,[])
                commands.tick();commands.enqueue('restart-receiver','post',{});commands.tick()
                self.assertEqual(calls,['restart'])
                self.assertEqual(store.meta('mock-command:'+key)['result'],{'generation':2})
            finally:store.close();mock.close()
    def test_mm_command_is_durable_and_duplicate_is_not_executed_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=Store(root/'ops');mock=MockGitLab(root/'mock')
            try:
                commands=MockCommands(store,mock)
                route={'channel_id':'c','root_id':'r','source_user_id':'u'}
                key=commands.enqueue('create demo','post1',route)
                self.assertEqual(key,commands.enqueue('create demo','post1',route))
                self.assertFalse(mock.repository('demo').exists())
                commands.tick();commands.tick()
                self.assertEqual(store.meta('mock-command:'+key)['state'],'done')
                self.assertTrue(mock.repository('demo').exists())
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)
            finally:store.close();mock.close()

    def test_unknown_commands_and_external_repositories_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=Store(root/'ops');mock=MockGitLab(root/'mock')
            try:
                commands=MockCommands(store,mock)
                for command in ('create ../../real','approve mr true','create https://gitlab/real'):
                    with self.assertRaises(ValueError):commands.enqueue(command,'p',{})
            finally:store.close();mock.close()

    def test_interrupted_command_is_reported_without_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=Store(root/'ops');mock=MockGitLab(root/'mock')
            try:
                commands=MockCommands(store,mock)
                key=commands.enqueue('create demo','post',{'channel_id':'c','root_id':'r','source_user_id':'u'})
                item=store.meta('mock-command:'+key)
                item['state']='running';store.set_meta('mock-command:'+key,item)
                mock.create_project('demo')
                commands.recover();commands.tick()
                self.assertEqual(store.meta('mock-command:'+key)['state'],'interrupted')
                self.assertEqual(mock.project_number('demo'),1)
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)
            finally:store.close();mock.close()
