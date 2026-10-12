# Delegate execution presentation

The Native execution observer attaches the owning child's small display header
to model and tool observations. Later partial updates retain the child's
session identity. Tool observations carry this header through the additive ACP
`_meta["amplifier.dev/execution"]` version 1 field; standard tool-call identifiers
remain scoped by the child session.

The header carries delegate identity, label, selected provider/model and routing
facts. It excludes report bodies and summaries. It grants no worker actions.
The Host bounds this data before durable AHP projection, so a paged client can
show delegate ownership without requesting the full execution family.

`tests/test_model_recordings.py` covers partial updates, current child model
identity, root-call exclusion and the standard ACP tool envelope.
