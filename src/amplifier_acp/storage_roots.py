"""Passive owner-declared accounting boundaries, never cleanup authorization."""

def storage_roots(app, native):
    roots=[{'id':'native-app','scope':'app','label':'Native configuration and saved data','path':str(app)},
           {'id':'history','scope':'history','label':'Shared CLI and Unified history (read only)','path':str(native/'projects')}]
    content=[{**roots[0],'category':'other'}, {**roots[1],'category':'sessions','optional':True}]
    groups={
        'workspaces': ['workspaces'],
        'sessions': ['chats','sessions','artifacts','attachments','diagnostics','app.sqlite3','app.sqlite3-wal','app.sqlite3-shm','recall.sqlite3','recall.sqlite3-wal','recall.sqlite3-shm'],
        'runtime': ['runtime','foundation','source-store','updates'],
        'backups': ['backups','reset-backups','maintenance/artifacts','maintenance/retained'],
        'cache': ['cache','logs','foundation/cache','updates/releases'],
    }
    labels={'maintenance/artifacts':'Private native archives','maintenance/retained':'Native recovery copies','foundation/cache':'Component cache','updates/releases':'Downloaded update releases','source-store':'Component sources','app.sqlite3':'Native app records','app.sqlite3-wal':'Native app pending records','app.sqlite3-shm':'Native app shared records','recall.sqlite3':'Native memory records','recall.sqlite3-wal':'Native memory pending records','recall.sqlite3-shm':'Native memory shared records'}
    for category,paths in groups.items():
        for relative in paths:
            content.append({'id':'native:'+relative,'scope':'app','label':labels.get(relative,relative.replace('-',' ').capitalize()),'path':str(app/relative),'category':category,'optional':True})
    # A co-located CLI home is not app settings. Its shared history is counted by
    # its own, more-specific root above, without reading CLI credentials.
    if native!=app and native.is_relative_to(app):
        content.append({'id':'shared-cli','scope':'app','label':'CLI configuration','path':str(native),'category':None,'optional':True})
    return {'version':1,'roots':roots,'contentVersion':1,'contentRoots':content}
