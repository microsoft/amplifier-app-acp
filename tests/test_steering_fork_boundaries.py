import copy
import pytest
from amplifier_acp.native.session_store import user_boundaries, _input_identity


def test_canonical_steering_before_later_input_keeps_both_boundaries():
    rows = [{'role': 'user', 'content': 'Correction', 'metadata': {'amplifier_input': {'version': 1, 'kind': 'steer', 'source': 'user', 'id': 'steer-id'}}},
            {'role': 'assistant', 'content': 'Accepted correction'},
            {'role': 'user', 'content': 'Next request', 'metadata': {'amplifier_input': {'version': 1, 'kind': 'user', 'source': 'user', 'id': 'next-id'}}}]
    before = copy.deepcopy(rows)
    assert user_boundaries(rows, [{'role':'user','text':'Correction','inputId':'steer-id'}, {'role':'user','text':'Next request','inputId':'next-id'}]) == [0, 2]
    assert rows == before
    with pytest.raises(ValueError, match='transcript changed'):
        user_boundaries([*rows, rows[0]], [{'role':'user','text':'Correction','inputId':'steer-id'}])


@pytest.mark.parametrize('kind', ['service', ['steer'], None])
def test_non_user_boundaries_never_gain_identity(kind):
    row = {'role':'user','content':'Same text','metadata':{'amplifier_input':{'version':1,'kind':kind,'id':'input'}}}
    assert _input_identity(row) is None
    with pytest.raises(ValueError, match='transcript changed'):
        user_boundaries([row], [{'role':'user','text':'Same text','inputId':'input','nativeMessageId':'bound'}])
