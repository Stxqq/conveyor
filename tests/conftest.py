import sys
from pathlib import Path

import pytest

from conveyor import Executor

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
if str(EXAMPLES) not in sys.path:
    sys.path.insert(0, str(EXAMPLES))


@pytest.fixture
def executor(tmp_path):
    ex = Executor(tmp_path / "ws", max_workers=8)
    yield ex
    ex.lineage.close()
