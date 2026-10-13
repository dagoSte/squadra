"""Synthetic-only stdio MCP fixture; neither names nor fields model Sentry's API."""
import json
from pathlib import Path
import sys

example = json.loads((Path(__file__).resolve().parents[1] / 'plugins/sentry/examples/issues.json').read_text())
for line in sys.stdin:
    request = json.loads(line)
    method = request['method']
    if method == 'notifications/initialized':
        continue
    if method == 'initialize':
        result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'synthetic-only-fixture', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'synthetic_issue_summaries',
                            'description': 'Synthetic fixture, not a Sentry tool',
                            'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]}
    elif method == 'tools/call' and request['params'] == {'name': 'synthetic_issue_summaries', 'arguments': {}}:
        result = {'content': [{'type': 'text', 'text': json.dumps(example)}], 'isError': False}
    else:
        print(json.dumps({'jsonrpc': '2.0', 'id': request.get('id'),
                          'error': {'code': -32601, 'message': 'Unknown synthetic fixture method'}}), flush=True)
        continue
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
