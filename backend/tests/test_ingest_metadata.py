from pathlib import Path
import json

from ingest.run import load_source_metadata


def test_local_file_without_sidecar_has_safe_defaults(tmp_path: Path):
    source = tmp_path / "brief.docx"
    source.write_bytes(b"dummy")
    uri, title, published_at, retrieved_at = load_source_metadata(source, "Brief")
    assert uri == source.resolve().as_uri()
    assert title == "Brief"
    assert published_at is None
    assert retrieved_at is None


def test_sidecar_metadata_is_loaded_without_self_reference(tmp_path: Path):
    source = tmp_path / "page.html"
    source.write_text("<p>text</p>", encoding="utf-8")
    sidecar = source.with_name(source.name + ".source.json")
    sidecar.write_text(json.dumps({
        "url": "https://www.cft.ru/about",
        "title": "О компании",
        "published_at": "2026-01-01",
        "retrieved_at": "2026-11-21T12:00:00+00:00",
    }, ensure_ascii=False), encoding="utf-8")
    uri, title, published_at, retrieved_at = load_source_metadata(source, "Fallback")
    assert uri == "https://www.cft.ru/about"
    assert title == "О компании"
    assert published_at == "2026-01-01"
    assert retrieved_at == "2026-11-21T12:00:00+00:00"


def test_sidecar_without_retrieved_at_is_safe(tmp_path: Path):
    source = tmp_path / "page.html"
    source.write_text("<p>text</p>", encoding="utf-8")
    source.with_name(source.name + ".source.json").write_text(
        '{"url":"https://www.cft.ru/"}', encoding="utf-8"
    )
    _, _, _, retrieved_at = load_source_metadata(source, "Fallback")
    assert retrieved_at is None
