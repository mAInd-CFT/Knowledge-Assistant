from app.retrieval.chunker import chunk_text


def test_chunk_text_empty():
    assert chunk_text("   ") == []


def test_chunk_text_overlaps_and_covers_text():
    text = "слово " * 1000
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert len(chunks) > 1
    assert all(chunks)
    assert "слово" in " ".join(chunks)


def test_chunker_preserves_headings_and_page_markers():
    text = "# Документ\n\nВведение.\n\n## Страница 2\n\n## Технологии\n\nJava и PostgreSQL."
    chunks = chunk_text(text, chunk_size=300, overlap=20)
    joined = "\n".join(chunks)
    assert "# Документ" in joined
    assert "## Страница 2" in joined
    assert "## Технологии" in joined
    assert "Java и PostgreSQL" in joined


def test_overlap_must_be_smaller_than_chunk_size():
    import pytest
    with pytest.raises(ValueError):
        chunk_text("hello", chunk_size=100, overlap=100)
