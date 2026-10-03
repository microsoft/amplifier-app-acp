"""Read reviews coexist with metadata; exact settings and effect locks remain."""
import asyncio
import sys

import pytest

from test_app_reset import call, hold, opened, prepare


@pytest.mark.asyncio
async def test_review_with_passive_reader_but_apply_and_restore_require_exclusion(tmp_path):
    from amplifier_acp.maintenance_gate import Gate

    client, cwd, home, app = await opened(tmp_path)
    path = app / 'admin-settings.yaml'
    original = b'bundle:\n  active: original\nother: preserved\n'
    path.write_bytes(original)
    try:
        auth = await hold(client, cwd)
        # A different process retains the exact shared admission used by passive
        # managed defaults/models. No timer or suppressed browser read is needed.
        with Gate({'home': str(home)}):
            review = await prepare(client, cwd, auth, 'review-with-reader')
            inspected = await call(client, cwd, 'inspect', {
                'preparedId': review['preparedId'], 'reviewHash': review['reviewHash'],
            })
            assert inspected['result']['applicable'] is True
            assert inspected['result']['revision'] == review['revision']
            args = {'commandId': 'apply-busy', 'preparedId': review['preparedId'],
                    'reviewHash': review['reviewHash']}
            refused = await call(client, cwd, 'apply', args, auth)
            assert refused['error']['data'] == {
                'reason': 'native-maintenance-busy', 'executed': False, 'replayed': False,
            }
            receipt = (await call(client, cwd, 'inspect', {'commandId': 'apply-busy'}))['result']['receipt']
            assert receipt['state'] == 'refused' and receipt['executed'] is False
            assert path.read_bytes() == original

        # An exact refused command remains refused after the reader leaves. A
        # separately explicit fresh admission is needed; no automatic retry.
        same = (await call(client, cwd, 'apply', args, auth))['result']
        assert same['receipt']['state'] == 'refused' and same['executed'] is False
        applied = (await call(client, cwd, 'apply', {**args, 'commandId': 'apply-new'}, auth))['result']
        assert applied['receipt']['state'] == 'succeeded'
        reset_bytes = path.read_bytes()
        with Gate({'home': str(home)}):
            undo = await prepare(client, cwd, auth, 'undo-review-with-reader', restoreCommandId='apply-new')
            restore = {'commandId': 'undo-busy', 'preparedId': undo['preparedId'],
                       'reviewHash': undo['reviewHash'], 'resetCommandId': 'apply-new',
                       'expectedPostResetRevision': applied['postResetRevision']}
            refused = await call(client, cwd, 'restore', restore, auth)
            assert refused['error']['data']['reason'] == 'native-maintenance-busy'
            assert refused['error']['data']['executed'] is False
            assert path.read_bytes() == reset_bytes
        restored = (await call(client, cwd, 'restore', {**restore, 'commandId': 'undo-new'}, auth))['result']
        assert restored['restored'] is True and path.read_bytes() == original
        assert not client.events  # Impossible worker command: no runtime/provider.
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_settings_writer_serializes_review_and_stale_cas_still_refuses(tmp_path):
    client, cwd, home, app = await opened(tmp_path)
    path = app / 'admin-settings.yaml'
    path.write_text('bundle:\n  active: original\nother: preserved\n')
    writer = None
    try:
        auth = await hold(client, cwd)
        review = await prepare(client, cwd, auth, 'before-write')
        # Use the real Foundation settings writer in an independent process.
        # Its public mutator runs with the exact file lock held. Pipes control
        # the interleaving, so the test does not depend on a scheduling sleep.
        writer = await asyncio.create_subprocess_exec(sys.executable, '-c', '''
import sys
from pathlib import Path
from amplifier_foundation.settings import update_settings
def mutate(body):
    print('locked', flush=True)
    assert sys.stdin.readline().strip() == 'commit'
    body.setdefault('bundle', {})['active'] = 'writer-newer'
update_settings(Path(sys.argv[1]), mutate)
''', str(path), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        assert await asyncio.wait_for(writer.stdout.readline(), 10) == b'locked\n'
        for operation, args in [
            ('inspect', {'preparedId': review['preparedId'], 'reviewHash': review['reviewHash']}),
            ('prepare', {'commandId': 'during-write', 'parts': ['native.app-bundle-default'],
                         'privateContentReviewed': True}),
        ]:
            result = await call(client, cwd, operation, args, auth)
            assert result['error']['data'] == {
                'reason': 'native-app-reset-settings-busy', 'executed': False, 'replayed': False,
            }
        receipt = (await call(client, cwd, 'inspect', {'commandId': 'during-write'}))['result']['receipt']
        assert receipt['state'] == 'refused' and receipt['executed'] is False
        writer.stdin.write(b'commit\n')
        await writer.stdin.drain()
        await asyncio.wait_for(writer.wait(), 10)
        assert writer.returncode == 0, (await writer.stderr.read()).decode()
        changed = path.read_bytes()
        current = (await call(client, cwd, 'inspect', {
            'preparedId': review['preparedId'], 'reviewHash': review['reviewHash'],
        }))['result']
        assert current['applicable'] is False and current['revision'] != review['revision']
        refused = await call(client, cwd, 'apply', {
            'commandId': 'stale-apply', 'preparedId': review['preparedId'], 'reviewHash': review['reviewHash'],
        }, auth)
        assert refused['error']['data']['executed'] is False
        assert path.read_bytes() == changed
        fresh = await prepare(client, cwd, auth, 'after-write')
        assert fresh['revision'] == current['revision']
        assert not client.events
    finally:
        if writer and writer.returncode is None:
            writer.kill()
            await writer.wait()
        await client.close()
