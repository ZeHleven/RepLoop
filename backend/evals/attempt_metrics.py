"""Versioned cross-attempt observations. Business scores are never inferred.

Registered coroutine intent, dispatch, transport and response denominators stay
separate. Partial evidence yields known subtotals, never invented full rates.
This module uses only the standard library so saved evidence is replayable.
"""
import json
import math
from collections import Counter
from decimal import Decimal, InvalidOperation

from evals.tool_attempt_ledger import tool_attempt_ledger


class InvalidObservation(ValueError):
    pass


def require(value, reason):
    if not value:
        raise InvalidObservation(reason)


def integer(value, minimum=0):
    require(type(value) is int and value >= minimum, "invalid_integer")
    return value


def token(value):
    if value is not None:
        integer(value)
    return value


def decode(value):
    require(isinstance(value, str), "missing_json_text")
    def invalid(_):
        raise InvalidObservation("nonfinite_json")
    try:
        return json.loads(value, parse_constant=invalid)
    except (ValueError, TypeError) as exc:
        raise InvalidObservation("invalid_json_text") from exc


def returned_error(value):
    if not isinstance(value, dict):
        return False
    return (value.get("status") in ("error", "failed", "failure", "cancelled", "timeout")
            or value.get("success") is False or value.get("ok") is False
            or any(value.get(k) for k in ("error", "error_code", "error_type", "exception")))


COUNTERS = ("client_attempts", "transport_attempts", "http_requests", "http_responses", "http_successes", "http_errors",
            "transport_errors", "local_errors", "unclassified_client_errors", "pending_or_cancelled", "usage_known_requests",
            "known_input_tokens", "known_output_tokens")


def provider_usage(usage, attempt):
    require(isinstance(usage, dict) and usage.get("version") == "transport_boundary_v2"
            and usage.get("scope") == "worker_attempt", "unsupported_model_usage")
    require(integer(usage.get("worker_attempt")) == attempt, "usage_attempt_mismatch")
    require(type(usage.get("prior_attempts_unobserved")) is bool and usage["prior_attempts_unobserved"] == (attempt > 1), "legacy_attempt_flag_mismatch")
    require(type(usage.get("observation_complete")) is bool, "invalid_complete_flag")
    calls = usage.get("calls")
    require(isinstance(calls, list), "missing_client_calls")
    all_responses = []
    for index, call in enumerate(calls, 1):
        require(isinstance(call, dict) and integer(call.get("sequence"), 1) == index, "invalid_client_sequence")
        require(call.get("source") in ("agent", "structured", "chat", "daily_meal"), "unknown_client_source")
        require(call.get("outcome") in ("pending", "cancelled", "http_success", "http_error", "local_error", "client_error", "transport_error"), "unknown_client_outcome")
        require(type(call.get("transport_boundary_observed")) is bool, "invalid_transport_flag")
        for key in ("transport_attempts", "transport_errors"):
            integer(call.get(key))
        responses = call.get("responses")
        require(isinstance(responses, list), "missing_responses")
        for response in responses:
            require(isinstance(response, dict) and 100 <= integer(response.get("http_status")) <= 599, "invalid_http_status")
            for key in ("input_tokens", "output_tokens", "cached_input_tokens"):
                token(response.get(key))
            cache = response.get("cached_input_tokens")
            require(cache is None or (response.get("input_tokens") is not None and cache <= response["input_tokens"]), "invalid_cached_tokens")
            all_responses.append(dict(response, source=call["source"]))
        for key in ("http_status", "input_tokens", "output_tokens", "cached_input_tokens"):
            if key != "http_status":
                token(call.get(key))
            require(call.get(key) == (responses[-1].get(key) if responses else None), "last_response_mismatch")
        if call["outcome"] in ("http_success", "http_error"):
            require(bool(responses), "terminal_http_without_response")
            require((call["outcome"] == "http_success") == (200 <= responses[-1]["http_status"] < 300), "http_outcome_mismatch")
        if call["outcome"] == "local_error":
            require(call["transport_boundary_observed"] and not call["transport_attempts"] and not responses, "local_error_after_transport")
    pending = sum(c["outcome"] in ("pending", "cancelled") for c in calls)
    requests = sum(max(c["transport_attempts"], len(c["responses"])) for c in calls)
    known = sum(r.get("input_tokens") is not None and r.get("output_tokens") is not None for r in all_responses)
    derived = dict(client_attempts=len(calls), transport_attempts=sum(c["transport_attempts"] for c in calls),
        http_requests=requests, http_responses=len(all_responses),
        http_successes=sum(200 <= r["http_status"] < 300 for r in all_responses),
        http_errors=sum(not 200 <= r["http_status"] < 300 for r in all_responses),
        transport_errors=sum(c["transport_errors"] for c in calls), local_errors=sum(c["outcome"] == "local_error" for c in calls),
        unclassified_client_errors=sum(c["outcome"] == "client_error" for c in calls), pending_or_cancelled=pending,
        usage_known_requests=known, known_input_tokens=sum(r.get("input_tokens") or 0 for r in all_responses),
        known_output_tokens=sum(r.get("output_tokens") or 0 for r in all_responses))
    for key, value in derived.items():
        require(integer(usage.get(key)) == value, "model_counter_mismatch:" + key)
    require(not (pending and usage["observation_complete"]), "pending_marked_complete")
    certain = not any(c["outcome"] in ("client_error", "transport_error") for c in calls)
    legacy_full = usage["observation_complete"] and not pending and certain and known == requests and attempt <= 1
    for key in ("input_tokens", "output_tokens"):
        require(token(usage.get(key)) == (derived["known_" + key] if legacy_full else None), "legacy_total_mismatch:" + key)
    transport_complete = all(c["transport_boundary_observed"] for c in calls)
    totals_known = usage["observation_complete"] and not pending and certain and transport_complete and known == requests and not derived['transport_errors']
    return derived, all_responses, totals_known, transport_complete and not pending


def history_metrics(run, history):
    count = integer(run.get("attempt_count"))
    require(count <= 100, "attempt_count_exceeds_observation_limit")
    require(isinstance(history, dict) and history.get("version") == "attempt_journal_v1"
            and history.get("scope") == "observed_worker_attempts", "unsupported_attempt_history")
    require(history.get("run_status") == run.get("status"), "history_status_mismatch")
    attempts = history.get("attempts")
    require(isinstance(attempts, list), "missing_attempts_array")
    ids = [integer(a.get("worker_attempt"), 1) for a in attempts]
    require(ids == sorted(set(ids)) and all(a <= count for a in ids), "duplicate_or_foreign_attempt")
    missing = sorted(set(range(1, count+1)) - set(ids))
    require(history.get("missing_attempts") == missing, "missing_attempt_claim_mismatch")
    counters = Counter(); tool_counts = Counter(); responses = []; tools = []; issues = []
    covered = bool(count) and not missing and len(ids) == count
    token_complete = covered and run.get("status") in ("completed", "failed")
    api_complete = token_complete
    request_complete = token_complete
    for attempt in attempts:
        aid = attempt["worker_attempt"]
        expected_state = "closed" if attempt.get("finished_at") else ("active" if aid == count and run.get("status") == "running" else "interrupted")
        require(attempt.get("state") == expected_state, "attempt_state_mismatch")
        if attempt.get("model_usage") is None:
            issues.append(f"{aid}:missing_model_observation");token_complete = request_complete = api_complete = False
        else:
            c, r, known, complete = provider_usage(attempt["model_usage"], aid)
            counters.update(c);responses.extend(r);token_complete &= known;request_complete &= complete
            usage=attempt['model_usage']
            api_complete &= (usage['observation_complete'] and not c['unclassified_client_errors'] and not c['transport_errors'] and c['usage_known_requests']==c['http_requests'])
        calls = attempt.get("tool_calls")
        require(isinstance(calls, list), "missing_tool_array")
        for sequence, call in enumerate(calls, 1):
            require(isinstance(call, dict) and integer(call.get("sequence"), 1) == sequence, "invalid_tool_sequence")
            require(isinstance(call.get("name"), str) and bool(call["name"]), "invalid_tool_name")
            arguments = decode(call.get("arguments_json"))
            require(isinstance(arguments, dict) and isinstance(arguments.get("args"), list) and isinstance(arguments.get("kwargs"), dict), "invalid_tool_arguments")
            state = call.get("state")
            require(state in ("pending", "completed", "failed", "cancelled"), "unknown_tool_state")
            result = None
            if state == "completed":
                result = decode(call.get("result_json"))
                require(call.get("error_type") is None, "completed_tool_has_exception")
                state = "returned_error" if returned_error(result) else "success"
            elif state in ("failed", "cancelled"):
                require(isinstance(call.get("error_type"), str) and bool(call["error_type"]), "missing_tool_error_type")
            else:
                require(call.get("result_json") is None and call.get("error_type") is None, "pending_tool_has_terminal_result")
            if state != "pending":
                integer(call.get("duration_ms"))
            tools.append(dict(run_id=run["id"], worker_attempt=aid, sequence=sequence, name=call["name"], status=state, error_type=call.get('error_type'), arguments=arguments, result=result))
            tool_counts[state] += 1
    # API convenience totals must match details; their completeness can be less
    # strict than this evaluator (e.g. unsupported transport instrumentation).
    for key in ("known_input_tokens", "known_output_tokens"):
        require(integer(history.get(key)) == counters[key], "history_counter_mismatch:" + key)
    require(type(history.get("usage_complete")) is bool, "invalid_history_complete")
    require(history['usage_complete'] == api_complete, "history_complete_claim_mismatch")
    for key in ("input_tokens", "output_tokens"):
        require(token(history.get(key)) == (counters["known_" + key] if history["usage_complete"] else None), "history_total_mismatch:" + key)
    if missing:
        issues.append("missing_worker_attempts")
    return dict(counters=counters, responses=responses, tools=tools, tool_counts=tool_counts, covered=covered,
                token_complete=token_complete, request_complete=request_complete, issues=issues, missing=missing)


def estimate_cost(responses, prices, complete):
    low = Decimal(0);high = Decimal(0);priced = 0;issues = []
    for response in responses:
        if response.get("input_tokens") is None or response.get("output_tokens") is None:
            continue
        try:
            require(isinstance(prices, dict) and prices.get("provenance") and prices.get("currency") == "CNY", "missing_price_provenance")
            model = prices["model_by_source"][response["source"]]
            rate = prices["rates_by_model"][model]
            values = []
            for key in ("input_per_million", "cached_input_per_million", "output_per_million"):
                require(isinstance(rate[key], str), "price_must_be_decimal_string")
                number = Decimal(rate[key]);require(number.is_finite() and number >= 0, "invalid_price")
                values.append(number)
            incoming, cached, outgoing = values
            total = response["input_tokens"];hit = response.get("cached_input_tokens")
            fixed = Decimal(response["output_tokens"])*outgoing
            if hit is None:
                low += (Decimal(total)*min(incoming,cached)+fixed)/1000000
                high += (Decimal(total)*max(incoming,cached)+fixed)/1000000
            else:
                amount = (Decimal(total-hit)*incoming+Decimal(hit)*cached+fixed)/1000000
                low += amount;high += amount
            priced += 1
        except (InvalidObservation, InvalidOperation, KeyError, TypeError):
            issues.append("unpriced_response")
    all_priced = not issues and priced == len(responses)
    totals = complete and all_priced
    return dict(currency="CNY", kind="estimate_not_invoice", price_provenance=(prices or {}).get("provenance") if isinstance(prices,dict) else None,
                priceable_responses=priced, known_subtotal_min=str(low), known_subtotal_max=str(high),
                total_min=str(low) if totals else None, total_max=str(high) if totals else None,
                total=str(low) if totals and low == high else None, issues=issues)


def _summarize_attempts(record, *, prices=None):
    issues=[];runs={};snapshots={};details=[];known=Counter();tool_counts=Counter();responses=[];tool_rows=[]
    require(record.get("attempt_observation_version") == 1, "attempt_observation_version_required")
    for run in record.get("runs", []):
        rid=run.get("id")
        if not isinstance(rid,str) or not rid:
            issues.append("missing_run_identity");continue
        if rid in runs and runs[rid] != run:
            issues.append("conflicting_run_snapshot:"+rid)
        runs[rid]=run
    for turn in record.get("turns", []):
        body=turn.get("body") or {};rid=body.get("id")
        if rid not in runs:
            runs[rid]=body
        elif (body.get("status"),body.get("attempt_count")) != (runs[rid].get("status"),runs[rid].get("attempt_count")):
            issues.append("api_sql_run_mismatch:"+str(rid))
    for observation in record.get("attempt_observations", []):
        rid=observation.get("run_id")
        if rid not in runs:
            issues.append("foreign_observation:"+str(rid));continue
        if rid in snapshots and snapshots[rid] != observation:
            issues.append("conflicting_attempt_snapshot:"+str(rid))
        snapshots[rid]=observation
    coverage=bool(runs);tokens_complete=coverage;requests_complete=coverage
    for rid,run in runs.items():
        try:
            require(rid in snapshots, "missing_attempt_snapshot")
            observation=snapshots[rid]
            require(not observation.get("capture_errors"), "capture_failed")
            data=history_metrics(run, observation.get("history"))
            if observation.get("sql") is not None:
                sql=observation["sql"]
                require(sql.get("run_id")==rid and sql.get("user_id")==record.get("user_id")
                        and sql.get("conversation_id")==record.get("conversation_id"), "sql_scope_mismatch")
                require(sql.get('read_only') is True and sql.get('isolation')=='repeatable read', 'sql_not_readonly_snapshot')
                require(sql.get('run_status')==run.get('status') and type(sql.get('attempt_count')) is int and sql['attempt_count']==run.get('attempt_count'), 'sql_run_state_mismatch')
                rows=sql.get("attempts")
                require(isinstance(rows,list) and len(rows)==len(observation["history"]["attempts"]), "sql_attempt_count_mismatch")
                for a,b in zip(rows,observation["history"]["attempts"]):
                    require(a.get("run_id")==rid and all(a.get(k)==b.get(k) for k in ("worker_attempt","model_usage","tool_calls")), "api_sql_attempt_mismatch")
            known.update(data["counters"]);tool_counts.update(data["tool_counts"]);responses.extend(data["responses"]);tool_rows.extend(data["tools"])
            coverage &= data["covered"];tokens_complete &= data["token_complete"];requests_complete &= data["request_complete"]
            details.append(dict(run_id=rid,missing_attempts=data["missing"],issues=data["issues"],source="api_and_sql" if observation.get("sql") is not None else "api_only"))
        except (InvalidObservation,ValueError,TypeError,KeyError) as exc:
            issues.append(str(rid)+":"+str(exc));coverage=tokens_complete=requests_complete=False
    if issues:
        coverage=tokens_complete=requests_complete=False
    pending=tool_counts["pending"];success=tool_counts["success"]
    errors=tool_counts["failed"]+tool_counts["cancelled"]+tool_counts["returned_error"]
    intents=sum(tool_counts.values());tool_complete=coverage and not pending and all(r.get("status") in ("completed","failed") for r in runs.values())
    dispatch=None;dispatch_issues=[]
    if "tool_attempts" in record and "tool_audits" in record and "turns" in record:
        dispatch=tool_attempt_ledger(record)
        if not dispatch["complete"]:dispatch_issues.append("legacy_dispatch_reconciliation_incomplete")
        if any(r.get("attempt_count") != 1 for r in runs.values()):dispatch_issues.append("prior_attempt_dispatch_unobserved")
        used=set();ambiguous=set()
        for row in tool_rows:
            candidates=[]
            for i,f in enumerate(record["tool_attempts"]):
                if i in used:continue
                ti=f.get("turn",0)
                if type(ti) is not int or not 0<=ti<len(record["turns"]):continue
                if record["turns"][ti]["body"]["id"]!=row["run_id"]:continue
                args=f.get("arguments") or {};args=args.get("args") if args.get("type")=="tool_call" else args
                if str(f.get("tool")).replace(".","_")==row["name"] and not row["arguments"]["args"] and args==row["arguments"]["kwargs"]:
                    candidates.append(i)
            if len(candidates)!=1:
                dispatch_issues.append("journal_function_identity_unproven");ambiguous.update(candidates)
            else:
                index=candidates[0];used.add(index);function=record['tool_attempts'][index]
                if row['status']!='pending' and (function.get('status')!=('success' if row['status']=='success' else 'error') or not function.get('lifecycle_complete')):
                    dispatch_issues.append('journal_function_status_mismatch')
        for index,function in enumerate(record['tool_attempts']):
            if index not in used and index not in ambiguous and not (function.get('status')=='error' and function.get('category')=='schema' and function.get('lifecycle_complete')):
                dispatch_issues.append('unmatched_function_without_preinvoke_rejection')
    else:
        dispatch_issues.append("dispatch_evidence_not_collected")
    dispatch_complete=tool_complete and dispatch is not None and dispatch["complete"] and not dispatch_issues
    elapsed=[]
    for turn in record.get("turns",[]):
        value=turn.get("api_to_terminal_ms")
        if turn.get("latency_valid") is True and turn.get("latency_scope")=="submission_to_terminal" and type(value) in (int,float) and math.isfinite(value) and value>=0:
            elapsed.append(value)
        else:elapsed.append(None)
    return dict(version="cross_attempt_eval_v1", business_score=None, evidence_valid=not issues,
        issues=issues, runs=details, coverage_complete=coverage,
        registered_read_intents=dict(observed=intents,successes=success,errors=errors,cancelled=tool_counts["cancelled"],returned_errors=tool_counts["returned_error"],pending=pending,
            complete=tool_complete,success_rate=success/intents if tool_complete and intents else None,error_rate=errors/intents if tool_complete and intents else None,
            scope="registered coroutine intent; pending does not prove SQL execution",rows=tool_rows),
        tool_dispatch=dict(complete=dispatch_complete,issues=sorted(set(dispatch_issues)),
            attempts=dispatch["attempts"] if dispatch_complete else None,
            successes=dispatch['successes'] if dispatch_complete else None,errors=dispatch['errors'] if dispatch_complete else None,
            success_rate=dispatch["success_rate"] if dispatch_complete else None,error_rate=dispatch["error_rate"] if dispatch_complete else None),
        model=dict(known_observations={k:known[k] for k in COUNTERS},observed_requests=known["http_requests"],
            full_request_count=known["http_requests"] if requests_complete else None,
            usage_complete=tokens_complete,input_tokens=known["known_input_tokens"] if tokens_complete else None,
            output_tokens=known["known_output_tokens"] if tokens_complete else None),
        cost=estimate_cost(responses,prices,tokens_complete),latency_ms=elapsed,
        human_takeover_rate=None,answer_accuracy=None,hallucination_rate=None)


def summarize_attempts(record, *, prices=None):
    """Malformed input is an unavailable metric, not a crashed batch scorer."""
    try:
        require(isinstance(record,dict), 'record_not_object')
        result=_summarize_attempts(record,prices=prices)
        if not result['runs']:
            result['issues'].append('no_observed_runs')
            result['evidence_valid']=False
        return result
    except (InvalidObservation,TypeError,ValueError,KeyError,AttributeError) as exc:
        empty=_summarize_attempts({'attempt_observation_version':1},prices=None)
        empty.update(evidence_valid=False,issues=['invalid_observation:'+str(exc)])
        return empty


def summarize_batch(records, *, prices=None):
    """Aggregate unique task records without replacing business/human review."""
    unique={};issues=[];metrics=[];run_ids=set()
    if not isinstance(records,(list,tuple)):
        records=[];issues.append('batch_not_array')
    for record in records:
        if not isinstance(record,dict):
            issues.append('record_not_object');continue
        identity=record.get('instance') or record.get('case_id')
        if not isinstance(identity,str) or not identity:
            issues.append('missing_instance_identity');continue
        if identity in unique:
            if unique[identity]!=record:issues.append('conflicting_instance_snapshot:'+identity)
            continue
        unique[identity]=record
    for identity,record in unique.items():
        metric=summarize_attempts(record,prices=prices if prices is not None else record.get('frozen_model_prices'))
        ids={r['run_id'] for r in metric['runs']}
        if ids & run_ids:
            issues.append('run_shared_between_instances:'+identity);continue
        run_ids.update(ids);metrics.append(metric)
    valid=bool(metrics) and len(metrics)==len(unique) and not issues and all(m['evidence_valid'] for m in metrics)
    tools_complete=valid and all(m['registered_read_intents']['complete'] for m in metrics)
    dispatch_complete=valid and all(m['tool_dispatch']['complete'] for m in metrics)
    tokens_complete=valid and all(m['model']['usage_complete'] for m in metrics)
    known={k:sum(m['model']['known_observations'][k] for m in metrics) for k in COUNTERS}
    tools={k:sum(m['registered_read_intents'][k] for m in metrics) for k in ('observed','successes','errors','cancelled','returned_errors','pending')}
    tools.update(complete=tools_complete,success_rate=tools['successes']/tools['observed'] if tools_complete and tools['observed'] else None,error_rate=tools['errors']/tools['observed'] if tools_complete and tools['observed'] else None)
    dispatch_n=sum(m['tool_dispatch']['attempts'] or 0 for m in metrics)
    dispatch_errors=sum(m['tool_dispatch']['errors'] or 0 for m in metrics)
    token_values=[m['model']['input_tokens']+m['model']['output_tokens'] for m in metrics if m['model']['usage_complete']]
    def percentile(values,q):return sorted(values)[math.ceil(q*len(values))-1] if values else None
    turn_times=[v for m in metrics for v in m['latency_ms']];valid_times=[v for v in turn_times if v is not None]
    clock_complete=valid and bool(turn_times) and len(turn_times)==len(valid_times)
    priced=valid and all(m['cost']['total_min'] is not None and m['cost']['total_max'] is not None for m in metrics)
    lower=sum((Decimal(m['cost']['total_min']) for m in metrics),Decimal(0)) if priced else None
    upper=sum((Decimal(m['cost']['total_max']) for m in metrics),Decimal(0)) if priced else None
    return dict(version='cross_attempt_batch_v1',instances=len(unique),aggregated_instances=len(metrics),evidence_valid=valid,issues=issues,
        known_model_observations=known,registered_read_intents=tools,
        tool_dispatch=dict(complete=dispatch_complete,attempts=dispatch_n if dispatch_complete else None,
            successes=dispatch_n-dispatch_errors if dispatch_complete else None,errors=dispatch_errors if dispatch_complete else None,
            success_rate=(dispatch_n-dispatch_errors)/dispatch_n if dispatch_complete and dispatch_n else None,
            error_rate=dispatch_errors/dispatch_n if dispatch_complete and dispatch_n else None),
        tokens=dict(complete_instances=len(token_values),input_total=known['known_input_tokens'] if tokens_complete else None,
            output_total=known['known_output_tokens'] if tokens_complete else None,
            mean_total=sum(token_values)/len(token_values) if tokens_complete else None,p95_total=percentile(token_values,.95) if tokens_complete else None),
        estimated_cost=dict(currency='CNY',kind='estimate_not_invoice',total_min=str(lower) if priced else None,total_max=str(upper) if priced else None,
            total=str(lower) if priced and lower==upper else None),
        turn_latency_ms=dict(scope='submission_to_terminal_per_turn',concurrency=None,n=len(turn_times),valid_n=len(valid_times),
            p95=percentile(valid_times,.95) if clock_complete else None,p99=percentile(valid_times,.99) if clock_complete else None),
        task_completion_time_ms=None,task_success_rate=None,answer_accuracy=None,hallucination_rate=None,human_takeover_rate=None)
