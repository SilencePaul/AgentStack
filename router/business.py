"""Local operator entry point for running an approved acceptance script."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from .continuity import workspace_stamp
from .process_tree import ProcessTree
from .verification import Checks
from .worker import clean_environment


def verify(task, script, approved_digest, timeout):
    if not task or task['status'] not in ('succeeded', 'reported_unverified'):
        raise ValueError('Select a finished task with passing role gates')
    if timeout <= 0:
        raise ValueError('Verification timeout must be positive')
    repo = Path(task['project']['path']).resolve()
    evidence = Path(task['evidence']).resolve()
    script = Path(script).resolve()
    if script.is_relative_to(repo) or evidence.is_relative_to(repo):
        raise ValueError('Keep the approved script and evidence outside the project')
    source = script.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    if digest != approved_digest.lower():
        raise ValueError('Approved script SHA-256 mismatch')
    snapshot = json.loads((evidence/'final-snapshot.json').read_text(encoding='utf-8'))
    expected = snapshot['git']
    file_checks = Checks('', repo, evidence)
    file_checks.files = [{'path':name} for name in snapshot['files']]
    def matches():
        return workspace_stamp(repo) == expected and file_checks.fingerprints() == snapshot['files']
    if not expected or not matches():
        raise ValueError('Final Git workspace does not match the task snapshot')
    directory = Path(tempfile.mkdtemp(prefix='business-', dir=evidence))
    report = directory/'junit.xml'
    receipt = {'task_id':task['id'], 'attempt':task['attempt'], 'workspace':expected,
               'script_sha256':digest, 'passed':False, 'directory':str(directory)}
    # Run precisely the bytes approved by the operator, in a separate process tree.
    approved = directory/'approved.py'
    approved.write_bytes(source)
    tree = ProcessTree()
    try:
        with (directory/'output.log').open('wb') as log:
            process = subprocess.Popen([sys.executable, '-I', str(approved), str(report)],
                cwd=repo, env=clean_environment(), stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=(os.name != 'nt'))
            tree.attach(process)
            receipt['exit_code'] = process.wait(timeout=timeout)
        tree.close()
        if receipt['exit_code'] != 0:
            raise ValueError('Verification script failed')
        if not report.is_file() or report.stat().st_size > 20*1024*1024:
            raise ValueError('Missing or oversized JUnit XML')
        suites = ET.parse(report).getroot()
        cases = list(suites.iter('testcase'))
        if suites.tag not in ('testsuite','testsuites') or not cases:
            raise ValueError('Expected a JUnit report with executed cases')
        receipt['cases'] = len(cases)
        bad_counts = any(int(node.get(field, '0')) != 0
                         for node in suites.iter() if node.tag in ('testsuite','testsuites')
                         for field in ('failures','errors','skipped','disabled'))
        if bad_counts or any(node.tag in ('failure','error','skipped') for node in suites.iter()):
            raise ValueError('JUnit recorded a failed or skipped case')
        if not matches():
            raise ValueError('Workspace changed during verification')
        receipt['passed'] = True
    except (OSError, ValueError, ET.ParseError, subprocess.SubprocessError) as exc:
        receipt['error'] = str(exc)
    finally:
        tree.close()
        (directory/'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    return receipt
