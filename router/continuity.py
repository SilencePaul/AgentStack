"""Bounded question receipts and independently collected workspace checkpoints."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

RECEIPT_INSTRUCTION = '''If a human answer is essential, stop doing work and end with exactly one JSON block:
```router-receipt
{"status":"needs_input","question":"one clear question","completed":"observed progress","remaining":"next steps"}
```
Do not include secrets in the question. The closing fence must be alone on its line.
Put any explanation before the receipt; output nothing after it. Never combine needs_input with
PASS/BLOCKED verdicts or instructions to keep executing. Exit normally and wait for the human answer.
This is NOT completion.
Otherwise finish the assigned phase normally, preserving any required terminal PASS/BLOCKED marker.
'''


def read_receipt(text):
    if '```router-receipt' not in text:return None
    matches=list(re.finditer(r'^```router-receipt[ \t]*\r?\n(.*?)^```[ \t]*(?=\r?$)',text,re.M|re.S))
    if len(matches)!=1 or text.count('```router-receipt')!=1:
        raise ValueError('Malformed question receipt')
    match=matches[0]
    tail=text[match.end():]
    outside=text[:match.start()]+tail
    if '```' in tail or '~~~' in tail or re.search(r'\bROUTER_(?:REVIEW|TEST|LEAD)\s*:\s*(?:PASS|BLOCKED|PLAN|REWORK|STOP)\b',outside):
        raise ValueError('Conflicting content after question receipt')
    if len(tail)>4000:
        raise ValueError('Question receipt explanation exceeds limit')
    # Trailing prose is ignored, never interpreted as permission to execute or
    # a completion verdict. Only the validated needs_input payload is returned.
    blocks=[match.group(1)]
    if len(blocks[0])>12000:raise ValueError('Question receipt exceeds limit')
    data=json.loads(blocks[0])
    if not isinstance(data,dict) or set(data)!={'status','question','completed','remaining'} or data['status']!='needs_input':
        raise ValueError('Invalid question receipt schema')
    for key,limit in (('question',1000),('completed',4000),('remaining',4000)):
        if not isinstance(data[key],str) or not data[key].strip() or len(data[key])>limit:
            raise ValueError('Invalid question receipt field: '+key)
    return data


def workspace_stamp(root):
    """Git HEAD, index/worktree diff and non-ignored untracked bytes; ignored files excluded."""
    root=Path(root).resolve()
    def git(*args,allow_unborn=False):
        result=subprocess.run(['git','-C',str(root),*args],capture_output=True,timeout=20)
        if result.returncode:
            if allow_unborn:return b'UNBORN'
            raise ValueError('Cannot checkpoint workspace with Git')
        if len(result.stdout)>20*1024*1024:raise ValueError('Workspace checkpoint exceeds 20 MiB')
        return result.stdout
    try:
        top=Path(git('rev-parse','--show-toplevel').decode().strip()).resolve()
        head=git('rev-parse','--verify','HEAD',allow_unborn=True).decode().strip()
        digest=hashlib.sha256()
        for label,args in (('worktree',('diff','--no-ext-diff','--binary')),('index',('diff','--cached','--no-ext-diff','--binary'))):
            digest.update(label.encode());digest.update(git(*args))
        total=0
        for name in sorted(git('ls-files','--others','--exclude-standard','-z').split(b'\0')):
            if not name:continue
            path=root/Path(name.decode('utf-8'))
            if path.is_symlink() or not path.resolve().is_relative_to(top):raise ValueError('Unsupported untracked symlink')
            total+=path.stat().st_size
            if total>20*1024*1024:raise ValueError('Untracked checkpoint exceeds 20 MiB; ignore build artifacts first')
            digest.update(name+b'\0');digest.update(hashlib.sha256(path.read_bytes()).digest())
        return {'root':str(root),'head':head,'sha256':digest.hexdigest(),'scope':'Git tracked changes and non-ignored untracked files; ignored files excluded'}
    except (OSError,UnicodeError,subprocess.SubprocessError) as exc:
        raise ValueError('Workspace checkpoint unavailable: '+type(exc).__name__) from None
