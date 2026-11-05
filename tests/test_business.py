import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from router.business import verify
from router.continuity import workspace_stamp


class BusinessVerification(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.repo = root/'repo'
        subprocess.run(['git','init','-q',str(self.repo)],check=True)
        self.script = root/'approved.py'
        self.evidence = root/'run'
        self.evidence.mkdir()
        snapshot = {'git':workspace_stamp(self.repo),'files':{}}
        (self.evidence/'final-snapshot.json').write_text(json.dumps(snapshot))
        self.task = {'id':'t','status':'reported_unverified','attempt':1,
                     'project':{'path':str(self.repo)},'evidence':str(self.evidence)}

    def run_xml(self, xml, extra=''):
        self.script.write_text('from pathlib import Path\nimport sys\nPath(sys.argv[1]).write_text('
                               + repr(xml) + ')\n' + extra)
        digest = hashlib.sha256(self.script.read_bytes()).hexdigest()
        return verify(self.task,self.script,digest,5)

    def test_verified_evidence_and_changed_version(self):
        receipt = self.run_xml('<testsuite><testcase name="approved"/></testsuite>')
        self.assertTrue(receipt['passed'])
        self.assertEqual(receipt['cases'],1)
        self.assertTrue((Path(receipt['directory'])/'output.log').is_file())
        (self.repo/'changed').write_text('new version')
        with self.assertRaisesRegex(ValueError,'snapshot'):
            self.run_xml('<testsuite/>')

    def test_refuse_false_pass(self):
        for xml, extra in (('<testsuite/>',''), ('<testsuite><testcase><skipped/></testcase></testsuite>',''),
                           ('<testsuite><testcase/></testsuite>','sys.exit(1)'),
                           ('<testsuite><testcase/></testsuite>',"Path('changed').touch()")):
            with self.subTest(xml=xml,extra=extra):
                self.assertFalse(self.run_xml(xml,extra)['passed'])
