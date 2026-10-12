import json
from types import SimpleNamespace
import pytest
from amplifier_acp.native.peer_results import capture, install, terminal


def saved(tmp_path, *, origin='peer', duplicate=False):
    directory = tmp_path/'native-session'; directory.mkdir()
    user = {'role': 'user', 'content': 'A scoped request', 'metadata': {
        'inputOrigin': origin, 'amplifier_input': {'id': 'peer:one'}}}
    rows = [user] * (2 if duplicate else 1) + [{'role': 'assistant', 'content': 'Saved answer'}]
    (directory/'transcript.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    (directory/'metadata.json').write_text(json.dumps({'session_id': directory.name}))
    return directory


def finished(**changes):
    return {'type': 'generation.finished', 'generation_id': 'generation-1', 'input_ids': ['peer:one'],
            'text': 'Saved answer', 'active_job_ids': [], 'disposition': 'manager_turn_finished', **changes}


def test_only_exact_saved_peer_answer_can_qualify(tmp_path):
    directory = saved(tmp_path)
    proof = capture(directory, tmp_path/'app', directory.name, {'id': 'generation-1', 'input_ids': ['peer:one']})
    assert proof['messageId'].startswith('native:') and proof['inputIds'] == ['peer:one']
    worker = SimpleNamespace(peer_terminal=proof)
    assert terminal(worker, finished()) == proof
    for changes in [{'text': 'Another answer'}, {'generation_id': 'another'}, {'input_ids': []},
                    {'active_job_ids': ['unfinished']}, {'type': 'generation.failed'}, {'disposition': 'unknown'}]:
        assert terminal(worker, finished(**changes)) is None


@pytest.mark.parametrize('options', [{'origin': 'user'}, {'origin': 'feedback'}, {'duplicate': True}])
def test_unrelated_or_ambiguous_history_cannot_supply_peer_proof(tmp_path, options):
    directory = saved(tmp_path, **options)
    assert capture(directory, tmp_path/'app', directory.name, {'id': 'generation-1', 'input_ids': ['peer:one']}) is None


@pytest.mark.asyncio
async def test_checkpoint_failure_clears_prior_proof_and_cannot_seal(tmp_path):
    async def checkpoint(): raise OSError('checkpoint failed')
    capabilities = {'live.checkpoint': checkpoint}
    coordinator = SimpleNamespace(get_capability=capabilities.get, register_capability=capabilities.__setitem__)
    worker = SimpleNamespace(session=SimpleNamespace(coordinator=coordinator), peer_terminal={'old': True})
    install(worker)
    with pytest.raises(OSError): await capabilities['live.checkpoint']()
    assert worker.peer_terminal is None
