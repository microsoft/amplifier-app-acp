"""Current Unified context policy applies equally to roots and child mounts."""
import copy

from amplifier_acp.native.host.session import live_plan


def test_archive_recovery_mounts_preserve_explicit_choices_and_original_plan():
    def node(context):
        return {'session': {'orchestrator': {'module': 'loop-live'}, 'context': context}}
    original = node({'module': 'context-managed', 'config': {'engine': 'boundary'}})
    original['agents'] = {
        'inherited': node({'module': 'context-managed', 'config': {'engine': 'boundary'}}),
        'disabled': node({'module': 'context-managed', 'config': {
            'engine': 'boundary', 'archive_recovery': False, 'durable_checkpoints': False}}),
        'simple': node({'module': 'context-simple'}),
    }
    before = copy.deepcopy(original)
    mounted, _ = live_plan(original)
    for item in [mounted, mounted['agents']['inherited']]:
        config = item['session']['context']['config']
        assert config['archive_recovery'] is True
        assert config['durable_checkpoints'] is True
    assert mounted['agents']['disabled']['session']['context']['config'] == before['agents']['disabled']['session']['context']['config']
    assert mounted['agents']['simple']['session']['context'] == {'module': 'context-simple'}
    assert original == before
