"""Shutdown ownership checks use fakes and make no external requests."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from router.cli import serve
from router.service import Scheduler
from router.store import Store


class LifecycleTests(unittest.TestCase):
    def test_shutdown_continues_after_worker_stop_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            store=Store(':memory:')
            try:
                stopped=[]
                class FakeWorker:
                    def __init__(self,command,task,run_dir,on_event): self.task=task
                    def start(self): pass
                    def poll(self): return None
                    def stop(self,reason):
                        stopped.append(self.task['project_id'])
                        if self.task['project_id']=='a': raise RuntimeError('cleanup error')
                for name in ('a','b'):
                    (root/name).mkdir(); store.add_project(name,root/name); store.submit(name,'goal')
                scheduler=Scheduler(store,['fake'],root/'runs',worker_factory=FakeWorker)
                scheduler.tick()
                with self.assertLogs('router.service',level='ERROR'):
                    scheduler.shutdown()
                self.assertEqual(['a','b'],stopped)
                self.assertFalse(scheduler.workers)
                self.assertTrue(all(t['status']=='interrupted' for t in store.tasks()))
            finally: store.close()

    def test_service_cancels_and_joins_planning_before_return(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(':memory:')
            started=threading.Event(); exited=threading.Event(); owners=[]
            class FakePlanner:
                def __init__(self,*args,**kwargs): self.cancel_event=kwargs['cancel_event']
            def process_one(queue,planner):
                owners.append(threading.current_thread())
                started.set()
                if not planner.cancel_event.wait(3): raise AssertionError('No cancellation')
                exited.set()
                return False
            def tick(scheduler):
                if not started.wait(3): raise AssertionError('Planning did not start')
            try:
                with patch('router.planner.Planner',FakePlanner), patch('router.dispatch.PlanningQueue.process_one',process_one), patch('router.cli.Scheduler.tick',tick):
                    serve(store,{'qoder_command':['fake']},Path(tmp),once=True)
                self.assertTrue(exited.is_set())
                self.assertTrue(owners)
                self.assertTrue(all(not thread.is_alive() for thread in owners))
                self.assertEqual('stopped',store.meta('service_heartbeat'))
            finally: store.close()
