import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from amplifier_acp.server import Server,Session
from amplifier_acp.worker import NativeError

@pytest.mark.asyncio
@pytest.mark.parametrize('loss',['retirement','replacement','closed','retired_reply'])
async def test_resident_observation_never_starts_or_replaces_after_delivery_preflight(tmp_path,loss):
    session=Session(Server({'appHome':str(tmp_path)},None),'fixture',str(tmp_path))
    session.start=AsyncMock(side_effect=AssertionError('Resident operation must not mount'))
    worker=SimpleNamespace(closed=False,process=SimpleNamespace(returncode=None))
    async def delivery(*args,**kwargs):
        if loss=='retirement':session.worker_retirement_pending=worker
        if loss=='replacement':session.worker=SimpleNamespace()
        if loss=='closed':worker.closed=True
        if loss=='retired_reply':raise NativeError('retired',data={'executed':False})
        return {'delivery':'unknown'}
    worker.command=delivery;worker.control=AsyncMock(side_effect=AssertionError('No admission after known worker loss'));session.worker=worker
    result=await session.settled_input('observation.submit',{'inputId':'fixture'},require_existing=True)
    assert result['accepted'] is False and result['executed'] is False
    session.start.assert_not_awaited();worker.control.assert_not_awaited()
