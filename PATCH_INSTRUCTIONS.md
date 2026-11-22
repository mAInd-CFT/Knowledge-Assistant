# Исправление тестов prompt injection

Распакуйте архив поверх корня `Knowledge-Assistent`, сохранив структуру папок.

Изменения:
- `backend/app/agent/orchestrator.py` — короткие самостоятельные вопросы больше не получают нерелевантный контекст прошлой переписки; контекст добавляется при явных указателях на предыдущий ответ.
- `backend/tests/test_agent.py` — тест проверяет, что валидная часть вопроса дошла до LLM, а не требует от заглушки выдумать ответ.
- `backend/scripts/security_smoke.py` — исправлен импорт `app` при запуске скрипта из `backend`.
- `backend/tests/test_prompt_injection.py` и `backend/tests/test_security.py`, `docs/security_test_cases.md`, `README.md` — дополнительные тесты и документация из предыдущего патча.

Команды из папки `backend`:

```powershell
python -m pytest -q tests/test_prompt_injection.py tests/test_agent.py tests/test_security.py
python scripts/security_smoke.py
```

Для `security_smoke.py` нужен действующий `DEEPSEEK_API_KEY` в локальном `.env` и настроенные зависимости. Smoke-тест вызывает реальную LLM и сохраняет отчёт в `data/processed/security_smoke_report.json`.

Не добавляйте `.env` и отчёт smoke-теста в публичный архив. В архиве патча секретов нет.
