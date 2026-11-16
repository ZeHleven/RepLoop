# Multi-turn requirements and task completion

This suite has two separate layers. Neither layer is a production success-rate estimate.

`multiturn_cases.json` contains eight independently annotated semantic cases. The route model runs on real user turns; intermediate assistant acknowledgements and window-filling messages are synthetic fixtures. Version 1.1 has six development cases and two holdouts. `latest_replaces` was removed from holdout when it exposed an evaluator false negative: “由25分钟调整为最多35分钟” correctly replaces the old duration. The evaluator accepts this explicit replacement form, with negative controls for still-active old limits. Required dates accept ISO and Chinese representations.

```text
python backend/scripts/evaluate_multiturn_semantics.py --split development --output <new-directory>
python backend/scripts/evaluate_multiturn_semantics.py --split all --replay-from <immutable-records> --output <new-directory>
```

`--without-state` provides an ablation. Replay makes no model calls; its fingerprint identifies the evaluator, not the original execution. Semantic checks inspect the normalized request and action, not the model's own claim that requirements are complete. Phrase checks cover this dataset only, not arbitrary meaning or hallucinations.

The business suite uses real API handlers, PostgreSQL schemas, tools, proposals and SQL readback. Five cases cover meal confirmation, rejection and cancellation; a non-week-aligned training-history range; and revision of an unconfirmed training proposal. Twenty-four synthetic messages push the original request past the runtime history window. Expected results are fixed independently: no writes before confirmation, one meal of rice 120g/156kcal after confirmation, no meal after rejection/cancellation, exactly the two completed sessions in range, and a confirmed bench-press target of 5 sets × 8 reps. The original plan is 3 × 10; an intermediate unconfirmed proposal is 4 × 8. The second scripted extraction intentionally returns only the new set count to reproduce the real omission.

```text
python backend/scripts/evaluate_multiturn_tasks.py --output <new-directory>
python backend/scripts/evaluate_multiturn_tasks.py --mode live --output <new-directory>
```

Set `DATABASE_URL` and `AGENT_QUERY_EVAL_DATABASE_URL` to the dedicated local PostgreSQL `*_test` database. Live mode uses configured provider credentials; scripted mode replaces model boundaries and never measures semantic model capability. One additional SQL isolation check covers another owner/conversation, future queued Runs and failed Runs. Proposal reuse also checks pending status, expiry and applied-state invalidation. Existing lease/idempotency tests remain in the main suite.

Results preserve HTTP bodies without authorization headers, raw structured model outputs, execution traces, tool audits and pytest failures. A passing gate means the expected outcome occurred: rejection/cancellation are reported as those outcomes, not as successful writes. In-progress, ended-early and out-of-range sessions provide counterfactual data. Plain bounded history replies share the actual SQL/card scope and cannot infer absence of unqueried statuses.

The five cases are development cases; do not relabel them as unseen holdouts after fixing them. Keep every attempt and any evaluator reclassification. Missing records or a nonzero pytest result fail the gate. CI runs the scripted task suites separately and preserves artifacts on failure.

Not covered: physical WeChat interaction, production task mix, arbitrary paraphrases, cross-session preferences, long-term fitness outcomes, aggregate token billing, queue/load measurements. Runtime `responded` means a response was produced, not that a user's business objective was achieved. Business completion is independently checked in these tasks.
