from pathlib import Path

from bs4 import BeautifulSoup
from docx import Document as DocxDocument
from pypdf import PdfReader


def _docx_to_text(path: Path) -> str:
    doc = DocxDocument(str(path))
    blocks: list[str] = []
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = (paragraph.style.name or "").lower() if paragraph.style else ""
        if "heading" in style or "заголовок" in style:
            blocks.append(f"\n## {text}\n")
        else:
            blocks.append(text)

    for table_index, table in enumerate(doc.tables, start=1):
        rows = []
        for row in table.rows:
            cells = [" ".join(cell.text.split()) for cell in row.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            blocks.append(f"\nТаблица {table_index}:\n" + "\n".join(rows))
    return "\n\n".join(blocks)


def _docling_to_markdown(path: Path) -> str:
    """Layout-aware conversion for DOCX and scanned/mixed PDFs."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    # CFT source documents are predominantly Russian. Docling's RapidOCR
    # default language is Chinese ("ch"), which requests unrelated PP-OCRv6
    # checkpoints. Explicitly select the Cyrillic recognizer instead.
    pdf_options = PdfPipelineOptions(
        ocr_options=RapidOcrOptions(lang=["cyrillic"]),
    )
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options),
        }
    )
    result = converter.convert(str(path))
    try:
        return result.document.export_to_markdown()
    finally:
        try:
            result.input.release()
        except Exception:
            pass


def parse_file(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(str(path))
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"\n## Страница {number}\n{text}")
        extracted = "\n\n".join(pages)
        # Fast path for ordinary text PDFs; Docling handles scanned/mixed PDFs.
        if extracted.strip() and len(pages) == len(reader.pages):
            return path.stem, extracted
        try:
            converted = _docling_to_markdown(path)
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.startswith("docling"):
                raise ValueError(
                    f"PDF {path.name} appears to be scanned or mixed. "
                    "Run indexing with the OCR image: "
                    "docker compose --profile ingest-ocr run --build --rm ingestion-ocr"
                ) from exc
            raise
        except Exception as exc:
            raise ValueError(f"Docling could not parse PDF {path.name}: {exc}") from exc
        if not converted.strip():
            raise ValueError(f"Docling returned no text for PDF: {path.name}")
        return path.stem, converted

    if suffix == ".docx":
        # python-docx reliably preserves paragraph styles and tables for DOCX.
        doc = DocxDocument(str(path))
        first_text = next((item.text.strip() for item in doc.paragraphs if item.text.strip()), "")
        title = first_text if first_text and len(first_text) <= 220 else path.stem
        return title, _docx_to_text(path)

    if suffix in {".html", ".htm"}:
        soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "lxml")
        title = soup.title.get_text(" ", strip=True) if soup.title else path.stem
        published_at = (
            soup.select_one('meta[property="article:published_time"]')
            or soup.select_one('meta[name="date"]')
            or soup.select_one('meta[name="publish-date"]')
        )
        published_value = published_at.get("content", "").strip() if published_at else ""
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "form"]):
            tag.decompose()
        for selector in ["[role=navigation]", ".cookie", ".cookies", ".menu", ".breadcrumb"]:
            for node in soup.select(selector):
                node.decompose()
        for heading in soup.find_all(["h1", "h2", "h3"]):
            heading_text = heading.get_text(" ", strip=True)
            heading.clear()
            heading.append(f"## {heading_text}")
        extracted = soup.get_text("\n", strip=True)
        if published_value:
            extracted = f"PUBLISHED_AT: {published_value}\n" + extracted
        return title, extracted

    if suffix in {".txt", ".md"}:
        return path.stem, path.read_text(encoding="utf-8", errors="ignore")
    raise ValueError(f"Unsupported file format: {suffix}")
