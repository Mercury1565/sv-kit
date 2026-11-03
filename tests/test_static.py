"""The built web UI (committed under src/svkit/server/static) is served by create_app."""
import re

from fastapi.testclient import TestClient

from svkit import config as C
from svkit.server.app import STATIC, create_app


def _client(tmp_path):
    (tmp_path / "svkit.toml").write_text('[project]\nname="demo"\ntop="core"\n')
    return TestClient(create_app(C.load(tmp_path)))


def test_index_served_at_root(tmp_path):
    assert (STATIC / "index.html").is_file(), "run `npm run build` in web/"
    c = _client(tmp_path)
    r = c.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert '<div id="root"></div>' in r.text
    # every asset the page references is served too
    assets = re.findall(r'(?:src|href)="\./(assets/[^"]+)"', r.text)
    assert assets, "index.html references no bundled assets"
    for a in assets:
        ra = c.get("/" + a)
        assert ra.status_code == 200, a
        assert len(ra.content) > 0


def test_spa_fallback_and_api_untouched(tmp_path):
    c = _client(tmp_path)
    # client-side routes are hash based, but any unknown path still gets the app shell
    assert '<div id="root"></div>' in c.get("/runs/r_0001").text
    assert c.get("/api/project").json()["name"] == "demo"
    assert c.get("/api/nope").status_code == 404


def test_static_bundle_is_lean():
    js = list((STATIC / "assets").glob("index-*.js"))
    assert len(js) == 1
    # main entry chunk; echarts / elkjs / views are lazy chunks
    assert js[0].stat().st_size < 400_000
