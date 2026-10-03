import pytest

from aranet_monitor import db


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def conn(db_path):
    c = db.connect(db_path)
    yield c
    c.close()
