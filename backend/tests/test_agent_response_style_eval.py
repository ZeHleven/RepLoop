import importlib.util
from pathlib import Path


def _runner():
    file = Path(__file__).resolve().parents[1] / 'scripts/evaluate_agent_response_style.py'
    spec = importlib.util.spec_from_file_location('style_eval', file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_style_corpus_has_unique_ids_heldout_context_and_evidence_cases():
    cases = _runner().load_cases()
    assert len({c['id'] for c in cases}) == len(cases) == 20
    assert sum(c['split'] == 'final_holdout' for c in cases) == 4
    assert any(c.get('history') for c in cases)
    assert any(c.get('stage') == 'finalizer' for c in cases)
    assert all(c['review'] and c['max_chars'] > 0 for c in cases)


def test_style_flags_are_diagnostic_not_a_false_safety_or_quality_grade():
    flags = _runner().style_flags
    assert flags('简短回答。', 20) == []
    assert flags('', 20) == ['empty_reply']
    assert flags('## 一\n### 二\nmissing_slots', 5) == ['long_for_case', 'heading_heavy', 'technical_leak']


def test_baseline_rejects_mutable_refs_before_running_git():
    import pytest
    with pytest.raises(ValueError, match='immutable'):
        _runner().baseline_prompt('main', 'direct')
