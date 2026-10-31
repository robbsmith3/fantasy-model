import pytest

from fantasy_model import config, db


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / "secrets")
    c = db.connect(tmp_path / "test.duckdb")
    yield c
    c.close()


class FakeResp:
    def __init__(self, body, url, status=200):
        self.body, self.url, self.status_code, self.ok = body, url, status, status < 400

    def json(self):
        return self.body

    def raise_for_status(self):
        if not self.ok:
            import requests

            raise requests.HTTPError(self.status_code)
