"""A small pipeline engine for ML."""

from .context import current, log, log_metric
from .dag import Pipeline, PipelineError
from .executor import Executor, RunResult, StepOutcome, StepTimeout
from .hashing import digest
from .step import Step, step

__all__ = [
    "Executor",
    "Pipeline",
    "PipelineError",
    "RunResult",
    "Step",
    "StepOutcome",
    "StepTimeout",
    "current",
    "digest",
    "log",
    "log_metric",
    "run",
    "step",
]
__version__ = "0.1.0"


def run(pipeline: Pipeline, params: dict | None = None, **options) -> RunResult:
    """Run a pipeline with a default executor rooted at ``./.conveyor``."""
    workspace = options.pop("workspace", ".conveyor")
    return Executor(workspace, **options).run(pipeline, params)
