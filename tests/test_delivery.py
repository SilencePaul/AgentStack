import tempfile,threading,unittest
from pathlib import Path
from router.store import Store
from router.cli import deliver_pending
from router.mm import TransportRejected
class Tests(unittest.TestCase):
 def test_rejected_content_not_retried(self):
  with tempfile.TemporaryDirectory() as d:
   s=Store(Path(d)/'s.db');s.enqueue_notice({'root_id':'r'},'raw report','test')
   class Client:
    calls=0
    def send(self,route,text,key):
     self.calls+=1
     if key=='test':raise TransportRejected('WAF')
   c=Client();deliver_pending(s,c,threading.Event());deliver_pending(s,c,threading.Event());deliver_pending(s,c,threading.Event())
   self.assertEqual(c.calls,2);self.assertTrue(s.meta('outbox_blocked:1'));s.close()
