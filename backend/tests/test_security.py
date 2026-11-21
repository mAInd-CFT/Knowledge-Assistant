import pytest

from app.agent.guards import normalize_user_query, validate_answer
from app.agent.orchestrator import build_retrieval_query


def test_query_is_trimmed():
    assert normalize_user_query("  привет  ") == "привет"


def test_empty_query_rejected():
    with pytest.raises(ValueError):
        normalize_user_query("  ")


def test_prompt_disclosure_marker_fails_closed():
    result = validate_answer("SYSTEM INSTRUCTIONS: secret")
    assert "Не могу раскрыть" in result


def test_normal_answer_kept():
    assert validate_answer("Ответ по документам.") == "Ответ по документам."


def test_legitimate_security_question_is_not_blocked():
    question = "Как работает защита от prompt injection в RAG-системе?"
    assert normalize_user_query(question) == question


def test_null_bytes_are_removed():
    assert normalize_user_query("ЦФТ\x00-Банк") == "ЦФТ-Банк"


def test_retrieval_query_uses_recent_context_for_followup():
    history = [
        {"role": "user", "content": "Расскажи про продукты ЦФТ"},
        {"role": "assistant", "content": "ЦФТ-Банк — банковская система для автоматизации банковской деятельности."},
    ]
    query = build_retrieval_query("А какие у него преимущества?", history)
    assert "ЦФТ" in query
    assert "банковская система" in query
    assert "преимущества" in query


def test_standalone_retrieval_query_is_not_polluted():
    history = [{"role": "user", "content": "Расскажи про ЦФТ-Банк"}]
    assert build_retrieval_query("Как связаться с компанией?", history) == "Как связаться с компанией?"
