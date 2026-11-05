"""Deterministic acceptance checks from the user's prompt, never model claims."""
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re


class Checks:
    def __init__(self, prompt, work_dir, protected=None):
        self.root = Path(work_dir).resolve()
        self.protected = Path(protected).resolve() if protected else None
        self.files = []
        blocks = re.findall(r'^```bridge-checks\s*\n(.*?)^```\s*$', prompt, re.M | re.S)
        if '```bridge-checks' in prompt and len(blocks) != 1:
            raise ValueError('验收条件格式错误：使用一个完整的 bridge-checks JSON 代码块；尚未执行任务')
        if blocks:
            try:
                data = json.loads(blocks[0])
                if not isinstance(data, dict) or set(data) != {'files'}:
                    raise ValueError()
                self.files = data['files']
                if not isinstance(self.files, list) or not 1 <= len(self.files) <= 20:
                    raise ValueError()
                for rule in self.files:
                    if not isinstance(rule, dict) or 'path' not in rule or len(rule) != 2:
                        raise ValueError()
                    op = next(key for key in rule if key != 'path')
                    if op not in ('equals', 'contains', 'exists'):
                        raise ValueError()
                    if op == 'exists' and type(rule[op]) is not bool:
                        raise ValueError()
                    if op != 'exists' and not isinstance(rule[op], str):
                        raise ValueError()
                    if op == 'contains' and not rule[op]:
                        raise ValueError()
                    self.path(rule['path'])
            except (ValueError, TypeError, OSError, StopIteration):
                raise ValueError('验收条件无效：仅支持工作目录内相对文件路径，以及 equals/contains/exists；尚未执行任务') from None
        self.before = [self.check(rule)[0] for rule in self.files]

    def path(self, name):
        if not isinstance(name, str) or not name or len(name) > 1024:
            raise ValueError('路径无效')
        win = PureWindowsPath(name)
        if win.drive or win.root or Path(name).is_absolute() or '..' in win.parts:
            raise ValueError('仅支持工作目录内相对路径')
        target = (self.root / name.replace('\\', '/')).resolve()
        if not target.is_relative_to(self.root) or target == self.root:
            raise ValueError('路径越出工作目录')
        if self.protected and (target == self.protected or target.is_relative_to(self.protected)):
            raise ValueError('不能把桥接状态文件作为验收对象')
        return target

    def check(self, rule):
        try:
            path = self.path(rule['path'])  # Resolve again after execution: links may have changed.
            if 'exists' in rule:
                ok = path.is_file() if rule['exists'] else not path.exists()
                return ok, '文件状态符合条件' if ok else '文件状态不符'
            if not path.is_file():
                return False, '文件不存在或不是普通文件'
            if path.stat().st_size > 1024 * 1024:
                return False, '文件超过 1 MiB 核验上限'
            # Universal newline comparison; keep all other whitespace significant.
            with path.open(encoding='utf-8-sig') as handle:
                actual = handle.read(1024 * 1024 + 1)
            op = 'equals' if 'equals' in rule else 'contains'
            expected = rule[op].replace('\r\n', '\n').replace('\r', '\n')
            ok = actual == expected if op == 'equals' else expected in actual
            return ok, '内容符合条件' if ok else '内容不匹配'
        except (OSError, UnicodeError, ValueError):
            return False, '无法核验（路径不可访问、越界或不是 UTF-8 文本）'

    def fingerprints(self):
        result = {}
        for rule in self.files:
            path = self.path(rule['path'])
            value = 'directory' if path.is_dir() else 'absent'
            if path.is_file():
                digest = hashlib.sha256()
                with path.open('rb') as handle:
                    for chunk in iter(lambda: handle.read(65536), b''):
                        digest.update(chunk)
                value = digest.hexdigest()
            result[rule['path']] = value
        return result

    def evaluate(self):
        results = []
        for index, rule in enumerate(self.files):
            ok, reason = self.check(rule)
            note = '；执行前已满足，不能据此认定本轮做了修改' if ok and self.before[index] else ''
            # Do not echo actual/expected file contents (possibly sensitive).
            results.append((ok, rule['path'] + '：' + reason + note))
        return results


class Evidence:
    def __init__(self):
        self.tool_errors = 0
        self.validation_errors = 0
        self.validation_warnings = []
        self.calls = {}
        self.permission_denials = 0

    def observe(self, event):
        if event.get('type') == 'result':
            self.permission_denials += len(event.get('permission_denials') or [])
        if event.get('type') == 'assistant':
            message = event.get('message', {})
            blocks = message.get('content', []) if isinstance(message, dict) else []
            if isinstance(blocks, list):
                for block in blocks:
                    if isinstance(block, dict) and block.get('type') == 'tool_use':
                        self.calls[block.get('id')] = block
        if event.get('type') != 'user':
            return  # Assistant text never becomes runtime evidence.
        message = event.get('message', {})
        content = message.get('content', []) if isinstance(message, dict) else []
        if not isinstance(content, list):
            return
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'tool_result' and block.get('is_error') is True:
                call = self.calls.get(block.get('tool_use_id'), {})
                inputs = call.get('input')
                # Exact observed Qoder pre-execution schema rejection only. Other
                # validation wording, runtime failures and denials remain fatal.
                recoverable = (call.get('name') == 'Write' and isinstance(inputs, dict)
                               and 'content' not in inputs and block.get('content') ==
                               "Error: Write tool parameter validation failed: params must have required property 'content'")
                if recoverable:
                    self.validation_errors += 1
                    self.validation_warnings.append({'tool_use_id': block.get('tool_use_id'), 'tool': 'Write', 'reason': 'required content missing; tool rejected before execution'})
                else:
                    self.tool_errors += 1


def render_report(model_text, execution_failed, checks, evidence):
    results = checks.evaluate()
    runtime_failed = execution_failed or bool(evidence.tool_errors or evidence.permission_denials)
    failed = runtime_failed or any(not ok for ok, _ in results)
    if runtime_failed:
        heading = '⚠️ 执行异常，不能认定完成'
    elif results and failed:
        heading = '❌ 验收未通过'
    elif results:
        heading = '✅ 所列文件条件通过（不代表整项任务已完成）'
    else:
        heading = '⚠️ 未核验：本轮已返回，但未提供独立验收条件，不能认定任务完成'
    lines = [heading]
    if evidence.validation_errors:
        lines.append("Pre-execution parameter validation warnings: " + str(evidence.validation_errors) + "; independent review, testing and acceptance are still required.")
    if evidence.tool_errors:
        lines.append('检测到工具错误：' + str(evidence.tool_errors) + ' 次（即使模型随后称成功，也需复核）。')
    if evidence.permission_denials:
        lines.append('检测到权限拒绝：' + str(evidence.permission_denials) + ' 次。')
    if results:
        lines.append('桥接直接读取磁盘核对：' + str(sum(ok for ok, _ in results)) + '/' + str(len(results)))
        lines += [('通过：' if ok else '未通过：') + message for ok, message in results]
    else:
        lines.append('文件任务可在指令末尾附 bridge-checks 条件；工具退出正常和模型自述均不等于业务验收。')
    lines += ['', '模型回复（原文，不作为独立验收证据）：',
              '\n'.join('> ' + line for line in model_text.splitlines())]
    return '\n'.join(lines), bool(failed)
