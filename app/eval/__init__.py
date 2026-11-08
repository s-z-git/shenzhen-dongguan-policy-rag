"""eval 子包：metrics（指标 + bootstrap CI）+ ablations（消融实验）。"""
from .ablations import ABLATIONS, retrieval_records, run_ablation
from .answer_points import aggregate_answer_points, judge_records
from .metrics import (aggregate_generation, aggregate_grounding, aggregate_retrieval,
                      golden_ids, paired_bootstrap_diff)

__all__ = ["ABLATIONS", "retrieval_records", "run_ablation",
           "aggregate_retrieval", "aggregate_generation", "aggregate_grounding", "golden_ids",
           "paired_bootstrap_diff", "aggregate_answer_points", "judge_records"]