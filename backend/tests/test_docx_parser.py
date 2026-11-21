from docx import Document as DocxDocument

from ingest.parsers.file_parser import parse_file


def test_docx_parser_keeps_headings_and_tables(tmp_path):
    path = tmp_path / "ЦФТ справка.docx"
    document = DocxDocument()
    document.add_paragraph("Информация о компании ЦФТ")
    document.add_heading("Продукты", level=1)
    document.add_paragraph("ЦФТ-Банк автоматизирует банковские процессы.")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Продукт"
    table.cell(0, 1).text = "Назначение"
    document.save(path)

    title, text = parse_file(path)
    assert title.startswith("Информация о компании")
    assert "## Продукты" in text
    assert "ЦФТ-Банк" in text
    assert "Продукт | Назначение" in text
