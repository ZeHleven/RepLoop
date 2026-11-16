# Pending proposal revision regression

Eight synthetic tasks were first evaluated against frozen round09 code (2/8 passed). They are now **development regressions**, not unseen holdouts. `revision_cases.json` preserves the original user turns and golds byte for byte. Three plan tasks, three meal tasks and two history queries exercise 17 turns through real API handlers, worker processing, proposals and PostgreSQL.

```text
python backend/scripts/evaluate_revision_tasks.py --output <fresh-directory>
python backend/scripts/evaluate_revision_tasks.py --mode live --output <fresh-directory>
```

Set `DATABASE_URL` and `AGENT_QUERY_EVAL_DATABASE_URL` to a dedicated local PostgreSQL `*_test` database. Each task uses an isolated schema. Live mode uses the configured provider; scripted mode replaces only model boundaries with saved synthetic structured outputs. Route task IDs are remapped and route edits normalized; separate unit tests cover the model's malformed preservation/withdrawal outputs. Scripted mode measures service behavior, not model comprehension. No auth headers or provider credentials are saved.

Checks compare final SQL to fixed golds, not the model's claim of success: all turns leave business rows unchanged until confirmation; confirmation is idempotent; plans retain every specified field across both exercises; meals retain date/type and only the revised food list with server-calculated calories; query cards contain exactly the requested completed sessions or an empty result. Replies must survive conversation reopening. Invalid score examples in the fixture check reject unchanged plans, absent meals and absent history cards.

Raw records retain HTTP bodies, model messages/output, tool audits, execution traces, SQL snapshots and errors. Clarifications are counted from API response flags. Per-turn waits include local enqueue, worker and result read, not phone/network rendering or production load. Recovery corrections are not measured: the fixed script performs only planned turns. A queue-time reversal is annotated without rewriting timestamps or changing the task score; its cause needs separate investigation.

CI runs the scripted suite separately from the main tests and uploads failures too. Missing records, failed gold checks or a nonzero pytest exit fail the gate. Keep all attempts; a passing rerun does not erase earlier failures. Existing ownership, confirmation, lease and proposal lifecycle checks remain in the backend suite. Physical WeChat interaction, arbitrary language, production task mix and cost are outside this suite.
