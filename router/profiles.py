"""Operator-loaded role prompts; never a source of runtime permissions."""
import hashlib
import json
from pathlib import Path
import re

SLOTS = {'leader','developer','reviewer','tester'}

def load_profiles(directory):
    root = Path(directory).resolve(strict=True)
    def read(path):
        path = path.resolve()
        if not path.is_relative_to(root) or path.stat().st_size > 65536:
            raise ValueError('Profile path escapes its directory or exceeds 64 KiB')
        return path.read_text(encoding='utf-8-sig')
    manifest = json.loads(read(root/'agents.json'))
    if not isinstance(manifest,dict) or set(manifest)-{'variables','roles'}:
        raise ValueError('Expected variables and roles only')
    variables = manifest.get('variables',{})
    roles = manifest.get('roles',{})
    if not isinstance(variables,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in variables.items()):
        raise ValueError('Variables must be strings')
    if not isinstance(roles,dict) or set(roles)-SLOTS:
        raise ValueError('Supported slots: leader, developer, reviewer, tester')
    def render(text):
        def replace(match):
            key=match.group(1)
            if key not in variables:raise ValueError('Missing profile variable: '+key)
            return variables[key]
        result=re.sub(r'{{\s*([A-Z][A-Z0-9_]*)\s*}}',replace,text)
        if '{{' in result or '}}' in result or len(result)>65536:
            raise ValueError('Unresolved template or profile too large')
        return result
    result={}
    for slot,entry in roles.items():
        if not isinstance(entry,dict) or set(entry)-{'id','version','directory','skills'} or not {'id','version','directory'} <= entry.keys():
            raise ValueError('Each role requires id, version and directory; skills is optional')
        for field in ('id','version'):
            if not isinstance(entry[field],str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',entry[field]):
                raise ValueError('Invalid profile '+field)
        if not isinstance(entry['directory'],str):raise ValueError('Invalid profile directory')
        folder=root/entry['directory']
        profile={'id':entry['id'],'version':entry['version'],'soul':render(read(folder/'SOUL.md')),'agent':render(read(folder/'AGENT.md'))}
        if 'skills' in entry:
            skills = entry['skills']
            if not isinstance(skills,list) or len(skills)>128 or not all(isinstance(name,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:_.-]{0,127}',name) for name in skills):
                raise ValueError('skills must be a list of Qoder skill names')
            if len(set(skills))!=len(skills):raise ValueError('Duplicate skill name')
            profile['skills'] = list(skills)
        profile['digest']=hashlib.sha256(json.dumps(profile,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        result[slot]=profile
    return result
