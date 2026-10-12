"""Installed ACP boundaries for the main-version bundle catalog and legacy IDs."""
import json
from pathlib import Path

import pytest
import yaml

from client import Client
from native_setup import configuration


async def initialize(client):
    return await client.request('initialize', {'protocolVersion': 1,
        'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})


async def test_installed_catalog_does_not_start_worker_or_rewrite_settings(tmp_path):
    config, cwd = configuration(tmp_path, workerCommand=['/no/worker/allowed'],
                                adminWorkspaceRoots=[str(tmp_path)])
    values = json.loads(config.read_text())
    settings = Path(values['home'])/'settings.yaml'
    settings.write_text('bundle:\n  active: foundation\n  app: []\n')
    before = settings.read_bytes()
    client = await Client.launch(config)
    try:
        await initialize(client)
        reply = await client.request('_amplifier/admin', {
            'cwd': str(cwd), 'operation': 'bundles.list', 'args': {}})
        rows = reply['result']['registeredBundles']
        assert [row['name'] for row in rows] == [
            'anchors', 'anchors-amp-dev', 'work', 'work-amp-dev']
        assert reply['result']['bundleDefaults']['shared'] == 'foundation'
        assert settings.read_bytes() == before
        assert not (Path(values['home'])/'projects').exists()
        assert not client.events
    finally:
        await client.close()


@pytest.mark.parametrize('legacy', ['Work', 'bundle:Work'])
async def test_installed_resume_legacy_work_preserves_transcript_without_call(tmp_path, legacy):
    config, cwd = configuration(tmp_path)
    values = json.loads(config.read_text())
    bundle = Path(values['bundle'])
    audit = tmp_path/'provider-audit'
    bundle.write_text(bundle.read_text().replace('config: {}',
        'config:\n      selectedModelAudit: '+str(audit)))
    shared = Path(values['home'])/'settings.yaml'
    shared.write_text(yaml.safe_dump({'bundle': {'app': [], 'added': {'work': str(bundle)}}}))
    settings_before = shared.read_bytes()
    client = await Client.launch(config)
    try:
        await initialize(client)
        loaded = await client.request('session/new', {'cwd': str(cwd), 'mcpServers': []})
        sid = loaded['result']['sessionId']
        recorded = await client.request('_amplifier/native', {'sessionId': sid,
            'operation': 'voice.transcript.record', 'args': {
                'callId': 'retained', 'itemId': 'original', 'role': 'user',
                'text': 'Retained words, do not execute', 'append': False, 'commandId': 'original'}})
        assert recorded['result']['recorded']
    finally:
        await client.close()
    transcript = next((Path(values['home'])/'projects').rglob('transcript.jsonl'))
    metadata = transcript.with_name('metadata.json')
    saved = json.loads(metadata.read_text())
    saved.update(bundle_name=legacy, bundle=legacy, name='Keep my title', name_source='manual')
    metadata.write_text(json.dumps(saved))
    before = transcript.read_bytes()
    client = await Client.launch(config)
    try:
        await initialize(client)
        reply = await client.request('session/resume', {
            'sessionId': sid, 'cwd': str(cwd), 'mcpServers': []})
        assert 'result' in reply, reply
        assert transcript.read_bytes() == before
        assert shared.read_bytes() == settings_before
        assert json.loads(metadata.read_text())['name'] == 'Keep my title'
        assert not audit.exists()
    finally:
        await client.close()
