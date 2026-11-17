"""generate 子包：query_analysis（条件抽取+子问题）+ prompt + generator（DeepSeek/引用/时效）。"""
from .generator import (REFUSAL, answer, build_answer_prompt, cited_doc_ids, get_llm,
                        staleness_notes, stream_answer, used_citations)
from .prompt import ANSWER_PROMPT, format_context
from .query_analysis import (analyze_query, city_scope, filters_from_analysis,
                             rewrite_query)
from .verify import build_doc_clause_index, check_grounding

__all__ = ["get_llm", "answer", "build_answer_prompt", "stream_answer",
           "staleness_notes", "used_citations", "cited_doc_ids", "REFUSAL",
           "ANSWER_PROMPT", "format_context", "analyze_query", "filters_from_analysis",
           "rewrite_query", "check_grounding", "build_doc_clause_index", "city_scope"]
