import tempfile, unittest
from pathlib import Path
from router.store import Store
from router.service import Router, Scheduler
class FakeWorker:
 def __init__(self, command,task,run_dir,on_event):self.task=task;self.result=None
 def start(self):pass
 def poll(self):return self.result
 def stop(self,reason):self.result={'status':'interrupted','summary':reason,'evidence':[]}
class ServiceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.s=Store(self.root/'state.db')
  for name in ('alpha','beta'):
   path=self.root/name;path.mkdir();self.s.add_project(name,str(path))
  self.r=Router(self.s)
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def test_route_unknown_and_goal(self):
  self.assertIn('alpha',self.r.handle('/router projects'))
  with self.assertRaises(ValueError):self.r.handle('/router run missing do something')
  self.r.handle('/router run alpha create hello',source_key='p')
  self.assertEqual(self.s.tasks()[0]['goal'],'create hello')
 def test_parallel_and_failure_isolation(self):
  self.s.submit('alpha','one');self.s.submit('alpha','two');self.s.submit('beta','three')
  scheduler=Scheduler(self.s,['fake'],self.root/'runs',worker_factory=FakeWorker)
  scheduler.tick();self.assertEqual(len(scheduler.workers),2)
  first=next(w for w in scheduler.workers.values() if w.task['project_id']=='alpha')
  first.result={'status':'failed','summary':'tool failed','evidence':[]}
  scheduler.tick();self.assertEqual(len(scheduler.workers),1)
  self.assertTrue(any(t['status']=='failed' for t in self.s.tasks()))
  scheduler.shutdown()
 def test_pause_survives_router_instances(self):
  self.r.handle('/router pause');self.s.submit('alpha','one')
  scheduler=Scheduler(self.s,['fake'],self.root/'runs',worker_factory=FakeWorker)
  scheduler.tick();self.assertEqual(len(scheduler.workers),0)
  Router(self.s).handle('/router resume');scheduler.tick();self.assertEqual(len(scheduler.workers),1)
  scheduler.shutdown()

 def test_six_projects_drain_with_two_slots(self):
  names=['alpha','beta','gamma','delta','epsilon','zeta']
  for name in names[2:]:
   path=self.root/name;path.mkdir();self.s.add_project(name,str(path))
  for name in names:self.s.submit(name,'isolated goal '+name)
  scheduler=Scheduler(self.s,['fake'],self.root/'six-runs',limit=2,worker_factory=FakeWorker)
  seen=set()
  for _ in range(4):
   scheduler.tick()
   self.assertLessEqual(len(scheduler.workers),2)
   for worker in scheduler.workers.values():
    seen.add(worker.task['project_id'])
    self.assertEqual(worker.task['project']['path'],str((self.root/worker.task['project_id']).resolve()))
    worker.result={'status':'succeeded','summary':'checked','evidence':[]}
  self.assertEqual(seen,set(names))
  self.assertTrue(all(t['status']=='succeeded' for t in self.s.tasks()))
  scheduler.shutdown()
