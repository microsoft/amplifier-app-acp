"""Location-only discovery hints emitted after successful durable native writes."""
_observer=None

def set_observer(observer):
    global _observer
    _observer=observer

def published(directory,metadata):
    if callable(_observer):
        _observer({'type':'history.persisted','sessionId':metadata.get('session_id') or directory.name,
            'cwd':metadata.get('working_dir') or metadata.get('project_dir'),
            'parentId':metadata.get('parent_id'), 'metadataPath':str(directory/'metadata.json')})
