"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


import copy


import asyncio


import hashlib


import inspect


import json


import math


import time


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def policy_snapshot(value=None, denial=None):
    """Bounded policy shared by active controls and passive history inspection."""
    defaults = {'revision': 0, 'enabled': False, 'maxTotalTokens': None, 'maxCostUsd': None,
                'warningFraction': .8, 'unknownPolicy': 'pause', 'includeEstimatedCost': False}
    if value is not None and not isinstance(value, dict):
        raise ValueError('Saved budget policy is invalid')
    policy = {key: (value or {}).get(key, default) for key, default in defaults.items()}
    if type(policy['revision']) is not int or policy['revision'] < 0:
        raise ValueError('Saved budget revision is invalid')
    if any(type(policy[key]) is not bool for key in ('enabled', 'includeEstimatedCost')):
        raise ValueError('Saved budget switches must be booleans')
    for key in ('maxTotalTokens', 'maxCostUsd'):
        v = policy[key]
        if v is not None and (not number(v) or v <= 0 or key == 'maxTotalTokens' and (type(v) is not int or v > 9007199254740991)):
            raise ValueError('Saved budget limits are invalid')
    if not number(policy['warningFraction']) or not 0 < policy['warningFraction'] <= 1 or policy['unknownPolicy'] not in ('pause', 'allow'):
        raise ValueError('Saved budget threshold or unknown-usage policy is invalid')
    if policy['enabled'] and policy['maxTotalTokens'] is None and policy['maxCostUsd'] is None:
        raise ValueError('An enabled budget needs at least one limit')
    if denial is not None:
        if not isinstance(denial, dict) or denial.get('budgetRevision') != policy['revision'] or not isinstance(denial.get('reasons'), list) or len(denial['reasons']) > 16 or any(not isinstance(reason, str) or not reason or len(reason) > 1000 for reason in denial['reasons']):
            raise ValueError('Saved budget denial is invalid')
        policy['lastDenial'] = {'budgetRevision': denial['budgetRevision'], 'reasons': denial['reasons'].copy()}
    return policy


class CapacityController:
    """One runtime control-state writer; edits are valid between provider calls."""
    def __init__(self, controls):
        self.controls = controls
        self.policy = policy_snapshot()
        self.receipts = {}
        self.last_denial = None
        self.admit = None

    def snapshot(self):
        return policy_snapshot(self.policy, self.last_denial)

    async def perform(self, operation, args):
        if operation == 'capacity.quota':
            return await self.quota()
        if operation == 'capacity.get':
            return self.snapshot()
        if operation != 'capacity.set':
            raise ValueError('Unknown capacity control')
        identity = args.get('commandId')
        if not isinstance(identity, str) or not identity:
            raise ValueError('Capacity edits need a stable commandId')
        fingerprint = hashlib.sha256(json.dumps(args, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if identity in self.receipts:
            if self.receipts[identity]['fingerprint'] != fingerprint:
                raise ValueError('This budget command ID has different contents')
            return {**copy.deepcopy(self.policy), 'duplicate': True}
        if type(args.get('expectedRevision')) is not int or args['expectedRevision'] != self.policy['revision']:
            raise ValueError('The budget revision changed; inspect it before editing')
        previous, denial = self.policy, self.last_denial
        policy = copy.deepcopy(previous)
        for key in ('enabled', 'maxTotalTokens', 'maxCostUsd', 'warningFraction', 'unknownPolicy', 'includeEstimatedCost'):
            if key in args:
                policy[key] = args[key]
        for key in ('maxTotalTokens', 'maxCostUsd'):
            if policy[key] is not None and (not number(policy[key]) or policy[key] <= 0 or (key == 'maxTotalTokens' and type(policy[key]) is not int)):
                raise ValueError('Limits must be positive numbers or null')
        if not number(policy['warningFraction']) or not 0 < policy['warningFraction'] <= 1 or policy['unknownPolicy'] not in ('pause', 'allow'):
            raise ValueError('Invalid warning threshold or unknown-usage policy')
        if any(type(policy[key]) is not bool for key in ('enabled', 'includeEstimatedCost')):
            raise ValueError('Budget switches must be booleans')
        if policy['enabled'] and policy['maxTotalTokens'] is None and policy['maxCostUsd'] is None:
            raise ValueError('An enabled budget needs at least one limit')
        policy_snapshot(policy)
        task = self.controls.tasks.record() or {}
        policy.update(revision=previous['revision'] + 1, updatedAt=time.time(), origin=args.get('origin'), commandId=identity,
                      taskId=task.get('id'), taskRevision=task.get('revision'), scope='conversation lifetime observed usage')
        self.policy, self.last_denial = policy, None
        self.receipts[identity] = {'fingerprint': fingerprint, 'revision': policy['revision']}
        try:
            self.controls.persist(task_only=True)
        except Exception:
            self.policy, self.last_denial = previous, denial
            self.receipts.pop(identity, None)
            raise
        return copy.deepcopy(policy)

    async def guard(self, row):
        # Always ask the host: it owns persisted usage and can reject stale
        # workers. A missing bridge cannot silently bypass an enabled budget.
        if self.admit is None:
            if self.policy.get('enabled'):
                raise ValueError('Task budget admission is unavailable')
            return
        result = await self.admit(row)
        if result['allowed']:
            row['budgetRevision'] = result['budgetRevision']
            row['admittedAt'] = result.get('admittedAt')
        if not result['allowed']:
            self.last_denial = {**result, 'at': time.time(), 'callId': row['id']}
            self.controls.persist(task_only=True)
            raise ValueError('Task budget paused future model calls: ' + '; '.join(result['reasons']))

    async def quota(self):
        rows = []
        for instance, provider in (self.controls.coordinator.get('providers') or {}).items():
            callback = getattr(provider, 'get_usage_limits', None)
            if not callable(callback):
                rows.append({'provider': instance, 'status': 'unsupported', 'remaining': None, 'resetsAt': None})
                continue
            try:
                value = callback()
                if inspect.isawaitable(value):
                    value = await asyncio.wait_for(value, timeout=10)
                # Provider-owned read-only capability. Never expose credentials,
                # arbitrary account metadata, or a provider response wholesale.
                windows = []
                for window in value.get('windows', [])[:30]:
                    if not isinstance(window, dict):
                        continue
                    safe = {key: window[key] if number(window.get(key)) else None for key in ('limit', 'used', 'remaining', 'resetsAt')}
                    safe.update({key: str(window[key])[:120] for key in ('name', 'unit') if isinstance(window.get(key), str)})
                    windows.append(safe)
                rows.append({'provider': instance, 'status': 'reported' if windows else 'unknown', 'source': 'provider.get_usage_limits', 'observedAt': time.time(), 'windows': windows})
            except Exception:
                rows.append({'provider': instance, 'status': 'unavailable', 'remaining': None, 'resetsAt': None})
        return {'providers': rows, 'scope': 'provider account capability; separate from task budget'}
