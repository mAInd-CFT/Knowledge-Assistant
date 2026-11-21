import re


def normalize_user_query(text: str) -> str:
    value = re.sub(r"\x00", "", text or "").strip()
    if not value:
        raise ValueError("Пустой вопрос")
    if len(value) > 10_000:
        raise ValueError("Вопрос превышает 10 000 символов")
    return value


def validate_answer(answer: str) -> str:
    value = (answer or "").strip()
    if not value:
        raise RuntimeError("LLM вернула пустой ответ")
    # This is a fail-closed tripwire, not a complete prompt-injection defence.
    suspicious = (
        "BEGIN SYSTEM PROMPT", "SYSTEM INSTRUCTIONS:",
        "ТЫ — ИНФОРМАЦИОННЫЙ ИИ-АССИСТЕНТ АО", "ТВОЯ ПРЕДМЕТНАЯ ОБЛАСТЬ ОГРАНИЧЕНА",
    )
    if any(marker.casefold() in value.casefold() for marker in suspicious):
        return (
            "Не могу раскрыть внутренние инструкции или служебные данные. "
            "Могу помочь с вопросом о продуктах и материалах ЦФТ."
        )
    return value
