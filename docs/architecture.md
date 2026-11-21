# Архитектура CFT Knowledge Assistant — DeepSeek + локальный E5

- Frontend: HTML, CSS, JavaScript, Nginx.
- API и orchestration: FastAPI, Python 3.11.
- История и реестр источников: PostgreSQL + SQLAlchemy.
- Векторное хранилище: Qdrant.
- Генерация: DeepSeek API (`deepseek-flash`) через OpenAI-compatible SDK.
- Dense embeddings: локальный FastEmbed `intfloat/multilingual-e5-large`, 1024 измерения, ONNX Runtime / CPU.
- Sparse retrieval: FastEmbed BM25 (Russian), локально.
- Hybrid retrieval: Qdrant prefetch + Reciprocal Rank Fusion.
- Ingestion: Scrapy, BeautifulSoup, lxml, pypdf, python-docx и Docling с RapidOCR.

Поток загрузки: материалы ЦФТ -> парсинг/очистка (Docling для OCR сканированных/смешанных PDF) -> chunking -> E5 passage embeddings + BM25 -> неактивные точки Qdrant -> переключение активной версии -> реестр PostgreSQL.

Поток запроса: пользователь -> Nginx -> FastAPI -> E5 query embeddings + BM25 -> Qdrant RRF -> DeepSeek с системной политикой и недоверенными фрагментами -> ответ с источниками.

Ollama не используется и не нужна: генерация выполняется через DeepSeek API. OpenCode — опциональный инструмент разработчика, он не входит в runtime и Docker. Docling находится только в ingestion-образе; backend API не загружает OCR-модели.


## Независимый запуск приложения и индексации

По умолчанию `docker compose up -d` запускает только постоянно работающие
PostgreSQL, Qdrant, Backend и Frontend. Сервис `ingestion` находится в профиле
`ingest` и запускается отдельно командой:

```bash
docker compose --profile ingest run --build --rm ingestion
```

Он ждёт готовности PostgreSQL и Qdrant, индексирует новые/изменённые документы
и завершается. Backend не зависит от `ingestion` и может обслуживать запросы
во время обновления базы. В этот момент запросы могут видеть старую версию
индекса до переключения активной версии документа. Первый запуск приложения
возможен до первичной индексации, но содержательных ответов по документам до
завершения первой загрузки ожидать не следует.


## Hardening в редакции 1.6

- Browser identity: случайный owner UUID подписывается HMAC-SHA256 на сервере; токен является bearer credential и не заменяет SSO.
- Retrieval: короткие follow-up вопросы дополняются недавними пользовательскими репликами; слабые RRF результаты отсеиваются стартовым порогом `RETRIEVAL_MIN_SCORE`.
- Chunking: сохраняются заголовки, абзацы и маркеры страниц; metadata обновляются даже при неизменном тексте.
- Citations: каждый уникальный источник получает номер; модель запрашивается ссылаться на `[n]`, backend удаляет несуществующие номера.
- Crawler: allowlist по умолчанию `cft.ru`, DNS/IP public-only validation и повторная проверка redirect targets.
- Chat: request idempotency и сериализация в рамках одной Backend-реплики; для нескольких реплик требуется общий lock.
- Session TTL и ограниченная выдача истории.
- Ограничение параллельных LLM-запросов и простой circuit breaker с cooldown; backup scripts для PostgreSQL и Qdrant. Compose не является HA-кластером и резервного LLM-провайдера нет.


## BM25 и Snowball (редакция 1.6.1)

Sparse-поиск использует `Qdrant/bm25` из FastEmbed с `language="russian"`.
FastEmbed автоматически применяет русский Snowball Stemmer и набор русских
стоп-слов как к индексируемым фрагментам, так и к поисковым запросам.
Для документов вызывается `embed()` (веса частоты терминов), для запросов —
`query_embed()` (веса поисковых терминов). Это разные операции.

Поскольку в 1.6 sparse-векторы документов были построены через query encoder,
1.6.1 использует отдельную коллекцию `knowledge_chunks_e5_large_v2`.
Требуется полная переиндексация. Старую коллекцию v1 можно оставить как
резервную до проверки новой.
