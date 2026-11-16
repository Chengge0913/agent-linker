"""Extract public messages and tool activity from WB or DSH event streams."""
import json
import sys
from pathlib import Path

LIMIT = 6000


def clipped(text):
    text = str(text)
    return text if len(text) <= LIMIT else text[:LIMIT] + '\n…（内容已截断，完整记录在事件文件中）'


def timeline(path):
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 2_000_000))
            lines = stream.read().decode('utf-8', errors='replace').splitlines()
    except OSError:
        return []
    result = []
    for line in lines:
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                continue
            kind = event.get('type')
            timestamp = event.get('__timestamp')
            if kind == 'tool_call':
                result.append({'kind': 'tool', 'time': timestamp, 'call_id': event.get('callId'),
                               'name': event.get('tool', '工具'), 'text': clipped(json.dumps(event.get('input', {}), ensure_ascii=False, indent=2))})
                continue
            if kind == 'tool_result':
                failed = str(event.get('status', '')).lower() in ('failed', 'error', 'denied')
                result.append({'kind': 'error' if failed else 'result', 'time': timestamp,
                               'call_id': event.get('callId'), 'text': clipped(event.get('result', '工具返回了非文本结果。'))})
                continue
            if kind == 'text' and event.get('text'):
                result.append({'kind': 'message', 'time': timestamp, 'text': clipped(event['text'])})
                continue
            if kind not in ('assistant', 'user'):
                continue
            for item in event.get('message', {}).get('content', []):
                category = item.get('type')
                if kind == 'assistant' and category == 'text' and item.get('text'):
                    result.append({'kind': 'message', 'time': timestamp, 'text': clipped(item['text'])})
                elif kind == 'assistant' and category == 'tool_use':
                    data = json.dumps(item.get('input', {}), ensure_ascii=False, indent=2)
                    result.append({'kind': 'tool', 'time': timestamp, 'call_id': item.get('id'), 'name': item.get('name', '工具'), 'text': clipped(data)})
                elif kind == 'user' and category == 'tool_result':
                    content = item.get('content', [])
                    if isinstance(content, str):
                        body = content
                    elif isinstance(content, list):
                        body = '\n'.join(str(part.get('text', '')) for part in content if isinstance(part, dict) and part.get('type') == 'text')
                    else:
                        body = ''
                    result.append({'kind': 'error' if item.get('is_error') else 'result',
                                   'time': timestamp, 'call_id': item.get('tool_use_id'), 'text': clipped(body or '工具返回了非文本结果。')})
        except (ValueError, TypeError, AttributeError):
            continue
    return result[-120:]


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(timeline(Path(sys.argv[1])), ensure_ascii=False))
