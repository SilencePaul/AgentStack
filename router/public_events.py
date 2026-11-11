"""Public execution observations. Reasoning blocks are deliberately excluded."""
import json
from .diagnostic_reports import redact


def public_events(event):
    role = event.get('type')
    message = event.get('message', {})
    blocks = message.get('content', []) if isinstance(message, dict) else []
    if not isinstance(blocks, list):
        return []
    output = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get('type')
        if role == 'assistant' and kind == 'text':
            output.append({'type': 'message', 'text': redact(block.get('text', ''))[:6000]})
        elif role == 'assistant' and kind == 'tool_use':
            inputs = block.get('input', {})
            # File contents/Write payloads stay in the workspace; only command/path metadata goes on the timeline.
            args = {key: inputs[key] for key in ('command', 'file_path', 'path', 'pattern')
                    if isinstance(inputs, dict) and key in inputs}
            output.append({'type': 'tool', 'tool': str(block.get('name', ''))[:80],
                           'text': redact(json.dumps(args, ensure_ascii=False))[:2000]})
        elif role == 'user' and kind == 'tool_result':
            content = block.get('content', '')
            if isinstance(content, list):
                content = '\n'.join(str(item.get('text', '')) for item in content
                                    if isinstance(item, dict) and item.get('type') == 'text')
            if isinstance(content, str):
                output.append({'type': 'tool_result', 'error': bool(block.get('is_error')),
                               'text': redact(content)[:6000]})
    return output
