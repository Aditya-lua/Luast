import sys

sys.setrecursionlimit(max(sys.getrecursionlimit(), 200000))

from .pipeline import Pipeline, RunReport, process_file

__all__ = ["Pipeline", "RunReport", "process_file"]
