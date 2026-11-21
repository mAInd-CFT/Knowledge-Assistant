from pathlib import Path
from ingest.parsers.file_parser import parse_file


def parse_html(path: str | Path) -> tuple[str, str]:
    return parse_file(Path(path))
