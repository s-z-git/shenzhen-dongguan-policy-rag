"""ingest 子包：正文抽取（loader）+ 条款切分（clause_splitter）+ 元数据（metadata）。"""
from .loader import clean_body, load_documents, split_front_matter
from .clause_splitter import chunk_body, split_at_level
from .metadata import build_chunks, build_corpus, citation, load_chunks, save_chunks, validate_chunks

__all__ = [
    "split_front_matter", "clean_body", "load_documents",
    "split_at_level", "chunk_body",
    "build_chunks", "build_corpus", "citation", "load_chunks", "save_chunks", "validate_chunks",
]