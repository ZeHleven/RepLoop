"""Evaluation-only reconciliation: append observations, never rewrite evidence.

No application, HTTP, provider, worker or budget-writer dependency belongs here.
The caller supplies the frozen identity registry and a read-only SQL observer.
Recovery never authorizes execution, even when a completed artifact is found.
"""
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def digest(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Clock:
    wall: float
    mono: float
    epoch: str | None
    source: str = 'monotonic'


def clock_now(source='monotonic'):
    # A process UUID would incorrectly treat every restart as a new boot.
    # An unavailable OS boot identity is unknown, never guessed from wall time.
    try:
        epoch = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        epoch = None
    from execution_clock import counter
    return Clock(time.time(), counter(source)(), epoch, source)


def inspect_budget(state, *, freeze, limits, clock, gap=30):
    """Pure preflight decision; this does not reserve a Run/request or start one.

    Legacy budgets without a boot identity remain readable but cannot authorize
    execution. A new session must persist clock_epoch at creation, and reserve
    cumulative counts atomically in its normal execution harness.
    """
    if state.get('budget_version')==2:
        from clock_policy import inspect_v2
        return inspect_v2(state,freeze=freeze,limits=limits,clock=clock,gap=gap)
    if state.get('budget_version') not in (None,1):
        return dict(read_only_allowed=True,execution_allowed=False,reasons=['unsupported_budget_version'],counters_reserved=False)
    reasons = []
    if state.get('freeze') != freeze or state.get('limits') != limits:
        reasons.append('contract_mismatch')
    if state.get('stop'):
        reasons.append('sticky_stop:' + str(state['stop']))
    fields = ['started', 'deadline', 'last_wall', 'last_mono', 'runs', 'requests']
    numeric = all(type(state.get(k)) in (float, int) and math.isfinite(state[k]) for k in fields)
    valid_limits = all(type(limits.get(k)) is int and limits[k] > 0 for k in ['seconds', 'runs', 'requests'])
    valid_clock = all(type(v) in (float, int) and math.isfinite(v) for v in [clock.wall, clock.mono])
    if not numeric or not valid_limits or not valid_clock:
        reasons.append('invalid_budget_or_clock')
    else:
        if state['deadline'] != state['started'] + limits['seconds']:
            reasons.append('deadline_contract_mismatch')
        if clock.wall >= state['deadline']:
            reasons.append('wall_deadline')
        for key in ['runs', 'requests']:
            if type(state[key]) is not int or state[key] < 0:
                reasons.append('invalid_' + key)
            elif state[key] >= limits[key]:
                reasons.append(key + '_budget')
        if not state.get('clock_epoch') or not clock.epoch:
            reasons.append('clock_epoch_unknown')
        elif state['clock_epoch'] != clock.epoch:
            reasons.append('clock_epoch_changed')
        else:
            dw, dm = clock.wall - state['last_wall'], clock.mono - state['last_mono']
            if dw < 0 or dm < 0 or abs(dw - dm) > gap:
                reasons.append('clock_discontinuity')
        if state['last_wall'] < state['started'] or state['last_mono'] < 0 or clock.mono < 0:
            reasons.append('invalid_clock_history')
    if not isinstance(state.get('instances'), dict) or any(v not in ('started', 'finished') for v in state.get('instances', {}).values()):
        reasons.append('invalid_instances')
    return dict(read_only_allowed=True, execution_allowed=not reasons, reasons=reasons,
                deadline=state.get('deadline'), runs=state.get('runs'), requests=state.get('requests'),
                counters_reserved=False)


def assert_execution_allowed(state, instance, **kwargs):
    decision = inspect_budget(state, **kwargs)
    if instance in state.get('instances', {}):
        decision['reasons'].append('instance_already_started')
        decision['execution_allowed'] = False
    if not decision['execution_allowed']:
        raise RuntimeError(','.join(decision['reasons']))
    return decision


@dataclass(frozen=True)
class Scope:
    schema: str | None
    user_id: str | None
    conversation_id: str | None
    expected_turns: int


@dataclass(frozen=True)
class Contract:
    schema_prefix: str
    freeze_sha256: str
    scopes: dict[str, Scope]

    def __post_init__(self):
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,19}_', self.schema_prefix):
            raise ValueError('Invalid explicit schema prefix')
        if not re.fullmatch(r'[0-9a-f]{64}', self.freeze_sha256):
            raise ValueError('Invalid freeze digest')
        for instance, scope in self.scopes.items():
            if not re.fullmatch(r'[A-Z][A-Z0-9]{0,30}-r[1-9][0-9]*', instance):
                raise ValueError('Invalid instance identifier')
            if type(scope.expected_turns) is not int or scope.expected_turns < 1:
                raise ValueError('Invalid expected turn count')


class ObservationRejected(Exception):
    """Reason code only; do not export foreign records or database credentials."""


def local_run_ids(rec):
    ids = {t['body']['id'] for t in rec.get('turns', []) if isinstance(t, dict)
           and isinstance(t.get('body'), dict) and t['body'].get('id')}
    for row in rec.get('http', []):
        if not isinstance(row, dict) or not isinstance(row.get('body'), dict):
            continue
        if row.get('method') == 'POST' and row.get('path') == '/api/v1/agent/runs' and row.get('status') in (200, 201, 202):
            rid = row['body'].get('run_id')
            if rid:
                ids.add(rid)
    if any(not isinstance(x, str) or not re.fullmatch(r'[0-9a-fA-F-]{36}', x) for x in ids):
        raise ObservationRejected('invalid_run_id')
    return sorted(ids)


def validate_record(rec, instance, contract):
    scope = contract.scopes[instance]
    if not isinstance(rec, dict) or rec.get('instance') != instance:
        raise ObservationRejected('instance_mismatch')
    if rec.get('freeze_sha256') != contract.freeze_sha256:
        raise ObservationRejected('freeze_mismatch')
    if not rec.get('schema'):
        raise ObservationRejected('schema_not_initialized')
    if not isinstance(rec['schema'], str) or not re.fullmatch(re.escape(contract.schema_prefix) + r'[0-9a-f]{32}', rec['schema']):
        raise ObservationRejected('schema_prefix_mismatch')
    for field in ('schema', 'user_id', 'conversation_id'):
        if not getattr(scope, field) or rec.get(field) != getattr(scope, field):
            raise ObservationRejected('identity_registry_mismatch')
    for field in ('turns', 'http', 'provider', 'tool_attempts'):
        if not isinstance(rec.get(field), list) or any(not isinstance(x, dict) for x in rec[field]):
            raise ObservationRejected('malformed_' + field)
    return scope


def local_completion(rec, expected_turns):
    turns = rec.get('turns', [])
    score = rec.get('score') or {}
    bodies = [t.get('body') or {} for t in turns]
    pending = sum(x.get('lifecycle_complete') is not True for x in rec.get('provider', []))
    complete = (bool(rec.get('finished_at')) and not rec.get('error') and
                len(turns) == expected_turns and
                [t.get('index') for t in turns] == list(range(expected_turns)) and
                all(b.get('status') == 'completed' and isinstance(b.get('reply'), str) and b['reply'].strip() for b in bodies) and
                score.get('terminal_delivery_eligible') is True and
                score.get('tool_ledger', {}).get('complete') is True and pending == 0 and
                all(x.get('lifecycle_complete') is True for x in rec.get('http', [])))
    return dict(complete=bool(complete), provider_pending=pending, turns_recorded=len(turns), expected_turns=expected_turns)


def reconcile(rec, local, observation):
    rows = observation['runs']
    states = [r.get('status') for r in rows]
    if not states:
        return 'sql_run_missing'
    terminal = all(s in ('completed', 'failed') for s in states)
    local_ids = {t['body']['id'] for t in rec.get('turns', []) if isinstance(t.get('body'), dict) and t['body'].get('id')}
    sql_ids = {r['id'] for r in rows}
    if local['complete']:
        messages = observation.get('messages', [])
        replies_match = all(
            [m.get('content') for m in messages if m.get('run_id') == t['body']['id'] and m.get('role') == 'assistant'] == [t['body']['reply']]
            for t in rec['turns'])
        if terminal and all(s == 'completed' for s in states) and local_ids == sql_ids and replies_match:
            return 'completed_artifact_preserved'
        return 'local_sql_conflict'
    if rec.get('finished_at') and rec.get('error'):
        return 'failed_artifact_preserved' if terminal and 'failed' in states else 'local_sql_conflict'
    if terminal:
        return 'terminal_observed_pending_review'
    if all(s in ('running', 'queued', 'completed', 'failed') for s in states):
        return 'unresolved_in_flight'
    return 'unknown_sql_status'


async def recover_session(root, budget_path, output_dir, contract, observer, *, freeze, limits, clock=None):
    """Inspect all started slots; produce one new, exclusive report per attempt."""
    root, budget_path, output_dir = Path(root), Path(budget_path), Path(output_dir)
    budget_bytes = budget_path.read_bytes()
    budget = json.loads(budget_bytes)
    source=budget.get('clock_policy',{}).get('elapsed_clock','monotonic')
    decision = inspect_budget(budget, freeze=freeze, limits=limits, clock=clock or clock_now(source))
    if budget.get('freeze') != freeze or budget.get('limits') != limits:
        raise ValueError('Recovery contract mismatch')
    if 'invalid_instances' in decision['reasons']:
        raise ValueError('Invalid instance ledger')
    if output_dir.resolve() == root.resolve() or output_dir.resolve() == (root / 'runs').resolve():
        raise ValueError('Use a separate recovery report directory')
    report = dict(version=1, at=datetime.now(timezone.utc).isoformat(), budget_sha256=digest(budget_bytes),
                  freeze_sha256=contract.freeze_sha256, budget_inspection=decision,
                  identity_contract_sha256=digest(json.dumps(asdict(contract),sort_keys=True,ensure_ascii=False).encode()),
                  method='SQL read-only observation; no HTTP, Run submission, worker or model',
                  execution_authorized=False, task_success=None, token_total=None, latency=None, instances=[])
    originals = {}
    for instance, slot in sorted(budget['instances'].items()):
        if slot != 'started':
            continue
        item = dict(instance=instance, original_slot=slot, task_success=None)
        report['instances'].append(item)
        if instance not in contract.scopes:
            item.update(classification='unregistered_instance')
            continue
        path = root / 'runs' / (instance + '.json')
        if not path.exists():
            item.update(classification='raw_missing', original_sha256=None)
            originals[path] = None
            continue
        raw = path.read_bytes()
        originals[path] = raw
        item['original_sha256'] = digest(raw)
        try:
            rec = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            item.update(classification='raw_corrupt')
            continue
        try:
            scope = validate_record(rec, instance, contract)
            ids = local_run_ids(rec)
            item['local'] = local_completion(rec, scope.expected_turns)
            observation = await observer(scope, ids)
            if observation.get('scope_validated') is not True or observation.get('read_only') is not True:
                raise ObservationRejected('observer_contract_invalid')
            item['classification'] = reconcile(rec, item['local'], observation)
            item['sql_observation'] = observation
        except ObservationRejected as exc:
            item.update(classification='observation_rejected', reason=str(exc))
        except Exception as exc:
            item.update(classification='observation_unavailable', error_type=type(exc).__name__)
    # Fail if a concurrent writer changed the inspected evidence. No result can
    # be mistaken for a stable snapshot, and the original writer is untouched.
    for path, raw in originals.items():
        if (path.read_bytes() if path.exists() else None) != raw:
            raise RuntimeError('Source changed during recovery')
    if budget_path.read_bytes() != budget_bytes:
        raise RuntimeError('Budget changed during recovery')
    report['source_bytes_unchanged'] = True
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / ('recovery-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '-' + uuid4().hex + '.json')
    with target.open('x', encoding='utf-8') as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    return target, report
