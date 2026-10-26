import os, json
import pytest

@pytest.fixture(autouse=True)
def _scope_probe(monkeypatch):
    from app.services import agent_runtime as rt
    orig = rt.current_symptom_reports
    def wrapped(message):
        out = orig(message)
        if out and rt.settings.AGENT_SYMPTOM_SCOPE_ENABLED:
            with open('/home/claude/probe/hits.jsonl', 'a') as f:
                f.write(json.dumps({'test': os.environ.get('PYTEST_CURRENT_TEST', '').rsplit(' ', 1)[0], 'message': message[:80]}, ensure_ascii=False) + '\n')
        return out
    monkeypatch.setattr(rt, 'current_symptom_reports', wrapped)
