# Проверка редакции 1.6 — hardening

Проверки, выполненные в среде подготовки архива:

- `python -m compileall -q backend/app backend/ingest backend/tests` — успешно.
- `docker-compose.yml` разбирается PyYAML — успешно; определены 7 сервисов.
- `node --check frontend/app.js` — успешно (если Node.js доступен).
- Добавлены unit-тесты для chunking структуры, context-aware retrieval, signed client tokens и основных защитных tripwire.

Что обязательно проверить в рабочем Docker-окружении перед защитой:

1. `docker compose up -d --build` и `docker compose ps`.
2. `docker compose --profile crawl run --build --rm crawler`; убедиться, что в crawl report нет запросов за пределы allowlist.
3. `docker compose --profile ingest run --build --rm ingestion`; проверить `data/processed/ingestion_report.json`.
4. `cd backend && pip install -r requirements-dev.txt && python -m pytest -q`.
5. `cd backend && python scripts/security_smoke.py` с действующим `DEEPSEEK_API_KEY`; вручную оценить прямую, косвенную, многошаговую атаку и легитимный технический вопрос.
6. Проверить открытие источников, `[n]` citations, новую сессию и продолжение диалога после перезагрузки страницы.
7. `scripts/backup.sh`, затем восстановление backup на отдельном тестовом окружении.

Ограничения среды подготовки: Docker daemon и внешний DeepSeek API здесь не запускались. Поэтому не заявляется, что реальные контейнеры, производительность модели, качество ответов и adversarial поведение конкретного LLM уже подтверждены. Порог `RETRIEVAL_MIN_SCORE` — начальная настройка; его надо откалибровать на экспертном наборе вопросов ЦФТ.

Дополнительная проверка в среде сборки архива:

- `pytest -q tests/test_chunker.py tests/test_client_auth.py` — 6 passed.
- Полный `pytest -q` не смог завершить сбор тестов, поскольку среда проверки не содержит установленные runtime-зависимости `openai`, `psycopg2` и `qdrant_client`. Это не результат провала тестовых assertions; полный прогон нужно повторить внутри backend-образа или после `pip install -r requirements-dev.txt`.


## Дополнительные проверки 1.6.1

Добавлены regression-тесты для метаданных локальных документов без sidecar и
разделения BM25 query/passage encoding. OCR image теперь содержит prefetch
Docling models и явный artifacts path. После сборки в рабочей среде обязательно
проверить `docker compose --profile ingest-ocr run --build --rm ingestion-ocr`,
наличие `data/processed/ingestion_report.json` с `processing_errors: 0` (либо
разобрать каждую ошибку) и заполнение коллекции `knowledge_chunks_e5_large_v2`.
