"""Structure-aware text chunking that preserves headings and page boundaries."""
from __future__ import annotations

import re


def _clean_block(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in (value or "").splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _split_oversized(paragraph: str, chunk_size: int, overlap: int) -> list[str]:
    result: list[str] = []
    remaining = paragraph
    while len(remaining) > chunk_size:
        boundary = max(
            remaining.rfind(". ", 0, chunk_size),
            remaining.rfind("; ", 0, chunk_size),
            remaining.rfind(" ", 0, chunk_size),
        )
        if boundary < chunk_size // 2:
            boundary = chunk_size
        piece = remaining[:boundary].strip()
        if piece:
            result.append(piece)
        next_start = max(1, boundary - overlap)
        remaining = remaining[next_start:].strip()
    if remaining:
        result.append(remaining)
    return result


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 180) -> list[str]:
    if chunk_size < 100:
        raise ValueError("chunk_size must be at least 100")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be non-negative and smaller than chunk_size")

    source = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not source:
        return []

    sections: list[str] = []
    current: list[str] = []
    for line in source.splitlines():
        if re.match(r"^#{1,6}\s+", line) and current:
            block = _clean_block("\n".join(current))
            if block:
                sections.append(block)
            current = [line]
        else:
            current.append(line)
    block = _clean_block("\n".join(current))
    if block:
        sections.append(block)

    chunks: list[str] = []
    for section in sections:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", section) if p.strip()]
        current_text = ""
        for paragraph in paragraphs:
            if len(paragraph) > chunk_size:
                if current_text:
                    chunks.append(current_text.strip())
                    current_text = ""
                chunks.extend(_split_oversized(paragraph, chunk_size, overlap))
                continue
            candidate = f"{current_text}\n\n{paragraph}" if current_text else paragraph
            if len(candidate) <= chunk_size:
                current_text = candidate
            else:
                if current_text:
                    chunks.append(current_text.strip())
                tail = current_text[-overlap:].strip() if current_text and overlap else ""
                current_text = f"{tail}\n\n{paragraph}" if tail else paragraph
                if len(current_text) > chunk_size:
                    split = _split_oversized(current_text, chunk_size, overlap)
                    chunks.extend(split[:-1])
                    current_text = split[-1] if split else ""
        if current_text:
            chunks.append(current_text.strip())

    return [item for item in chunks if item]
