"""Credential-free update receipts; never retain subprocess text or arguments."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid

PROBE_PREFIX = 'AMPLIFIER_UPDATE_PROBE='
ERROR_TYPES = {'Exception','CommandFailure','CommandTimeout','AssertionError','ImportError','ModuleNotFoundError','FileNotFoundError','PermissionError',
               'OSError','RuntimeError','ValueError','TimeoutError','CancelledError','ModuleActivationError',
               'BundleNotFoundError','BundleLoadError','BundleValidationError','BundleDependencyError','HostComponentConflict','ConfiguredModuleError','AmbiguousBundleReferenceError'}
PREPARATION_REASONS = {'ModuleActivationError':'module-prepare-failed','BundleNotFoundError':'bundle-not-found',
                       'BundleLoadError':'bundle-load-failed','BundleValidationError':'bundle-validation-failed',
                       'BundleDependencyError':'bundle-dependency-failed', 'HostComponentConflict':'host-component-conflict','ConfiguredModuleError':'configured-module-failed'}
RECOVERY_REASONS = {'protected-runtime-source', 'runtime-source-changed', *PREPARATION_REASONS.values()}
PROBE_STAGES = {'imports','package','assets','login','terminal','complete','prepare','prepared','capabilities','cleanup'}


def exception_type(error):
    name=type(error).__name__
    return name if name in ERROR_TYPES else 'Exception'


def probe_failure(error, stage):
    facts={'ok':False,'errorType':exception_type(error)}
    reason=PREPARATION_REASONS.get(type(error).__name__) if stage=='prepare' else None
    if reason:facts['reason']=reason
    if type(error).__name__=='ConfiguredModuleError':
        from .module_failures import safe_failures
        failures=safe_failures(getattr(error,'failures',[]))
        facts.update(moduleFailures=failures[:8],moduleFailuresTruncated=len(failures)>8)
    return facts


def probe_record(output):
    if isinstance(output,bytes):output=output.decode(errors='replace')
    records=[]
    for line in output.splitlines():
        if not line.startswith(PROBE_PREFIX) or len(line)>8192:continue
        try:value=json.loads(line[len(PROBE_PREFIX):])
        except ValueError:continue
        if not isinstance(value,dict):continue
        safe={key:value[key] for key in ('ok','isolated','packageInEnvironment','frontendPresent','loginAvailable','standalone','providersPresent','cliAbsent','dependenciesPrepared') if type(value.get(key)) is bool}
        if isinstance(value.get('stage'),str) and value['stage'] in PROBE_STAGES:safe['stage']=value['stage']
        if isinstance(value.get('errorType'),str) and value['errorType'] in ERROR_TYPES:safe['errorType']=value['errorType']
        if isinstance(value.get('reason'),str) and value['reason'] in RECOVERY_REASONS:safe['reason']=value['reason']
        if 'moduleFailures' in value:
            from .module_failures import safe_failures
            safe['moduleFailures']=safe_failures(value['moduleFailures'])[:8]
            safe['moduleFailuresTruncated']=value.get('moduleFailuresTruncated') is True or isinstance(value['moduleFailures'],list) and len(value['moduleFailures'])>8
        for key in ('version','pythonVersion'):
            if isinstance(value.get(key),str) and re.fullmatch(r'\d+\.\d+\.\d+',value[key]):safe[key]=value[key]
        for key in ('elapsedMs', 'sources', 'packages', 'resolverTransactions'):
            if type(value.get(key)) is int and value[key]>=0:safe[key]=value[key]
        if 'ok' in safe:records.append(safe)
    return records[-1] if records else None


def command_facts(stdout,stderr,returncode,duration):
    facts={'exitCode':returncode,'durationMs':round(duration*1000),'stdoutBytes':len(stdout),'stderrBytes':len(stderr)}
    probe=probe_record(stdout)
    if probe:facts['probe']=probe
    # Only a fixed exception class, never the following message or traceback.
    classes=re.findall(rb'^([A-Za-z]+Error):',stderr,re.MULTILINE)
    if classes:
        name=classes[-1].decode('ascii')
        if name in ERROR_TYPES:facts['errorType']=name
    return facts


class CommandOutput(str):
    def __new__(cls,value,facts=None):
        result=super().__new__(cls,value);result.diagnostic_facts=facts or {};return result
    def __deepcopy__(self,memo):return str(self)


class CommandFailure(RuntimeError):
    def __init__(self,facts):
        super().__init__('Update command failed; see its sanitized diagnostic receipt')
        self.diagnostic_facts=facts


class CommandTimeout(TimeoutError):
    def __init__(self,duration):
        super().__init__('Update command exceeded its time limit')
        self.diagnostic_facts={'durationMs':round(duration*1000),'errorType':'TimeoutError','timedOut':True}
