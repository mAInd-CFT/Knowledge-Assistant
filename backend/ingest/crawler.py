"""Incremental, polite crawler for CFT and optionally external sources.

The crawler stores a persistent URL manifest with content hashes and HTTP validators.
Unchanged pages are not rewritten; ingestion independently skips unchanged content.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import scrapy
from scrapy.crawler import CrawlerProcess
from scrapy.linkextractors import LinkExtractor
from scrapy.http import HtmlResponse

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("/app/data/raw/crawled")
STATE_PATH = OUTPUT_DIR / ".crawl_state.json"
REPORT_PATH = Path("/app/data/processed/crawl_report.json")
SUPPORTED_FILE_EXTENSIONS = {".pdf", ".docx", ".html", ".htm", ".txt", ".md"}
CONTENT_TYPE_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/html": ".html",
    "text/plain": ".txt",
    "text/markdown": ".md",
}
DENIED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".css", ".js",
    ".mp3", ".mp4", ".avi", ".mov", ".zip", ".rar", ".7z", ".tar", ".gz",
    ".exe", ".dmg", ".iso", ".woff", ".woff2", ".ttf", ".eot",
}
DENIED_PATH = re.compile(
    r"/(?:search|login|log-in|logout|sign-in|sign-up|register|registration|"
    r"auth|oauth|account|profile|personal|cabinet|cart|checkout|password|"
    r"compare|favorites|favourites|wp-admin|wp-login(?:\.php)?|wp-json|"
    r"api|ajax|graphql|feed|rss|print)(?:/|$)", re.IGNORECASE,
)
DENIED_QUERY_KEYS = {
    "q", "query", "search", "filter", "filters", "sort", "order", "session",
    "sessionid", "sid", "token", "access_token", "page", "page_size", "pagesize",
    "limit", "offset", "sortby", "order_by", "utm_source", "utm_medium",
    "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid",
}


def allowed_domain(host: str, domains: set[str]) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _public_host(host: str) -> bool:
    """Reject private, loopback, link-local and reserved DNS destinations."""
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addresses = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, None)}
        except (OSError, ValueError):
            return False
    return bool(addresses) and all(address.is_global for address in addresses)


class SafeDomainMiddleware:
    """Validate every outgoing request, including redirect targets."""
    def __init__(self, domains: set[str]):
        self.domains = domains

    @classmethod
    def from_crawler(cls, crawler):
        raw = crawler.settings.get("CRAWL_ALLOWED_DOMAINS", "cft.ru")
        domains = {item.strip().lower().lstrip(".").rstrip(".") for item in raw.split(",") if item.strip()}
        return cls(domains or {"cft.ru"})

    def process_request(self, request, spider):
        parts = urlsplit(request.url)
        host = (parts.hostname or "").lower()
        if parts.scheme not in {"http", "https"} or not allowed_domain(host, self.domains):
            raise scrapy.exceptions.IgnoreRequest(f"URL outside CRAWL_ALLOWED_DOMAINS: {request.url}")
        if not _public_host(host):
            raise scrapy.exceptions.IgnoreRequest(f"Non-public or unresolved destination blocked: {host}")
        return None


def canonicalize_url(url: str) -> str | None:
    """Normalize URL enough to avoid fragment/tracking duplicates."""
    try:
        parts = urlsplit(url.strip())
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
        host = parts.hostname.lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
            return None
        try:
            address = ipaddress.ip_address(host)
            if not address.is_global:
                return None
        except ValueError:
            pass
        port = parts.port
        netloc = host if not port or (parts.scheme == "https" and port == 443) or (parts.scheme == "http" and port == 80) else f"{host}:{port}"
        query = [
            (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in DENIED_QUERY_KEYS and not key.lower().startswith("utm_")
        ]
        return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", urlencode(sorted(query)), ""))
    except ValueError:
        return None


def _load_state() -> dict:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"pages": {}}
    except (OSError, json.JSONDecodeError):
        return {"pages": {}}


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class CFTSpider(scrapy.Spider):
    name = "cft_incremental"

    def __init__(self, start_urls=None, max_pages=300, max_depth=2, *args, **kwargs):
        super().__init__(*args, **kwargs)
        seeds = [item.strip() for item in (start_urls or "https://www.cft.ru/").split(",") if item.strip()]
        raw_domains = os.getenv("CRAWL_ALLOWED_DOMAINS", "cft.ru")
        self.allowed_domains = {item.strip().lower().lstrip(".").rstrip(".") for item in raw_domains.split(",") if item.strip()}
        self.start_urls = [
            url for item in seeds
            if (url := canonicalize_url(item))
            and allowed_domain(urlsplit(url).hostname or "", self.allowed_domains)
            and _public_host(urlsplit(url).hostname or "")
        ]
        if not self.start_urls:
            raise ValueError("No valid CRAWL_START_URLS within CRAWL_ALLOWED_DOMAINS; default allowlist is cft.ru")
        self.max_pages = max(1, min(int(max_pages), 10000))
        self.max_depth = max(0, min(int(max_depth), 20))
        self.run_id = str(uuid.uuid4())
        self.state = _load_state()
        self.state.setdefault("pages", {})
        self.seen: set[str] = set()
        self.failed: set[str] = set()
        self.scheduled: set[str] = set()
        self.processed = 0
        self.changed = 0
        self.unchanged = 0
        self.files_downloaded = 0
        self.skipped = 0
        self.link_extractor = LinkExtractor(
            deny_extensions=sorted(DENIED_EXTENSIONS),
            unique=True,
            canonicalize=True,
        )

    def start_requests(self):
        for url in self.start_urls:
            if len(self.scheduled) >= self.max_pages:
                break
            self.scheduled.add(url)
            yield self._request(url, depth=0)

    def _request(self, url: str, depth: int):
        headers = {}
        previous = self.state["pages"].get(url, {})
        previous_file = OUTPUT_DIR / previous.get("file_path", "__missing__")
        # Do not ask for 304 unless we still have a local copy to reuse.
        if previous_file.is_file() and previous.get("etag"):
            headers["If-None-Match"] = previous["etag"]
        if previous_file.is_file() and previous.get("last_modified"):
            headers["If-Modified-Since"] = previous["last_modified"]
        return scrapy.Request(
            url,
            callback=self.parse,
            errback=self.request_failed,
            headers=headers,
            meta={"depth": depth, "handle_httpstatus_list": [304]},
        )

    def _allowed_url(self, url: str) -> str | None:
        normalized = canonicalize_url(url)
        if not normalized:
            return None
        parts = urlsplit(normalized)
        if not allowed_domain(parts.hostname or "", self.allowed_domains):
            return None
        if not _public_host(parts.hostname or ""):
            return None
        if DENIED_PATH.search(parts.path):
            return None
        suffix = Path(parts.path).suffix.lower()
        if suffix in DENIED_EXTENSIONS:
            return None
        return normalized

    def _write_sidecar(self, target: Path, metadata: dict) -> None:
        _write_json(target.with_name(target.name + ".source.json"), metadata)

    def _save_document(self, response, url: str, title: str, content: bytes, extension: str, content_type: str):
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        target = OUTPUT_DIR / f"{digest}{extension}"
        content_hash = hashlib.sha256(content).hexdigest()
        previous = self.state["pages"].get(url, {})
        etag = response.headers.get(b"ETag", b"").decode("latin1") or previous.get("etag")
        last_modified = response.headers.get(b"Last-Modified", b"").decode("latin1") or previous.get("last_modified")
        metadata = {
            "url": url,
            "title": title,
            "content_type": content_type,
            "content_sha256": content_hash,
            "etag": etag,
            "last_modified": last_modified,
            "last_seen_run": self.run_id,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "kind": "download" if extension != ".txt" or content_type.startswith("text/plain") else "page",
        }
        if previous.get("content_sha256") == content_hash and target.exists():
            self.unchanged += 1
        else:
            target.write_bytes(content)
            self.changed += 1
            if extension in SUPPORTED_FILE_EXTENSIONS and extension not in {".html", ".htm", ".txt", ".md"}:
                self.files_downloaded += 1
        self._write_sidecar(target, metadata)
        self.state["pages"][url] = {
            **metadata,
            "file_path": target.name,
        }

    def parse(self, response):
        url = canonicalize_url(response.url)
        if not url:
            self.skipped += 1
            return
        self.seen.add(url)
        if response.status >= 400:
            self.failed.add(url)
            self.skipped += 1
            logger.warning("HTTP %s for %s", response.status, url)
            return
        if response.status == 304:
            previous = self.state["pages"].get(url, {})
            previous["last_seen_run"] = self.run_id
            previous["retrieved_at"] = datetime.now(timezone.utc).isoformat()
            self.state["pages"][url] = previous
            self.unchanged += 1
            self.processed += 1
            logger.info("UNCHANGED (HTTP 304) %s", url)
            return

        content_type = response.headers.get(b"Content-Type", b"").decode("latin1").split(";", 1)[0].strip().lower()
        suffix = Path(urlsplit(url).path).suffix.lower()
        extension = suffix if suffix in SUPPORTED_FILE_EXTENSIONS else CONTENT_TYPE_EXTENSIONS.get(content_type)

        if extension in SUPPORTED_FILE_EXTENSIONS and extension not in {".html", ".htm"} and content_type != "text/html":
            title = urlsplit(url).path.rsplit("/", 1)[-1] or url
            self._save_document(response, url, title, response.body, extension, content_type)
            self.processed += 1
            logger.info("DOCUMENT %s (%s)", url, extension)
            return

        is_html = "html" in content_type or suffix in {".html", ".htm"}
        if not is_html:
            self.skipped += 1
            logger.info("SKIP unsupported content type %s %s", content_type, url)
            return
        # Some servers label HTML downloads as application/octet-stream; make
        # the response text-aware before using Scrapy's CSS/XPath selectors.
        if not hasattr(response, "css"):
            response = HtmlResponse(
                url=response.url,
                body=response.body,
                encoding="utf-8",
                request=response.request,
            )

        title = (response.css("title::text").get() or url).strip()
        published_at = (
            response.css('meta[property="article:published_time"]::attr(content)').get()
            or response.css('meta[name="date"]::attr(content)').get()
            or response.css('time[datetime]::attr(datetime)').get()
            or ""
        )
        content_root = response.xpath("(//main | //article)[1]") or response.xpath("//body")
        blocks = content_root.xpath(
            ".//h1 | .//h2 | .//h3 | .//h4 | .//h5 | .//h6 | .//p | .//li | .//blockquote | .//dt | .//dd | .//tr"
        )
        structured: list[str] = []
        for node in blocks:
            tag = (node.root.tag or "").lower() if hasattr(node, "root") else ""
            if not isinstance(tag, str):
                tag = ""
            if tag == "tr":
                value = " | ".join(part.strip() for part in node.xpath("./th//text() | ./td//text()").getall() if part.strip())
            else:
                value = " ".join(part.strip() for part in node.xpath(".//text()[not(ancestor::script) and not(ancestor::style)]").getall() if part.strip())
            value = re.sub(r"\s+", " ", value).strip()
            if not value:
                continue
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                value = "## " + value
            elif tag == "li":
                value = "- " + value
            if not structured or structured[-1] != value:
                structured.append(value)
        if not structured:
            text_nodes = content_root.xpath(
                ".//text()[not(ancestor::script) and not(ancestor::style) and not(ancestor::nav) "
                "and not(ancestor::footer) and not(ancestor::header)]"
            ).getall()
            structured = [re.sub(r"\s+", " ", part).strip() for part in text_nodes if part.strip()]
        text = "\n\n".join(structured)
        payload = f"URL: {url}\nTITLE: {title}\nPUBLISHED_AT: {published_at}\n\n{text}".encode("utf-8")
        self._save_document(response, url, title, payload, ".txt", "text/plain")
        self.processed += 1
        logger.info("PAGE %s", url)

        depth = int(response.meta.get("depth", 0))
        if depth >= self.max_depth or len(self.scheduled) >= self.max_pages:
            return
        for link in self.link_extractor.extract_links(response):
            target = self._allowed_url(urljoin(response.url, link.url))
            if not target or target in self.scheduled or target in self.state["pages"] and target in self.seen:
                continue
            if len(self.scheduled) >= self.max_pages:
                break
            self.scheduled.add(target)
            yield self._request(target, depth + 1)

    def request_failed(self, failure):
        request_url = canonicalize_url(failure.request.url)
        if request_url:
            self.failed.add(request_url)
        logger.warning("Request failed: %s", failure.getErrorMessage())

    def closed(self, reason):
        now = datetime.now(timezone.utc).isoformat()
        pages = self.state.get("pages", {})
        previous_run = self.state.get("last_run", {})
        # Only compare inventories when both runs exhausted the same depth/seeds without failures.
        inventory_complete = reason == "finished" and len(self.scheduled) < self.max_pages and not self.failed
        same_inventory_policy = (
            previous_run.get("complete_inventory") is True
            and previous_run.get("max_depth") == self.max_depth
            and previous_run.get("start_urls") == self.start_urls
        )
        stale = [
            url for url, item in pages.items()
            if item.get("last_seen_run") != self.run_id and url not in self.failed
        ] if inventory_complete and same_inventory_policy else []
        for url in stale:
            pages[url]["possibly_missing_since"] = pages[url].get("possibly_missing_since") or now
        for url in self.seen:
            if url in pages:
                pages[url].pop("possibly_missing_since", None)
        self.state["last_run"] = {
            "run_id": self.run_id,
            "finished_at": now,
            "reason": reason,
            "scheduled": len(self.scheduled),
            "processed": self.processed,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "files_downloaded": self.files_downloaded,
            "failed": len(self.failed),
            "possibly_missing": len(stale),
            "max_depth": self.max_depth,
            "start_urls": self.start_urls,
            "complete_inventory": inventory_complete,
        }
        _write_json(STATE_PATH, self.state)
        _write_json(REPORT_PATH, {
            **self.state["last_run"],
            "max_pages": self.max_pages,
            "max_depth": self.max_depth,
            "stale_urls": stale,
            "failed_urls": sorted(self.failed),
            "note": "Possibly missing URLs are reported only; they are not deleted from Qdrant automatically.",
        })
        logger.info(
            "Crawl finished: processed=%s changed=%s unchanged=%s files=%s failed=%s possibly_missing=%s",
            self.processed, self.changed, self.unchanged, self.files_downloaded, len(self.failed), len(stale),
        )


def main():
    seeds = os.getenv("CRAWL_START_URLS", "https://www.cft.ru/")
    process = CrawlerProcess(settings={
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_TIMEOUT": int(os.getenv("CRAWL_DOWNLOAD_TIMEOUT", "30")),
        "DOWNLOAD_MAXSIZE": int(os.getenv("CRAWL_MAX_DOWNLOAD_MB", "25")) * 1024 * 1024,
        "CONCURRENT_REQUESTS": int(os.getenv("CRAWL_CONCURRENT_REQUESTS", "6")),
        "CONCURRENT_REQUESTS_PER_DOMAIN": int(os.getenv("CRAWL_CONCURRENT_PER_DOMAIN", "2")),
        "DOWNLOAD_DELAY": float(os.getenv("CRAWL_DOWNLOAD_DELAY", "1.0")),
        "RANDOMIZE_DOWNLOAD_DELAY": True,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 3,
        "RETRY_HTTP_CODES": [408, 429, 500, 502, 503, 504],
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 1.0,
        "AUTOTHROTTLE_MAX_DELAY": 30.0,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.0,
        "DEPTH_LIMIT": int(os.getenv("CRAWL_MAX_DEPTH", "2")),
        "DEPTH_PRIORITY": 1,
        "TELNETCONSOLE_ENABLED": False,
        "LOG_LEVEL": os.getenv("CRAWL_LOG_LEVEL", "INFO"),
        "USER_AGENT": os.getenv("CRAWL_USER_AGENT", "CFTKnowledgeAssistantCrawler/1.0 (+respect robots.txt)"),
        "CRAWL_ALLOWED_DOMAINS": os.getenv("CRAWL_ALLOWED_DOMAINS", "cft.ru"),
        "DOWNLOADER_MIDDLEWARES": {
            "ingest.crawler.SafeDomainMiddleware": 50,
        },
    })
    process.crawl(
        CFTSpider,
        start_urls=seeds,
        max_pages=os.getenv("CRAWL_MAX_PAGES", "300"),
        max_depth=os.getenv("CRAWL_MAX_DEPTH", "2"),
    )
    process.start()


if __name__ == "__main__":
    main()
