import time

from fastapi.testclient import TestClient

from fantasy_model import api


def test_async_cache_reports_computing_then_ready_and_keeps_errors():
    c = api.Cache(workers=1)
    assert c.get_async(("k",), lambda: (time.sleep(0.2), 42)[1])["status"] == "computing"
    for _ in range(50):
        r = c.get_async(("k",), lambda: 0)
        if r["status"] == "ready":
            break
        time.sleep(0.05)
    assert r == {"status": "ready", "data": 42}
    c.get_async(("bad",), lambda: 1 / 0)
    time.sleep(0.2)
    assert c.get_async(("bad",), lambda: 0)["status"] == "error"
    c.clear_errors()
    assert c.get_async(("bad",), lambda: 1)["status"] in {"computing", "ready"}


def test_spa_serves_assets_and_falls_back_to_index(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(api, "WEB_DIST", tmp_path)
    client = TestClient(api.create_app())
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert "root" in client.get("/waivers").text  # client-side route
    assert "root" in client.get("/../../etc/passwd").text  # no escaping the dist folder
