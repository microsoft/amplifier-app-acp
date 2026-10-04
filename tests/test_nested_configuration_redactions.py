"""Editing public mount plans must not lose or cross-wire nested credentials."""
import copy
import json
from pathlib import Path

import pytest
import yaml

from amplifier_acp.native.runtime_controls import public_config, restore_redactions
from client import Client
from native_setup import configuration


@pytest.mark.parametrize('key', ['instance_id', 'id', 'module', 'name', None])
def test_nested_reordered_credentials_match_unique_entry(key):
    entries = [{'url': 'https://a.invalid', 'api_key': 'synthetic-a'},
               {'url': 'https://b.invalid', 'api_key': 'synthetic-b'}]
    if key:
        for suffix, row in zip(['a', 'b'], entries):
            row[key] = suffix
    previous = {'endpoints': entries}
    draft = public_config(previous)
    draft['endpoints'].reverse()
    if key:
        draft['endpoints'][0]['label'] = 'edited non-secret field'
    result = restore_redactions(draft, previous)
    assert [row['api_key'] for row in result['endpoints']] == ['synthetic-b', 'synthetic-a']
    assert previous['endpoints'][0]['api_key'] == 'synthetic-a'
    assert all(row['api_key'] == '[REDACTED]' for row in draft['endpoints'])


@pytest.mark.parametrize('case', ['missing-id', 'changed-anonymous-url', 'duplicate-old-id',
    'duplicate-draft-id', 'duplicate-old-structure', 'duplicate-draft-structure',
    'mixed-identities', 'removed-id', 'identity-kind-change', 'boolean-is-not-number'])
def test_ambiguous_or_changed_counterpart_refuses_secret_restoration(case):
    old = [{'id': 'one', 'url': 'https://a.invalid', 'api_key': 'synthetic-a'}]
    draft = public_config(old)
    if case == 'missing-id':
        draft[0]['id'] = 'new'
    elif case == 'changed-anonymous-url':
        old[0].pop('id'); draft[0].pop('id'); draft[0]['url'] = 'https://other.invalid'
    elif case == 'duplicate-old-id':
        old.append({**old[0], 'api_key': 'synthetic-b'})
    elif case == 'duplicate-draft-id':
        draft.append(copy.deepcopy(draft[0]))
    elif case == 'duplicate-old-structure':
        old[0].pop('id'); draft[0].pop('id'); old.append({**old[0], 'api_key': 'synthetic-b'})
    elif case == 'duplicate-draft-structure':
        old[0].pop('id'); draft[0].pop('id'); draft.append(copy.deepcopy(draft[0]))
    elif case == 'mixed-identities':
        old[0].pop('id'); draft[0].pop('id'); old.append({'id': 'unrelated'})
    elif case == 'removed-id':
        draft[0].pop('id')
    elif case == 'identity-kind-change':
        draft[0]['name'] = draft[0].pop('id')
    elif case == 'boolean-is-not-number':
        old[0].pop('id'); draft[0].pop('id'); old[0]['enabled'] = True; draft[0]['enabled'] = 1
    originals = copy.deepcopy((old, draft))
    with pytest.raises(ValueError, match='redacted value'):
        restore_redactions(draft, old)
    assert (old, draft) == originals


def test_nested_lists_explicit_replacements_and_deleted_rows_preserve_draft_shape():
    old = {'groups': [[{'url': 'a', 'authorization': 'synthetic-auth'}]],
           'values': [False, 1, None], 'removed': [{'api_key': 'synthetic-removed'}]}
    draft = public_config(old)
    draft['values'] = [2, True]
    draft['removed'] = []
    result = restore_redactions(draft, old)
    assert result['groups'] == old['groups']
    assert result['values'] == [2, True] and result['removed'] == []
    explicit = {'endpoints': [{'url': 'new', 'api_key': 'synthetic-explicit'}]}
    assert restore_redactions(explicit, {'endpoints': []}) == explicit


@pytest.mark.parametrize('case', ['unchanged', 'reordered', 'ambiguous'])
async def test_actual_configuration_apply_preserves_nested_credentials(tmp_path, monkeypatch, case):
    monkeypatch.setenv('AMPLIFIER_RUNTIME_IMMUTABLE', '1')
    monkeypatch.setenv('UV_OFFLINE', '1')
    config, workspace = configuration(tmp_path)
    values = json.loads(config.read_text())
    bundle_file = Path(values['bundle'])
    bundle = yaml.safe_load(bundle_file.read_text())
    original = [{'url': 'https://a.invalid', 'api_key': 'synthetic-a'},
                {'url': 'https://b.invalid', 'api_key': 'synthetic-b'}]
    bundle['providers'][0]['config'] = {'endpoints': original}
    bundle_file.write_text(yaml.safe_dump(bundle))
    client = await Client.launch(config)
    try:
        await client.request('initialize', {'protocolVersion': 1,
            'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
        made = await client.request('session/new', {'cwd': str(workspace), 'mcpServers': []})
        assert 'result' in made, made
        sid = made['result']['sessionId']
        async def control(operation, args=None):
            return await client.request('_amplifier/native', {'sessionId': sid,
                'operation': operation, 'args': args or {}})
        before = (await control('configuration.inspect'))['result']
        draft = copy.deepcopy(before['plan'])
        draft['session']['context']['config'] = {'max_tokens': 4321}
        ambiguous = case == 'ambiguous'
        expected = original if case == 'unchanged' else list(reversed(original))
        if case != 'unchanged':
            draft['providers'][0]['config']['endpoints'].reverse()
        if ambiguous:
            draft['providers'][0]['config']['endpoints'][0]['url'] = 'https://new.invalid'
        result = await control('configuration.apply', {'config': draft,
            'expectedConfigurationRevision': before['configurationRevision']})
        current = (await control('configuration.inspect'))['result']
        assert 'synthetic-' not in json.dumps([before, result, current])
        saved = Path(values['appHome']) / 'sessions' / sid / 'configuration.json'
        if ambiguous:
            assert result['error']['data'] == {'restored': True, 'ready': True, 'replayed': False}
            assert current['configurationRevision'] == before['configurationRevision']
            assert current['plan'] == before['plan'] and not saved.exists()
        else:
            assert result['result']['remounted'], result
            assert current['plan']['session']['context']['config']['max_tokens'] == 4321
            private = json.loads(saved.read_text())
            assert private['providers'][0]['config']['endpoints'] == expected
            assert current['plan']['providers'][0]['config']['endpoints'] == public_config(expected)
        (tmp_path / 'redaction-proof.json').write_text(json.dumps({
            'case': case, 'ambiguous': ambiguous, 'result': result,
            'secretAbsentFromPublicResponses': True, 'inferenceRequests': 0,
            'privateValuesMatchedByEndpoint': not ambiguous,
            'priorConfigurationPreserved': ambiguous}, indent=2))
    finally:
        (tmp_path / 'stderr.log').write_text(await client.close())
