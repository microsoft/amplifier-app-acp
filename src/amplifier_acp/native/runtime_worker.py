"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


import asyncio


import json


import os


from pathlib import Path


import sys


import time


import uuid


if not __package__:
    from runtime_bootstrap import bootstrap_app_package
    bootstrap_app_package()
    __package__='amplifier_acp.native'

try:
    from .runtime_protocol import MAX_MESSAGE_BYTES, encode_message
    from .message_delivery import contains_input
except ImportError:  # Executed directly inside the isolated runtime.
    from runtime_protocol import MAX_MESSAGE_BYTES, encode_message
    from message_delivery import contains_input


_PROTOCOL = None


from amplifier_acp import preparation_policy as runtime_preparation

def configure_protocol():
    global _PROTOCOL
    _PROTOCOL = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr


def publish(data):
    _PROTOCOL.write(encode_message(data).decode("utf-8"))


class Worker:
    def __init__(self):
        self.inspection_instance=str(uuid.uuid4());self.inspection_snapshot=None
        from .history_hints import set_observer
        set_observer(publish)
        self.session = self.runtime = self.execution = None
        from amplifier_acp.public_message import PendingOriginals
        self.public_originals = PendingOriginals()
        self.approvals = {}
        self.operation_ids = set()
        self.operation_controls = 0
        self.bridges = {}
        self.start_task = None
        self.tasks = set()
        self.shutdown = asyncio.Event()
        self.telemetry = None
        self.controls = None
        self.naming = None
        self.workspace = None
        self.home = None
        self.shared_store = None
        self.shared_handle = None
        self.maintenance_gate = None
        self.activation_gate = None
        self.activation = None
        self.parked = False
        self.retiring = False
        self.draining = False
        self.transport_closed = False
        self.cleaning = False
        self.cleanup_bridge_failed = False
        self.parked_history_stamp = None
        self.parked_config_stamp = None
        self.mounted_settings_stamp = {}
        self.config_inputs = ()
        self.command_lock = asyncio.Lock()
        self.peer_stop_epoch = 0
        self.memory_task = None
        self.start_config = None
        self.remounting = False
        self.context_bindings = {}
        self.context_inputs = []
        self.root_generation_outcome = None
        self.terminal_checkpointed = False
        self.pending_input_failure = None
        if __package__:
            from .ownership import WorkerOwnership
        else:
            from ownership import WorkerOwnership
        self.ownership = WorkerOwnership(self, lambda data: publish(data))

    async def ask(self, prompt, options):
        identity = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        options = options or ["allow", "deny"]
        self.approvals[identity] = (future, options)
        publish({"type": "approval.requested", "id": identity, "prompt": prompt, "options": options})
        try:
            decision = await future
            publish({"type": "approval.resolved", "id": identity, "decision": decision})
            return decision
        except asyncio.CancelledError:
            publish({"type": "approval.resolved", "id": identity, "decision": "expired"})
            raise
        finally:
            self.approvals.pop(identity, None)

    async def bridge(self, operation, args):
        cleanup = self.cleaning
        if self.transport_closed:
            if cleanup:self.cleanup_bridge_failed = True
            raise RuntimeError('Native host connection is closed; bridge work was not replayed.')
        identity = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.bridges[identity] = future
        try:
            publish({"op": "bridge", "id": identity, "operation": operation, "args": args})
            return await asyncio.wait_for(future, 60)
        except Exception:
            if cleanup:self.cleanup_bridge_failed = True
            raise
        finally:
            self.bridges.pop(identity, None)

    def app_access_bridge(self, coordinator):
        # Workers inherit the originating inputs at assignment, never
        # another client's later input or another coordinator's receipts.
        assigned = None if coordinator is self.session.coordinator else list(self.context_inputs)
        assigned_bindings = None if assigned is None else [{'inputId': i, 'clientId': self.context_bindings.get(i, {}).get('clientId')} for i in assigned]
        assigned_clients = None if assigned_bindings is None else [b['clientId'] for b in assigned_bindings]
        watched = set()
        async def bridge(operation, args):
            ids = self.context_inputs if assigned is None else assigned
            args = {**args, '_inputBindings': ([{'inputId': i, 'clientId': self.context_bindings.get(i, {}).get('clientId')} for i in ids] if assigned_bindings is None else assigned_bindings), '_inputClients': ([self.context_bindings.get(i, {}).get('clientId') for i in ids]
                                             if assigned_clients is None else assigned_clients)}
            if operation.startswith('context.'):
                bindings = [self.context_bindings[i] for i in ids if i in self.context_bindings]
                if assigned is not None:
                    if operation == 'context.read':
                        watched.add(args.get('surfaceId'))
                    bindings = [{**b, 'targets': [t for t in b.get('targets', []) if t['surfaceId'] in watched]} for b in bindings]
                    if not bindings:
                        bindings = [{'clientId': 'detached-worker', 'targets': []}]
                args = {**args, '_contextInputs': ids,
                        '_contextBindings': bindings}
            return await self.bridge(operation, args)
        return bridge

    def stage_original(self, input_id, original):
        if original is None:return
        from amplifier_acp.public_message import supports_context
        if not supports_context(self.session.coordinator.get('context')):
            publish({'type':'public.message.unavailable','input_id':input_id,
                     'reason':'shared-request-view-unavailable','originalAuthority':'host'})
            return
        self.public_originals.stage(input_id,original)

    def observe(self, event):
        """Publish lifecycle metadata, never provider reasoning or tool inputs."""
        event = dict(event)
        if event.get('type') in {'steering.held','steering.unknown'}:
            self.public_originals.discard(event.get('input_id'))
        root_session_id = self.runtime.session_id if self.runtime else None
        event_session_id = event.get('sessionId') or event.get('session_id') or root_session_id
        event_root_id = event.get('rootSessionId') or event.get('root_session_id') or root_session_id
        if event.get('type', '').startswith('generation.') and event_session_id:
            event['sessionId'] = event_session_id
            event['rootSessionId'] = event_root_id
        root_generation = event_session_id == root_session_id and event_root_id == root_session_id
        if root_generation and event.get('type') == 'generation.started':
            self.peer_terminal = None
            self.root_generation_outcome = None
            self.terminal_checkpointed = False
            self.pending_input_failure = None
            capability=self.session.coordinator.get_capability('live.steering')
            if isinstance(capability,dict):event['activeSteering']={key:capability[key] for key in ('version','mode','cancellable') if key in capability}
            if isinstance(capability,dict) and capability.get('version')==1 and capability.get('mode')=='request_boundary' and callable(self.session.coordinator.get_capability('live.attachments.encode')):
                event['activeSteering']['content']={'version':1,'attachments':True,'quotedReplies':True}
        elif root_generation and event.get('type') in {'generation.finished', 'generation.failed', 'generation.detached'}:
            self.root_generation_outcome = {
                'generation.finished': 'completed',
                'generation.failed': 'error',
                'generation.detached': 'interrupted',
            }[event['type']]
        if self.controls:
            from amplifier_acp.native.scheduled_input import finish as finish_scheduled_input
            finish_scheduled_input(self.controls, event)
            from amplifier_acp.native.observation_input import finish as finish_observation_input
            finish_observation_input(self.controls, event)
        if root_generation and event.get('type') == 'generation.started':
            self.context_inputs = []
        if root_generation and event.get('type') in {'input.delivered', 'steering.applied'} and event.get('input_id'):
            if event['input_id'] not in self.context_inputs:
                self.context_inputs = (self.context_inputs + [event['input_id']])[-8:]
        if self.naming:self.naming.observe(event)
        if self.telemetry:
            self.telemetry.lifecycle(event)
        loop = self.session.coordinator.get("orchestrator") if self.session else None
        if event.get("type", "").startswith("job.") and loop:
            job = loop.jobs.get(event.get("job_id"), {})
            arguments = (job.get("tool_call") or {}).get("arguments", {})
            if isinstance(arguments, dict) and isinstance(arguments.get("agent"), str):
                event["agent"] = arguments["agent"]
        if root_generation and event.get('type') == 'generation.finished':
            from .peer_results import terminal
            proof = terminal(self, event)
            if proof is not None:
                event['peerTerminal'] = proof
        if root_generation and event.get('type') == 'generation.failed':
            # loop-live emits this before final job joining/checkpoint. It is
            # not terminal proof until the owning execute task has settled.
            self.pending_input_failure = event
        else:
            publish(event)
        if event.get("type") == "generation.finished" and loop:
            count = sum(not job["task"].done() for job in loop.jobs.values())
            if count:
                publish({"type": "runtime.activity", "phase": "waiting-workers", "activeWorkers": count,
                    "detail": f"Waiting for {count} delegated task{'s' if count != 1 else ''} to report back."})
        elif event.get("type") == "input.delivered":
            publish({"type": "runtime.activity", "phase": "model", "detail": "Preparing a model response."})

    def install_activity(self, coordinator):
        from amplifier_core import HookResult
        if coordinator.get_capability("web.activity"):
            # Live provider edits may mount a new immutable instance after the
            # hooks were installed. Preserve its admission wrapper as well.
            if self.telemetry:
                providers = coordinator.get("providers") or {}
                for name, provider in list(providers.items()):
                    providers[name] = self.telemetry.instrument_provider(coordinator.session_id, provider)
            return
        coordinator.register_capability("web.activity", True)
        # A resumed child reuses its session ID but owns a new coordinator.
        # Bind hooks to their originating run, never the mutable registry row.
        activity_run_id = coordinator.get_capability("web.worker_run")
        async def observe_operation(event):
            identity = (coordinator.session_id, event.get("operationId"))
            if event.get("phase") == "started":
                self.operation_ids.add(identity)
            try:
                from amplifier_acp.native.computation import KERNEL_TRANSPORT
                if event.get("source") == "tool-bash" and KERNEL_TRANSPORT.get():
                    return {"capturedBy": "computation"}
                return await self.bridge("operations.observe", {
                    "runtimeSessionId": coordinator.session_id, "event": event})
            finally:
                if event.get("phase") == "finished":
                    self.operation_ids.discard(identity)
                    task = asyncio.create_task(self.park())
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
        if self.start_config.get("hostInfo",{}).get("features",{}).get("operations"):
            coordinator.register_capability("operations.observe", observe_operation)
        async def admit_questions(question_ids):
            return await self.bridge("questions.admit", {"questionIds": question_ids})
        if self.start_config.get("hostInfo",{}).get("features",{}).get("questions"):
            coordinator.register_capability("questions.admit", admit_questions)
        def public_stream():
            from amplifier_acp.native.execution_events import CALL_PURPOSE
            return not CALL_PURPOSE.get()
        coordinator.register_capability("live.public_stream", public_stream)
        if self.telemetry:
            coordinator.register_capability('web.provider_observe', lambda provider: self.telemetry.instrument_provider(coordinator.session_id,provider))
            coordinator.register_capability('web.provider_call', lambda provider, request, invoke, **kwargs: self.telemetry.provider_call(coordinator.session_id, provider, request, invoke, **kwargs))
            registry = coordinator.get_capability("live.children")
            if registry and coordinator.session_id in registry.rows:
                self.telemetry.lifecycle({"type":"child.updated", **registry.rows[coordinator.session_id]})
            providers = coordinator.get("providers") or {}
            for name, provider in list(providers.items()):
                providers[name] = self.telemetry.instrument_provider(coordinator.session_id, provider)
        async def activity(event, data):
            identity = coordinator.session_id
            if data.get("session_id", identity) != identity:
                return HookResult()
            if self.telemetry:
                self.telemetry.hook(identity, event, data)
            from amplifier_acp.native.execution_events import CALL_PURPOSE
            if CALL_PURPOSE.get() or event.startswith("llm:"):
                # Naming calls retain their measured usage, but do not own the
                # conversation's busy indicator or a delegated worker's state.
                return HookResult()
            phase = "model"
            retry = {}
            detail = "Waiting for the configured model to respond."
            if event == "context:compaction_started":
                phase, detail = "compacting", "Making room in the conversation. This can take a few minutes. You can keep sending updates."
                if data.get("recovery"):
                    detail = "Preparing recent context from saved history. This can take a few minutes. Older history remains available. You can keep sending updates."
            elif event == "context:compaction_progress":
                phase = "compacting"
                completed, remaining = data.get("completed_parts", 0), data.get("remaining_parts", 0)
                detail = (f"Recovering oversized history · {completed} of {completed + remaining} parts saved. This can take a few minutes."
                          if remaining else "Conversation context prepared; continuing work.")
            elif event == "context:compaction_finished":
                detail = "Conversation context prepared; continuing work." if data.get("outcome") == "completed" else "Context preparation " + str(data.get("outcome", "ended")) + "."
            elif event == "provider:retry":
                phase = "retrying"
                attempt, maximum = data.get("attempt"), data.get("max_retries")
                suffix = f" ({attempt} of {maximum})" if isinstance(attempt, int) and isinstance(maximum, int) else ""
                if suffix:
                    retry = {"retryAttempt": attempt, "retryMax": maximum}
                detail = "The model request was interrupted; retrying" + suffix + "."
            elif event.startswith("tool:"):
                phase = "tools"
                tool = data.get("tool_name")
                tool = tool[:100] if isinstance(tool, str) else "tool"
                detail = ("Running " if event == "tool:pre" else "Finished " if event == "tool:post" else "An error occurred in ") + tool + "."
            if identity == self.runtime.session_id:
                publish({"type": "runtime.activity", "phase": phase, "detail": detail, **retry})
            else:
                registry = coordinator.get_capability("live.children")
                row = registry.rows.get(identity, {}) if registry else {}
                publish({"type": "worker.activity", "workerId": identity, "phase": phase,
                    "detail": detail, "name": row.get("agent", "Worker"), "callId": row.get("callId"),
                    "runId": activity_run_id, "time": time.time(), **retry})
            return HookResult()
        for event in ("provider:request", "provider:retry", "tool:pre", "tool:post", "tool:error", "llm:request", "llm:response", "context:compaction_started", "context:compaction_progress", "context:compaction_finished"):
            coordinator.hooks.register(event, activity, name="amplifier-web-activity-" + event)

    async def preparation_progress(self, directory):
        while True:
            if (directory / "live-mount-plan.json").exists():
                publish({"type": "runtime.progress", "phase": "session-mount",
                    "detail": "Loading the configured providers, tools, and worker agents."})
                return
            await asyncio.sleep(2)

    async def start(self, config, *, raise_errors=False, recover_bundle=True, resolved_root=None):
        progress = None
        startup_capture = None
        try:
            publish({"type": "runtime.progress", "phase": "bundle-preparation",
                "detail": "Loading your bundle and app behaviors; downloading or installing modules as needed."})
            # Load only app code, never the outer host's site-packages metadata.
            if __package__:
                from .runtime_bootstrap import bootstrap_app_package
            else:
                from runtime_bootstrap import bootstrap_app_package
            bootstrap_app_package()
            from amplifier_acp.maintenance_gate import Gate
            if self.maintenance_gate is None:self.maintenance_gate=Gate()
            from amplifier_acp.native.host.config import app_home
            from amplifier_acp.native.worker_diagnostics import StartupCapture
            startup_capture = StartupCapture()
            from amplifier_acp.native.host.session import prepare_manager
            from amplifier_acp.native.execution_events import ExecutionEvents
            from amplifier_acp.native.runtime_controls import RuntimeControls
            from amplifier_acp.native.shared_state import ActivationGate, configuration_stamp
            from amplifier_module_loop_live.runtime import Runtime
            self.home = app_home()
            from amplifier_acp.native.runtime_qualification import active_install_overrides
            # App dependency constraints belong only to module installers. A
            # tool invoking uv must retain the caller's environment unchanged.
            qualified_overrides = install_overrides = None
            if not os.environ.get("UV_OVERRIDE"):
                qualified_overrides = active_install_overrides(self.home)
                install_overrides = qualified_overrides if qualified_overrides is not None else Path(__file__).with_name("runtime_deps") / "compatibility.txt"
            self.runtime = Runtime(session_id=config["id"], observer=self.observe, max_input_chars=200_000)
            self.telemetry = ExecutionEvents(config["id"], publish, self.home/'sessions'/config["id"]/'model-calls')
            workspace = Path(config.get("workspace") or config.get("workingDirectory") or os.getcwd()).expanduser().resolve(strict=True)
            from amplifier_acp.managed_session import runtime_scope
            managed_scope=runtime_scope(str(workspace),config["id"],{"managedSessionRoots":json.loads(os.environ.get("AMPLIFIER_ACP_MANAGED_ROOTS","[]"))})
            self.workspace = workspace
            self.start_config = dict(config)
            # Execution locking lives in the isolated runtime. The outer host
            # uses the same Foundation native-history reader for navigation.
            if self.shared_store is None:
                from amplifier_foundation.session.shared_state import SharedSessionStore, file_stamp
                self.shared_store = SharedSessionStore(workspace, config["id"])
                self.shared_store_stamp = file_stamp
                self.activation_gate = ActivationGate()
            if self.shared_handle is None:
                self.shared_handle = await asyncio.to_thread(
                    self.shared_store.acquire, app="amplifier-acp", pid=os.getpid())
            if recover_bundle:
                from amplifier_acp.native.bundle_selection import BundleTransaction
                BundleTransaction(self.home, workspace, config["id"]).restore()
            from amplifier_acp.native.history_revision import recover_pending
            recover_pending(self.home, workspace, config['id'])
            self.activation = self.activation_gate.activate()
            self.runtime.capture_activation = self.activation_gate.current
            # Always allow loading the saved transcript when one exists; the
            # adapter marks interrupted jobs as evidence, never replays them.
            report_directory = self.home / "runtime-reports" / config["id"]
            progress = asyncio.create_task(self.preparation_progress(report_directory))
            # Saved controls are the resume authority, including an explicit
            # reset. Only a user/agent selection action may replace that choice.
            selection = config.get('selection')
            controls_path = self.home / 'sessions' / config['id'] / 'control-state.json'
            if config.get('replaceSavedSelection') is not True and controls_path.exists():
                saved_controls = json.loads(controls_path.read_text())
                if 'selection' in saved_controls:
                    selection = saved_controls['selection']
            from amplifier_acp.native.shared_settings import settings_paths
            self.mounted_settings_stamp = {str(path): self.shared_store_stamp(path)
                for path in settings_paths(workspace, session_id=config['id']).values()}
            self.session, self.runtime, report = await prepare_manager(workspace,
                runtime=self.runtime, bundle=config.get("bundle") or None, ask=self.ask,
                resume=True, application_host="Amplifier ACP", selection=selection or None,
                report_dir=report_directory, shared_handle=self.shared_handle,
                shared_handle_getter=lambda: self.shared_handle,
                write_guard=self.activation_gate.check_current, resolved_root=resolved_root,
                execution_workspace=config.get("workingDirectory"), install_overrides=install_overrides,global_only=bool(managed_scope))
            if qualified_overrides is not None:
                active_install_overrides(self.home, str(qualified_overrides))
            self.module_source_overrides = report.get("moduleSourceOverrides", {})
            self.config_inputs = tuple(report.get("config_inputs", ()))
            if config.get("mcpServers"):
                from amplifier_acp.mcp import install
                await install(self.session.coordinator, config["mcpServers"])
            from amplifier_acp.native.attachments import encode
            self.session.coordinator.register_capability('live.attachments.encode',encode)
            self.controls = RuntimeControls(self.session, self.runtime, self.telemetry)
            self.controls.capacity.admit = (lambda row: self.bridge("capacity.admit", {"call": row, "budget": self.controls.capacity.snapshot()})) if config.get("hostInfo",{}).get("features",{}).get("capacity") else None
            self.telemetry.admission_guard = self.controls.capacity.guard
            # Preserve app controls on native mounts. A legacy common snapshot
            # retains its previous restoration policy during one-time recovery.
            if report.get("history_source") != "legacy-checkpoint":
                if config.get('replaceSavedSelection') is True:
                    await self.controls.restore(selection_override=config['selection'])
                else:
                    await self.controls.restore()
            self.start_config.pop('replaceSavedSelection', None)
            self.controls.persist()
            if config.get("forkContext") and not report.get("resumed"):
                # Fork conversational context without tool receipts or runtime
                # ownership. Source operations must never be replayed.
                from types import SimpleNamespace
                messages = [{"role": row["role"], "content": encode(SimpleNamespace(
                    text=row.get("text", ""), attachments=row["attachments"]))
                    if row["role"] == "user" and row.get("attachments") else row.get("text", "")}
                    for row in config.get("messages", []) if row.get("role") in {"user", "assistant"} and (row.get("text") or row.get("attachments"))]
                if messages:
                    await self.session.coordinator.get("context").set_messages(messages)
                report["fork_context_messages"] = len(messages)
            host = self
            from amplifier_acp.native.app_guidance import install_app_access
            if config.get("hostBridge") and config.get("hostInfo",{}).get("appControl"):
                await install_app_access(self.session.coordinator, self.app_access_bridge(self.session.coordinator), host_info=config.get("hostInfo",{}))
            original_host = self.session.coordinator.get_capability("live.host")
            class ObservedHost:
                def __getattr__(self, name):
                    return getattr(original_host, name)
                async def prepare_execution(self, loop, coordinator, providers):
                    result = await original_host.prepare_execution(loop, coordinator, providers)
                    if coordinator:
                        host.install_activity(coordinator)
                        if config.get("hostBridge") and config.get("hostInfo",{}).get("appControl"):
                            await install_app_access(coordinator, host.app_access_bridge(coordinator), host_info=config.get("hostInfo",{}))
                        from amplifier_acp.native.host.session import SelectedProvider
                        transform = coordinator.get_capability('web.provider_transform') or (lambda provider: provider)
                        if isinstance(getattr(loop,'root_provider',None), SelectedProvider):
                            loop.root_provider.execution_adapter = transform
                        runtime, selected, scope = result
                        return runtime, {name: transform(value) for name, value in selected.items()}, scope
                    return result
            # loop-live propagates this host through its existing ContextVar to
            # delegated sessions. Observe their public lifecycle without changing
            # spawning, provider routing, approvals, or execution ownership.
            self.session.coordinator.register_capability("live.host", ObservedHost())
            self.session.coordinator.register_capability("web.activity.install", self.install_activity)
            self.session.coordinator.register_capability("live.activation", self.activation_gate)
            self.session.coordinator.register_capability("live.park", self.park)
            from amplifier_acp.native.host.naming import LiveSessionNaming
            completed=[identity for event in config.get('generations',[]) if event.get('event')=='generation.finished' for identity in event.get('input_ids',[]) if identity in {t['id'] for t in config.get('execution',{}).get('turns',[])}]
            self.naming=LiveSessionNaming(self.session.coordinator,self.home,publish,completed)
            from .peer_results import install as install_peer_results
            install_peer_results(self)
            from amplifier_acp.native.input_provenance import Context as ProvenanceContext
            self.input_provenance_path=self.home/'sessions'/config['id']/'input-provenance.sqlite3'
            await self.session.coordinator.mount('context',ProvenanceContext(self.session.coordinator.get('context'),self.input_provenance_path,self.public_originals))
            from amplifier_acp.native.passive_feedback import install as install_feedback
            self.passive_feedback = install_feedback(self.controls, self.input_provenance_path.parent)
            if config.get('hostInfo', {}).get('features', {}).get('peerNotifications'):
                from amplifier_acp.native.passive_peer import install as install_peer
                self.passive_peer = install_peer(self.controls, self.bridge)
            await self.ownership.register()
            self.execution = asyncio.create_task(self.session.execute(""))
            self.execution.add_done_callback(self.executed)
            self.parked_history_stamp = self.history_stamp()
            self.parked_config_stamp = self.retain_mounted_settings(configuration_stamp(
                workspace, config["id"], self.home, self.shared_store_stamp,
                extra_paths=self.config_inputs))
            report["tools"] = list(self.session.coordinator.get("tools"))
            # Keep absolute module sources and full mount plans on disk. The UI
            # gets a compact capability report, not credentials or config blobs.
            public = {key: report.get(key) for key in ("bundle", "root_bundle", "workspace", "session_id", "resumed", "providers",
                "tools", "agents", "provider_choices", "selection", "effective_selection", "delegationRouting", "steering", "capabilities", "runtimePreparation", "fork_context_messages", "standalone", "settings_file")}
            from amplifier_acp.source_closure import ready_report
            source_ready = ready_report(report)
            if source_ready is not None: public["sourceClosure"] = source_ready
            from amplifier_acp.public_message import mounted_capability
            public["publicMessagePreservation"] = mounted_capability(self.session.coordinator.get("context"))
            publish({"type": "runtime.ready", "report": public})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            diagnostic = startup_capture.save(exc, config.get("id")) if startup_capture else None
            if raise_errors:
                if diagnostic:
                    exc.diagnostic_path = diagnostic
                raise
            error = {"type": "runtime.error", "error": f"{type(exc).__name__}: {exc}"}
            if diagnostic:
                error["diagnosticReceipt"] = diagnostic.name
            from amplifier_acp.native.module_failures import ConfiguredModuleError
            if isinstance(exc, ConfiguredModuleError):
                error.update(code="module_load_failed", moduleFailures=exc.failures)
            from .history_revision import ContextClearError
            if isinstance(exc, ContextClearError): error.update(exc.data)
            if type(exc).__name__ == "SessionBusyError":
                error.update(code="session_busy", owner=getattr(exc, "owner", None))
            publish(error)
            self.shutdown.set()
        finally:
            if startup_capture:
                startup_capture.close()
            if progress:
                progress.cancel()
                await asyncio.gather(progress, return_exceptions=True)

    def retain_mounted_settings(self, stamp):
        """Parking cannot claim a concurrently saved policy was already mounted."""
        return tuple((path, self.mounted_settings_stamp.get(path, value))
                     for path, value in stamp)

    async def park(self, *, activation=None):
        """Checkpoint then relinquish only a settled manager activation."""

        # Naming may itself await an approval/bridge response. Do not hold the
        # command lock while waiting for it; admission is rechecked below.
        if self.naming and self.naming.pending and not self.naming.pending.done():
            await self.naming.pending
        async with self.command_lock:
            if self.ownership.yielding or self.parked or self.shared_handle is None:
                return
            loop = self.session.coordinator.get("orchestrator")
            if (self.approvals or self.bridges or self.operation_ids or self.operation_controls or self.runtime.queued_inputs
                    or not self.runtime.inbox.empty() or self.runtime.generation
                    or (loop and (loop.pending or loop._active_jobs()))):
                return
            activation = activation or self.activation
            self.activation_gate.check(activation)
            token = self.activation_gate.bind(activation)
            try:
                checkpoint = self.session.coordinator.get_capability("live.checkpoint")
                if checkpoint:
                    await checkpoint(self.root_generation_outcome or "completed")
                self.parked_history_stamp = self.history_stamp()
                from amplifier_acp.native.shared_state import configuration_stamp
                self.parked_config_stamp = self.retain_mounted_settings(configuration_stamp(
                    self.workspace, self.runtime.session_id, self.home, self.shared_store_stamp,
                    extra_paths=self.config_inputs))
                self.activation_gate.release(activation)
                await asyncio.to_thread(self.shared_handle.release)
                self.shared_handle = None
                self.parked = True
            finally:
                self.activation_gate.reset(token)
        publish({"type": "runtime.parked", "session_id": self.runtime.session_id})

    async def checkpoint_terminal_outcome(self):
        """Persist only an observed failed/detached root turn before teardown."""
        outcome = self.root_generation_outcome
        if (self.terminal_checkpointed or outcome not in {"error", "interrupted"}
                or self.ownership.yielding or self.parked
                or self.shared_handle is None or self.session is None):
            return
        async with self.command_lock:
            outcome = self.root_generation_outcome
            if (self.terminal_checkpointed or outcome not in {"error", "interrupted"}
                    or self.ownership.yielding or self.parked
                    or self.shared_handle is None or self.session is None):
                return
            checkpoint = self.session.coordinator.get_capability("live.checkpoint")
            if checkpoint is None:
                return
            try:
                token = self.activation_gate.bind(self.activation)
            except RuntimeError:
                # Ownership release/cancellation may already have invalidated the
                # write token; its dedicated release flow owns that checkpoint.
                return
            try:
                await checkpoint(outcome)
                self.terminal_checkpointed = True
                if (self.pending_input_failure is not None and self.execution is not None
                        and self.execution.done() and not self.execution.cancelled()):
                    from amplifier_acp.input_outcome import Outcomes
                    messages = await self.session.coordinator.get('context').get_messages()
                    ids = self.pending_input_failure.get('input_ids', [])
                    # A bounded generation list comes from the native runtime,
                    # never a caller-supplied proof. No message text is retained.
                    if not isinstance(ids, list) or len(ids) > 200:
                        return
                    owner = Outcomes(self.home, self.runtime.session_id, str(self.workspace))
                    proofs = {}
                    for identity in ids:
                        if isinstance(identity, str) and contains_input(messages, identity):
                            proofs[identity] = owner.failed(identity, self.pending_input_failure)
                    self.pending_input_failure['_input_outcomes'] = proofs
            finally:
                self.activation_gate.reset(token)

    def history_stamp(self):
        """Cheap native invalidation; never parse events or consult checkpoints."""
        from amplifier_acp.native.session_files import sessions_dir
        directory = sessions_dir(self.workspace) / self.runtime.session_id
        return tuple(self.shared_store_stamp(directory / name) for name in
                     ("transcript.jsonl", "transcript.jsonl.backup", "metadata.json", "metadata.json.backup"))

    async def acquire_for_mutation(self):
        """Acquire after a parked loop wakes, before it can accept an input."""

        if not self.parked:
            return
        handle = await asyncio.to_thread(
            self.shared_store.acquire, app="amplifier-acp", pid=os.getpid())
        try:
            history_stamp = self.history_stamp()
            from amplifier_acp.native.shared_state import configuration_stamp
            config_stamp = configuration_stamp(
                self.workspace, self.runtime.session_id, self.home, self.shared_store_stamp,
                extra_paths=self.config_inputs)
        except BaseException:
            await asyncio.to_thread(handle.release)
            raise
        if (self.parked_history_stamp and any(self.parked_history_stamp[:2])
                and not any(history_stamp[:2])):
            await asyncio.to_thread(handle.release)
            raise RuntimeError(
                "The native session transcript and its backup disappeared while this runtime was "
                "parked. Its previous context will not be replaced with legacy history.")
        if history_stamp != self.parked_history_stamp or config_stamp != self.parked_config_stamp:
            self.shared_handle = handle
            self.parked = False
            await self.remount()
            return
        self.shared_handle = handle
        self.activation = self.activation_gate.activate()
        self.parked = False
        await self.ownership.register()
        publish({"type": "runtime.reactivated", "session_id": self.runtime.session_id,
                 "reused": True})

    async def remount(self):
        """Replace stale mounted state after lock+stamp validation."""

        self.remounting = True
        try:
            if self.execution and not self.execution.done():
                self.execution.cancel()
                await asyncio.gather(self.execution, return_exceptions=True)
            if self.controls:
                await self.controls.close()
            if self.session:
                await self.session.cleanup()
            self.session = self.controls = self.naming = self.execution = None
            try:
                await self.start(self.start_config, raise_errors=True)
            except Exception as exc:
                from amplifier_acp.native.runtime import RuntimeStartupError
                failure = RuntimeStartupError('The conversation worker could not reload before accepting the message.')
                from amplifier_acp.native.host.model_selection import ProviderSelectionError
                if isinstance(exc, ProviderSelectionError):
                    from amplifier_acp.native.session_health import failure_details
                    detail = failure_details(exc)
                    failure.args = (f"ProviderSelectionError: {detail['summary']} {detail['guidance']}",)
                failure.diagnostic_path = getattr(exc, 'diagnostic_path', None)
                failure.failures = getattr(exc, 'failures', [])
                raise failure from exc
        finally:
            self.remounting = False

    def bind_activation(self):
        """Bind the current capability to a command task, never to a global writer."""

        if self.parked or self.shared_handle is None:
            raise RuntimeError("The session is parked and has no active shared writer.")
        return self.activation_gate.bind(self.activation)

    def executed(self, task):
        if self.remounting or self.ownership.yielding:
            return
        completion = asyncio.create_task(self.finish_execution(task))
        self.tasks.add(completion)
        completion.add_done_callback(self.tasks.discard)

    async def finish_execution(self, task):
        """Join execution, checkpoint, and commit exact proof before public error."""
        try:
            if not task.cancelled():
                error = task.exception()
                if self.pending_input_failure is not None:
                    try:
                        await self.checkpoint_terminal_outcome()
                    except Exception as exc:
                        # A failed checkpoint/receipt write never becomes proof.
                        publish({'type': 'persistence.failed', 'error_type': type(exc).__name__})
                    event, self.pending_input_failure = self.pending_input_failure, None
                    publish(event)
                if error:
                    publish({'type': 'runtime.error', 'error': 'Native execution stopped after a failure; work was not replayed.'})
        finally:
            self.shutdown.set()

    async def command(self, data):
        """Serialize admission with parking and bind a per-work write token."""

        op = data.get("op")
        if op == 'stop':
            self.peer_stop_epoch += 1
        if self.retiring or self.draining:
            # Only an exact live reply settles work already owned by cleanup.
            # This grants no command admission, activation or Host mutation.
            future = self.bridges.get(data.get("id")) if op == "bridge.result" else None
            if future is not None and not future.done():
                await self._command_serial(data)
            else:
                publish({"op":"reply","id":data.get("id"),"error":"Native worker is shutting down; no new work admitted","code":"worker_retired","executed":False})
            return
        memory_control = op == 'control' and data.get('operation') == 'memory.consolidate'
        naming_control = op == 'control' and data.get('operation') in {'session.naming','session.naming.generate'}
        if op in {'send', 'retry', 'stop', 'resume', 'worker.message', 'worker.steer', 'worker.stop'}:
            # Auxiliary personalization must never delay foreground admission.
            # Cancellation cannot retract an already accepted provider request;
            # its source attempt is recorded interrupted and is not replayed.
            if self.memory_task and not self.memory_task.done():
                self.memory_task.cancel()
        if op == "park":
            await self.park()
            publish({"op": "reply", "id": data.get("id"), "result": {"parked": self.parked}})
            return
        if op == "retire":
            async with self.command_lock:
                loop = self.session.coordinator.get("orchestrator") if self.session else None
                settled = bool(self.parked and not self.ownership.yielding
                    and not self.approvals and not self.bridges and not self.operation_ids and not self.operation_controls and not self.remounting
                    and not self.runtime.queued_inputs and self.runtime.inbox.empty()
                    and not self.runtime.generation
                    and not any(not row["task"].done() for row in self.controls.logins.values())
                    and not (self.memory_task and not self.memory_task.done())
                    and not (self.naming and self.naming.pending and not self.naming.pending.done())
                    and not (loop and (loop.pending or loop._active_jobs())))
                publish({"op": "reply", "id": data.get("id"), "result": {"retired": settled}})
                if settled:
                    self.retiring=True
                    self.shutdown.set()
            return
        # A correlated approval settles already-admitted work under its original
        # activation. That work can hold command_lock while awaiting this reply.
        # Like bridge.result, resolving its exact live future must not acquire
        # a second admission token or wait behind the operation it unblocks.
        if op not in {"send", "retry", "steer", "resume", "control", "worker.steer", "worker.stop", "worker.message"}:
            await self._command_serial(data)
            return
        try:
            async with self.command_lock:
                if self.retiring:
                    publish({"op":"reply","id":data.get("id"),"error":"Native retirement already committed; no new work admitted","code":"worker_retired","executed":False})
                    return
                if self.ownership.yielding:
                    from amplifier_foundation.session import SessionBusyError
                    raise SessionBusyError(self.shared_handle.owner if self.shared_handle else None)
                # A memory call can finish admission while a foreground command
                # waits for this lock, so also cancel after acquiring it.
                if op in {'send', 'retry', 'resume', 'worker.message', 'worker.steer', 'worker.stop'} and self.memory_task and not self.memory_task.done():
                    self.memory_task.cancel()
                from .history_revision import guard_context_clear
                guard_context_clear(self.home, self.session.session_id)
                await self.acquire_for_mutation()
                token = self.bind_activation()
                detached_cancel = memory_control or naming_control or op == "control" and (data.get("operation", "").startswith(("operations.", "kernels.")))
                if detached_cancel:
                    self.operation_controls += 1
                    if memory_control:
                        self.memory_task = asyncio.current_task()
                else:
                    try:
                        await self._command_serial(data)
                    finally:
                        self.activation_gate.reset(token)
            if detached_cancel:
                try:
                    await self._command_serial(data)
                finally:
                    self.operation_controls -= 1
                    self.activation_gate.reset(token)
                    if memory_control and self.memory_task is asyncio.current_task():
                        self.memory_task = None
            if op in {"control", "retry"}:
                # A control-only action or duplicate retry does not wake the inbox.
                # It must therefore schedule its own settled release.
                await self.park(activation=self.activation)
        except asyncio.CancelledError:
            if not memory_control:
                raise
            publish({'op':'reply', 'id':data.get('id'), 'result':{'interrupted':True}})
        except Exception as exc:
            reply = {"op": "reply", "id": data.get("id"),
                     "error": f"{type(exc).__name__}: {exc}"}
            from amplifier_acp.native.runtime import RuntimeStartupError
            from .history_revision import ContextClearError
            if isinstance(exc, ContextClearError): reply.update(exc.data)
            preparation_failed = isinstance(exc, RuntimeStartupError)
            if preparation_failed:
                from amplifier_acp.native.worker_diagnostics import diagnostic_reference
                reply.update(code='worker_preparation_failed',
                    moduleFailures=getattr(exc, 'failures', []), **diagnostic_reference(exc))
            if type(exc).__name__ == "SessionBusyError":
                reply["code"] = "session_busy"
                reply["owner"] = getattr(exc, "owner", None)
            publish(reply)
            if preparation_failed:
                self.shutdown.set()
        finally:
            if memory_control and self.memory_task is asyncio.current_task():
                self.memory_task = None

    async def _command_serial(self, data):
        from amplifier_acp.native.input_provenance import reserved as input_reserved
        identity = data.get("id")
        try:
            op = data.get("op")
            if op == "bridge.result":
                future = self.bridges.get(identity)
                if future and not future.done():
                    if data.get("error"):
                        future.set_exception(RuntimeError(data["error"]))
                    else:
                        future.set_result(data.get("result"))
                return
            if op == "resume":
                if not self.session or not self.execution or self.execution.done():
                    raise RuntimeError('The session has not finished loading successfully.')
                result = {"accepted": True}
            elif op == "approval":
                future, options = self.approvals[data["approval_id"]]
                if future.done() or data["decision"] not in options:
                    raise ValueError("Approval expired or decision is not offered")
                future.set_result(data["decision"])
                result = {"accepted": True}
            elif op == "start":
                if self.start_task:
                    raise RuntimeError("Already started")
                self.start_task = asyncio.create_task(self.start(data["session"]))
                return
            elif op == "stop":
                if self.runtime and not self.runtime.closed:
                    from amplifier_module_loop_live.runtime import Input
                    await self.runtime.submit(Input("stop"))
                else:
                    self.shutdown.set()
                return
            elif op == "runtime.inspect":
                if not self.session or not self.execution:
                    raise ValueError('Resident runtime inspection requires the original ready worker')
                from amplifier_acp.runtime_inspection import mounted,interpreter,page,validate_args
                args=data.get('arguments',{})
                validate_args(args)
                if args.get('cursor') and (args.get('refresh') or self.inspection_snapshot is None):
                    raise ValueError('Start a new runtime inspection before paging')
                if self.inspection_snapshot is None or args.get('refresh'):
                    mounts=mounted(self.session.coordinator)
                    self.inspection_snapshot=await asyncio.to_thread(interpreter,mounts,self.inspection_instance,getattr(self,"module_source_overrides",{}))
                result=page(self.inspection_snapshot,args)
            elif op == "dependencies":
                if __package__:
                    from .artifact_runtime import discover
                else:
                    from artifact_runtime import discover
                result = await discover('worker', **({'verify': True} if data.get('verifyImports') is True else {}))
            elif op == "desktop.readiness":
                if not self.controls:
                    result = {"status": "unavailable", "reason": "Conversation tools are not mounted."}
                else:
                    from amplifier_acp.native.desktop_readiness import worker_report
                    result = worker_report(self.controls)
            elif not self.session or not self.execution:
                raise RuntimeError("Session is not ready")
            elif op in {"delivery", "retry"} and (
                input_reserved(self.input_provenance_path,data['input_id']) or
                data['input_id'] in self.runtime.accepted or
                contains_input(
                    await self.session.coordinator.get('context').get_messages(), data['input_id'])
            ):
                result = {"accepted": True, "delivery": "accepted", "duplicate": True}
            elif op == "delivery":
                result = {"delivery": "unknown"}
            elif op == "steer":
                from amplifier_module_loop_live.runtime import Input
                await self.runtime.submit(Input("steer", data["text"], id=data["input_id"], activation=self.activation))
                result = {"accepted": True, "inputId": data["input_id"]}
            elif op in {"send", "retry"}:
                from amplifier_module_loop_live.runtime import Input
                from amplifier_acp.native.message_interactions import prepare_input
                from amplifier_acp.public_message import input_original
                original=input_original(data)
                text = await prepare_input(self.session.coordinator, data['text'], data.get('reply_context'), max_chars=self.runtime.max_input_chars)
                self.context_bindings[data['input_id']] = data.get('context_binding', {'clientId': None, 'targets': []})
                self.context_bindings = dict(list(self.context_bindings.items())[-64:])
                provenance = data.get('input_provenance')
                if provenance is not None:
                    from amplifier_acp.native.input_provenance import validate_prompt
                    validate_prompt(provenance)
                    from amplifier_acp.native.input_provenance import reserve
                    reserve(self.input_provenance_path, data['input_id'], provenance)
                self.stage_original(data['input_id'],original)
                try:
                    input_id = await self.runtime.submit(Input(
                        "user", text, id=data["input_id"],
                        attachments=tuple(data.get("attachments", [])),
                        activation=self.activation))
                except BaseException:
                    self.public_originals.discard(data['input_id'])
                    raise
                result = {"accepted": True, "inputId": input_id}
            elif op == "control":
                arguments = data.get("arguments", {})
                if data["operation"] == "schedule.submit":
                    from amplifier_acp.native.scheduled_input import admit
                    result = await admit(self.controls, self.runtime, arguments, self.activation)
                elif data["operation"] == "steer.submit":
                    if 'public_message' in arguments:
                        from amplifier_acp.public_message import validate as validate_public
                        validate_public(arguments['public_message'],arguments.get('inputId'))
                    capability=self.session.coordinator.get_capability('live.steering')
                    generation=self.runtime.generation
                    if not isinstance(capability,dict) or capability.get('version')!=1 or capability.get('mode')!='request_boundary' or not callable(capability.get('submit')):
                        result={'accepted':False,'executed':False,'reason':'This mounted runtime has no anchored request-boundary steering capability.'}
                    elif not generation or generation['id']!=arguments.get('generationId') or generation.get('initial_input_id')!=arguments.get('activeInputId'):
                        result={'accepted':False,'executed':False,'reason':'The requested generation ended before steering admission.'}
                    elif arguments.get('contentVersion')==1 and not callable(self.session.coordinator.get_capability('live.attachments.encode')):
                        result={'accepted':False,'executed':False,'reason':'This mounted runtime has no steering attachment encoder.'}
                    else:
                        from amplifier_acp.native.input_provenance import reserve
                        from amplifier_module_loop_live.runtime import Input
                        reserve(self.input_provenance_path,arguments['inputId'],{'inputOrigin':'steering','activeInputId':arguments['activeInputId'],**({'voiceDelivery':arguments['voiceDelivery']} if arguments.get('voiceDelivery') else {}),**({'steeringProvenance':arguments['provenance']} if arguments.get('provenance') else {})})
                        self.stage_original(arguments['inputId'],arguments.get('public_message'))
                        try:
                            text=arguments['text']
                            if arguments.get('contentVersion')==1:
                                from amplifier_acp.native.message_interactions import prepare_input
                                text=await prepare_input(self.session.coordinator,text,arguments.get('reply_context'),max_chars=self.runtime.max_input_chars)
                            result=await capability['submit'](Input('steer',text,id=arguments['inputId'],attachments=tuple(arguments.get('attachments',[])),activation=self.activation),arguments['generationId'])
                        except ValueError as error:
                            self.public_originals.discard(arguments['inputId'])
                            result={'accepted':False,'executed':False,'reason':str(error)}
                        except BaseException:
                            self.public_originals.discard(arguments['inputId'])
                            raise
                        if not result.get('accepted'):self.public_originals.discard(arguments['inputId'])
                elif data["operation"] == "question.submit":
                    from amplifier_acp.native.question_input import admit
                    authority=(lambda value:self.bridge("questions.delivery.admit",value)) if self.start_config.get("hostInfo",{}).get("features",{}).get("questions") else None
                    result=await admit(self.controls,self.runtime,arguments,self.activation,authorize=authority,provenance_path=self.input_provenance_path)
                elif data["operation"] in {"peer.submit", "peer.steer"}:
                    from amplifier_acp.native.peer_input import admit
                    result = await admit(self.controls, self.runtime, arguments, self.activation,
                        authorize=lambda value: self.bridge('coordination.delivery.admit', value),
                        stop_epoch=lambda: (self.peer_stop_epoch, self.retiring, self.draining, self.transport_closed, self.ownership.yielding),
                        provenance_path=self.input_provenance_path, originals=self.public_originals, steering=data['operation'] == 'peer.steer')
                elif data["operation"] == "observation.submit":
                    from amplifier_acp.native.observation_input import admit
                    result = await admit(self.controls, self.runtime, arguments, self.activation,
                        authorize=lambda value: self.bridge("observation.admit", value),provenance_path=self.input_provenance_path)
                elif data["operation"] == "voice.delivery.record":
                    from amplifier_acp.native.voice_history import bind_delivery
                    result = await bind_delivery(self.controls, arguments)
                elif data["operation"] == "voice.transcript.record":
                    from amplifier_acp.native.voice_history import record
                    result = await record(self.controls, arguments)
                elif data["operation"] == "session.naming.generate":
                    from amplifier_acp.naming_commands import Commands,validate
                    validate('generate',arguments)
                    if not self.naming:raise ValueError('Automatic naming is unavailable for this conversation.')
                    commands=Commands(self.home,self.runtime.session_id,self.naming.directory)
                    result=await commands.perform('generate',arguments,self.naming.suggest)
                elif data["operation"] == "session.naming":
                    if not self.naming:
                        raise ValueError('Automatic naming is unavailable for this conversation.')
                    result = await self.naming.suggest()
                elif data["operation"] == "bundle.preview":
                    from amplifier_acp.native.bundle_selection import preview
                    self.controls.require_idle()
                    self.bundle_preview = {**await asyncio.wait_for(preview(self.controls, self.workspace, arguments['bundle']), 150),
                                           'previewId': str(uuid.uuid4())}
                    result = self.bundle_preview
                elif data["operation"] == "history.revise":
                    from amplifier_acp.history_edit import revise
                    result=await revise(self,arguments,publish)
                elif data["operation"] == "history.edit":
                    if self.approvals or self.bridges or not self.runtime.inbox.empty():
                        raise ValueError('Finish pending interactions before editing history.')
                    from amplifier_acp.native.history_revision import rewind
                    from amplifier_acp.native.message_interactions import prepare_input
                    text = await prepare_input(self.session.coordinator, arguments['text'], arguments.get('reply_context'), max_chars=self.runtime.max_input_chars)
                    result = await rewind(self.controls, arguments)
                    publish({'type': 'history.revised', **result})
                    from amplifier_module_loop_live.runtime import Input
                    await self.runtime.submit(Input('user', text, id=arguments['operationId'],
                        attachments=tuple(arguments.get('attachments', [])), activation=self.activation))
                elif data["operation"] == "bundle.switch":
                    from amplifier_acp.native.bundle_selection import switch
                    result = await switch(self, arguments)
                else:
                    result = await self.controls.perform(data["operation"], arguments)
            elif op in {"worker.steer", "worker.stop", "worker.message"}:
                children = self.session.coordinator.get_capability("live.children")
                wid = data["worker_id"]
                if data.get('expected_run_id') is not None and (not children or wid not in children.rows or children.rows[wid].get('runId')!=data['expected_run_id']):
                    result={'accepted':False,'executed':False,'reason':'Original worker lifetime changed before admission'}
                elif children and wid in children.rows:
                    result = await children.control(wid, "steer" if op == "worker.steer" else "message" if op == "worker.message" else "cancel", data.get("text", ""), input_id=data.get("input_id"))
                elif op == "worker.stop":
                    from amplifier_module_loop_live.runtime import Input
                    loop = self.session.coordinator.get("orchestrator")
                    job = loop.jobs.get(wid)
                    if job is None and children and wid in children.rows:
                        call = children.rows[wid].get("callId")
                        wid = next((k for k,v in loop.jobs.items() if v.get("call_id") == call), None)
                        job = loop.jobs.get(wid)
                    if not job or job["task"].done():
                        raise ValueError("Worker is no longer active")
                    await self.runtime.submit(Input("cancel_job", target=wid, activation=self.activation))
                    result = {"accepted": True, "completed": False}
                else:
                    raise ValueError("This finite worker cannot receive messages; ask the main session to revise its work")
            else:
                raise ValueError("Unknown runtime operation")
            if identity:
                publish({"op": "reply", "id": identity, "result": result})
        except Exception as exc:
            from .runtime_controls import ConfigurationConflict
            from .task_continuity import TaskRefusal
            from ..bundle_commands import BundlePreflightRefusal, BundleActivationFailure
            from .history_revision import ContextClearError
            from .host.model_selection import ProviderSelectionRefusal
            detail=exc.configuration_conflict if isinstance(exc,ConfigurationConflict) else exc.data if isinstance(exc,(TaskRefusal,BundlePreflightRefusal,BundleActivationFailure,ContextClearError,ProviderSelectionRefusal)) else {}
            publish({"op": "reply", "id": identity, "error": f"{type(exc).__name__}: {exc}",**detail})

    async def read(self):
        reader = asyncio.StreamReader(limit=MAX_MESSAGE_BYTES)
        protocol = asyncio.StreamReaderProtocol(reader)
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin)
        try:
            while line := await reader.readline():
                try:
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        continue
                except ValueError:
                    continue
                if data.get("op") == "bridge.result":
                    # Settle a reply before a following buffered EOF can fail it.
                    await self.command(data)
                    continue
                task = asyncio.create_task(self.command(data))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            publish({'type':'runtime.error','error':f'Amplifier worker communication failed ({type(exc).__name__}). A message could not be read; work was not replayed.'})
        finally:
            transport.close()
            self.transport_closed = True
            self.shutdown.set()
            for future in list(self.bridges.values()):
                if not future.done():
                    future.set_exception(RuntimeError('Native host connection closed before bridge settlement; work was not replayed.'))

    @runtime_preparation.governed
    async def run(self):
        read = asyncio.create_task(self.read())
        try:
            await self.shutdown.wait()
        finally:
            self.draining = True
            for task in [self.start_task, self.execution, *self.tasks]:
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*(t for t in [self.start_task, self.execution, *self.tasks] if t), return_exceptions=True)
            self.cleaning = True
            try:
                try:
                    await self.checkpoint_terminal_outcome()
                finally:
                    if self.ownership.registration:
                        await self.ownership.registration.close()
                    if self.controls:
                        from amplifier_acp.native.observation_input import finish
                        finish(self.controls, {'type': 'generation.detached'})
                        await self.controls.close()
                    if self.session:
                        await self.session.cleanup()
                    if self.cleanup_bridge_failed:
                        raise RuntimeError('Native cleanup bridge did not settle successfully; retirement is unconfirmed.')
                    if self.shared_handle is not None:
                        try:
                            await asyncio.to_thread(self.shared_handle.release)
                        finally:
                            self.shared_handle = None
            finally:
                self.cleaning = False
                for task in [read, *self.tasks]:
                    if task and not task.done():task.cancel()
                await asyncio.gather(read, *self.tasks, return_exceptions=True)
                if self.maintenance_gate is not None:
                    self.maintenance_gate.release();self.maintenance_gate=None


if __name__ == "__main__":
    configure_protocol()
    asyncio.run(Worker().run())
