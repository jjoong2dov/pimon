import pytest

from fake.make_fake import make


@pytest.fixture
def fake_pi(tmp_path):
    """시험마다 정상 기본값으로 새로 만든 가짜 파이 폴더."""
    return make(tmp_path / "pi", fresh=True)
