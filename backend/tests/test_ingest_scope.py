from ingest.run import is_cft_source


def test_only_official_cft_https_pages_are_accepted():
    assert is_cft_source("https://www.cft.ru/products", "page.txt", "Продукты", "Описание")
    assert is_cft_source("https://docs.cft.ru/help", "page.txt", "Документация", "Описание")
    assert not is_cft_source("http://www.cft.ru/", "page.txt", "ЦФТ", "Описание")
    assert not is_cft_source("https://example.com/", "page.txt", "ЦФТ", "Описание")


def test_local_document_requires_cft_marker():
    assert is_cft_source("file:///app/data/raw/cft.docx", "ЦФТ.docx", "Справка", "Текст")
    assert is_cft_source("file:///app/data/raw/manual.docx", "manual.docx", "Справка ЦФТ", "Текст")
    assert not is_cft_source("file:///app/data/raw/other.docx", "other.docx", "Справка", "Обычный текст")


def test_explicitly_approved_local_document_is_accepted_without_marker():
    assert is_cft_source("file:///app/data/raw/approved/manual.pdf", "approved/manual.pdf", "Руководство", "Текст без названия компании")
