"""SPIKE ABSA 包：情感属性抽取（对应规格 §8.2 / §9）。"""

from .model_adapter import extract_aspects, model_status  # noqa: F401

__all__ = ["extract_aspects", "model_status"]
