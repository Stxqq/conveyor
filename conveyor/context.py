from __future__ import annotations

import contextvars
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .events import EventLog
    from .registry import ModelRegistry

_current: contextvars.ContextVar[StepContext] = contextvars.ContextVar("conveyor_step")


@dataclass
class StepContext:
    run_id: str
    step: str
    attempt: int
    workspace: Path
    events: EventLog
    metrics: dict[str, float | None] = field(default_factory=dict)

    def log_metric(self, name: str, value: float) -> None:
        value = float(value)
        self.metrics[name] = value if math.isfinite(value) else None
        self.events.emit("metric", step=self.step, name=name, value=value)

    def log(self, message: str) -> None:
        self.events.emit("log", step=self.step, message=message)

    @property
    def registry(self) -> ModelRegistry:
        from .registry import ModelRegistry

        return ModelRegistry(self.workspace)


def current() -> StepContext:
    """The context of the step running on this thread."""
    try:
        return _current.get()
    except LookupError:
        raise RuntimeError("not inside a running conveyor step") from None


def log_metric(name: str, value: float) -> None:
    """Record a metric for the current step; it lands in lineage and the UI."""
    current().log_metric(name, value)


def log(message: str) -> None:
    current().log(message)
