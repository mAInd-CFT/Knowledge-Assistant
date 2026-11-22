import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.db.postgres import init_db
from ingest.loaders.qdrant_loader import index_document
from ingest.parsers.file_parser import parse_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("ingest")


def _as_str(value) -> str:
    return "" if value is None else str(value)


RAW_DIR = Path("/app/data/raw")
REPORT_PATH = Path("/app/data/processed/ingestion_report.json")
SUPPORTED = {".pdf", ".docx", ".html", ".htm", ".txt", ".md", ".jsonl"}
CFT_MARKERS = (
    "цфт", "центр финансовых технологий", "cft.ru", "cft-bank",
    "cft-service suite", "цфт-банк", "цфт-ритейл",
)


def is_cft_source(source_uri: str, source_file: str, title: str, text: str) -> bool:
    parsed = urlparse(source_uri)
    if parsed.scheme in {"http", "https"}:
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "https" and (host == "cft.ru" or host.endswith(".cft.ru")):
            return True
        normalized_crawl_path = source_file.replace("\\", "/").lstrip("/")
        approved = {
            item.strip().lower().lstrip(".")
            for item in os.getenv("CRAWL_APPROVED_EXTERNAL_DOMAINS", "").split(",")
            if item.strip()
        }
        return normalized_crawl_path.startswith("crawled/") and any(
            host == domain or host.endswith("." + domain) for domain in approved
        )
    normalized_path = source_file.replace("\\", "/").lstrip("/")
    if normalized_path.startswith("approved/"):
        return True
    searchable = " ".join((source_file, title, text)).lower()
    return any(marker in searchable for marker in CFT_MARKERS)


def load_source_metadata(path: Path, default_title: str) -> tuple[str, str, str | None, str | None]:
    """Load optional crawler metadata; local files work without a sidecar."""
    source_uri = path.resolve().as_uri()
    title = default_title
    published_at = None
    retrieved_at = None
    sidecar = path.with_name(path.name + ".source.json")
    if sidecar.exists():
        try:
            source_metadata = json.loads(sidecar.read_text(encoding="utf-8"))
            source_uri = source_metadata.get("url") or source_uri
            title = source_metadata.get("title") or title
            published_at = source_metadata.get("published_at") or None
            retrieved_at = source_metadata.get("retrieved_at") or None
        except (OSError, json.JSONDecodeError):
            logger.warning("Invalid crawler metadata sidecar: %s", sidecar)
    return source_uri, title, published_at, retrieved_at


def _ingest_jsonl(path: Path, report: dict) -> None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                record = None
                record_id = None
                try:
                    record = json.loads(line)
                    report["records_found"] += 1
                    record_id = record.get("id")
                    text_raw = (record.get("text") or "").strip()
                    if not text_raw:
                        logger.info("Skipped empty-text JSONL record %s in %s", record_id, path.name)
                        continue
                    url = record.get("url") or ""
                    parsed = urlparse(url)
                    host = (parsed.hostname or "").lower()
                    if not (parsed.scheme == "https" and (host == "cft.ru" or host.endswith(".cft.ru"))):
                        report["documents_skipped_out_of_scope"] += 1
                        logger.warning("Skipped out-of-scope JSONL record %s in %s: %s", record_id, path.name, url)
                        continue
                    title = record.get("title") or ""
                    parts = [text_raw]
                    interface = (record.get("interface") or "").strip()
                    architecture = (record.get("architecture") or "").strip()
                    if interface:
                        parts.append(interface)
                    if architecture:
                        parts.append(architecture)
                    text = "\n".join(parts)
                    metadata = {
                        "product_version": _as_str(record.get("version")),
                        "code": _as_str(record.get("code")),
                        "item_type": _as_str(record.get("type")),
                        "catalog": _as_str(record.get("catalog")),
                    }
                    result = index_document(url, title, text, source_file="", published_at=None, retrieved_at=None, metadata=metadata, dedupe_content=False)
                    report["documents_indexed"] += 1
                    report["chunks_indexed"] += result.get("chunks", 0)
                    report["text_characters"] += len(text)
                    report["documents"].append({
                        "file": path.name,
                        "record_id": record_id,
                        "title": title,
                        "source": url,
                        "characters": len(text),
                        **result,
                    })
                    logger.info("Indexed JSONL record %s in %s: %s", record_id, path.name, result)
                except Exception as exc:
                    logger.exception("Failed to ingest JSONL record at line %s in %s", line_number, path.name)
                    report["failures"].append({"file": path.name, "record_id": record_id, "error": str(exc)})
    except Exception as exc:
        logger.exception("Failed to ingest %s", path)
        report["failures"].append({"file": path.name, "error": str(exc)})


def main():
    init_db()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(
        path for path in RAW_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "crawler_allowlist": os.getenv("CRAWL_ALLOWED_DOMAINS", "cft.ru"),
        "approved_external_domains": os.getenv("CRAWL_APPROVED_EXTERNAL_DOMAINS", ""),
        "scope": "АО «Центр Финансовых Технологий» (ЦФТ)",
        "supported_formats": sorted(SUPPORTED),
        "documents_found": len(files),
        "documents_indexed": 0,
        "records_found": 0,
        "documents_skipped_out_of_scope": 0,
        "documents_unchanged": 0,
        "documents_deduplicated": 0,
        "documents_changed": 0,
        "chunks_indexed": 0,
        "text_characters": 0,
        "thematic_sections": {},
        "failures": [],
        "documents": [],
    }
    for path in files:
        if path.suffix.lower() == ".jsonl":
            _ingest_jsonl(path, report)
            continue
        try:
            title, text = parse_file(path)
            source_uri, title, published_at, retrieved_at = load_source_metadata(path, title)
            if text.startswith("URL: "):
                lines = text.splitlines()
                source_uri = lines[0][5:].strip()
                if len(lines) > 1 and lines[1].startswith("TITLE: "):
                    title = lines[1][7:].strip() or title
                content_lines = lines[2:]
                if content_lines and content_lines[0].startswith("PUBLISHED_AT: "):
                    published_at = content_lines[0][14:].strip() or None
                    content_lines = content_lines[1:]
                text = "\n".join(content_lines).strip()
            elif text.startswith("PUBLISHED_AT: "):
                lines = text.splitlines()
                published_at = lines[0][14:].strip() or None
                text = "\n".join(lines[1:]).strip()
            relative_source_file = str(path.relative_to(RAW_DIR))
            if not is_cft_source(source_uri, relative_source_file, title, text):
                report["documents_skipped_out_of_scope"] += 1
                logger.warning("Skipped out-of-scope source: %s", relative_source_file)
                continue
            result = index_document(
                source_uri, title, text,
                source_file=relative_source_file,
                published_at=published_at,
                retrieved_at=retrieved_at,
            )
            chars = len(text)
            for section in re.findall(r"(?m)^##\s+(.+)$", text):
                section = section.strip()
                if section:
                    report["thematic_sections"][section] = report["thematic_sections"].get(section, 0) + 1
            if result.get("duplicate"):
                report["documents_deduplicated"] += 1
                logger.info("Skipped duplicate %s; canonical source: %s", source_uri, result.get("duplicate_of"))
            else:
                report["documents_indexed"] += 1
                if result.get("unchanged"):
                    report["documents_unchanged"] += 1
                else:
                    report["documents_changed"] += 1
            report["chunks_indexed"] += result.get("chunks", 0)
            report["text_characters"] += chars
            report["documents"].append({
                "file": path.name,
                "title": title,
                "source": source_uri,
                "characters": chars,
                **result,
            })
            logger.info("Indexed %s: %s", path.name, result)
        except Exception as exc:
            logger.exception("Failed to ingest %s", path)
            report["failures"].append({"file": path.name, "error": str(exc)})
    report["processing_errors"] = len(report["failures"])
    report["unique_documents"] = report["documents_indexed"]
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Ingestion report: %s", REPORT_PATH)
    if report["failures"]:
        raise RuntimeError(f"Failed to ingest {len(report['failures'])} files; see ingestion report")


if __name__ == "__main__":
    main()
