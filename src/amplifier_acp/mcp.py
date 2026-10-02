"""ACP-provided MCP servers use the ecosystem MCP implementation unchanged."""
from pathlib import Path

def validate_servers(servers):
    if not isinstance(servers,list):raise ValueError('mcpServers must be an array.')
    names=set()
    for server in servers:
        if not isinstance(server,dict) or not isinstance(server.get('name'),str) or not server['name'] or server['name'] in names:
            raise ValueError('MCP server names must be nonempty and unique.')
        names.add(server['name'])
        if server.get('type') not in {None,'stdio'}:raise ValueError('Only stdio MCP transport is advertised.')
        if not isinstance(server.get('command'),str) or not Path(server['command']).is_absolute():
            raise ValueError('ACP MCP command must be an absolute executable path.')
        if not isinstance(server.get('args',[]),list) or any(not isinstance(v,str) for v in server.get('args',[])):
            raise ValueError('MCP args must be strings.')
        if not isinstance(server.get('env',[]),list) or any(not isinstance(v,dict) or not isinstance(v.get('name'),str) or not isinstance(v.get('value'),str) for v in server.get('env',[])):
            raise ValueError('MCP environment entries need name and value strings.')
    return servers

async def install(coordinator,servers):
    from amplifier_module_tool_mcp.manager import MCPManager
    config={'servers':{r['name']:{'command':r['command'],'args':r.get('args',[]),
        'env':{v['name']:v['value'] for v in r.get('env',[])}} for r in validate_servers(servers)}}
    manager=MCPManager(config,coordinator)
    try:
        await manager.start()
        if set(manager.get_server_names())!=set(config['servers']):
            raise RuntimeError('One or more requested ACP MCP servers could not connect. No prompt was sent.')
        mounted=coordinator.get('tools') or {}
        for name,tool in manager.get_all_capabilities().items():
            if name in mounted:raise ValueError('Requested ACP MCP tool collides with a configured native tool: '+name)
            await coordinator.mount('tools',tool,name=name)
        from amplifier_module_tool_mcp.hooks import MCPVisibilityHook
        hook=MCPVisibilityHook(manager,{})
        coordinator.hooks.register(event='provider:request',handler=hook.on_provider_request,priority=hook.priority,name='acp-mcp-visibility')
        coordinator.register_cleanup(manager.stop)
    except BaseException:
        await manager.stop();raise
