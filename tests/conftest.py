import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "fake"))

import make_fake  # noqa: E402


@pytest.fixture
def pi(tmp_path):
    """정상 상태의 가짜 파이 폴더."""
    return make_fake.make(tmp_path / "pi", fresh=True)
