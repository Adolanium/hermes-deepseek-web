"""DeepSeek provider contracts, exercised through Hermes' real plugin discovery."""

import asyncio
import json
import shutil
from pathlib import Path

import httpx
import pytest

PLUGIN = "hermes-deepseek-web"
KEY = "sk-deepseek-test-0123456789"


def install(tmp_path, monkeypatch, web=None):
    """Install this checkout as a user plugin in a throwaway profile and discover it."""
    home = tmp_path / "profile"
    plugin_dir = home / "plugins" / PLUGIN
    plugin_dir.mkdir(parents=True)
    root = Path(__file__).resolve().parents[1]
    for filename in ("plugin.yaml", "__init__.py", "provider.py"):
        shutil.copy2(root / filename, plugin_dir / filename)
    config = {"plugins": {"enabled": [PLUGIN]}}
    if web:
        config["web"] = web
    # JSON is valid YAML; Hermes does not ship PyYAML.
    (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(tmp_path / "empty-bundled"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from hermes_cli.plugins import _ensure_plugins_discovered
    from agent.web_search_registry import get_provider

    _ensure_plugins_discovered(force=True)
    return get_provider("deepseek")


@pytest.fixture
def provider(tmp_path, monkeypatch):
    found = install(tmp_path, monkeypatch)
    assert found is not None, "the installed plugin must register the 'deepseek' provider"
    return found


@pytest.fixture
def keyed(provider, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
    return provider


@pytest.fixture
def requests(monkeypatch):
    """Route every httpx client through ``handler``; record what was sent."""
    seen = []
    state = {"handler": None}

    def dispatch(request):
        seen.append(request)
        return state["handler"](request)

    mock = httpx.MockTransport(dispatch)
    sync_client, async_client = httpx.Client, httpx.AsyncClient
    monkeypatch.setattr(httpx, "Client", lambda **kw: sync_client(transport=mock, **kw))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: async_client(transport=mock, **kw))

    def use(handler):
        state["handler"] = handler
        return seen

    return use


def fetch_ok(request):
    url = json.loads(request.content)["url"]
    return httpx.Response(200, json={"title": f"Title of {url}", "content": f"# Page\n\nBody of {url}"})


def deepseek_error(status, message):
    return httpx.Response(status, json={"error": {
        "message": message, "type": "invalid_request_error", "param": None, "code": "invalid_request_error",
    }})


def extract(provider, urls):
    return asyncio.run(provider.extract(urls))


def test_without_a_key_deepseek_is_never_auto_selected(provider):
    from agent.web_search_registry import get_active_extract_provider, get_active_search_provider

    assert not provider.is_available()
    assert not provider.is_keyless_available()
    assert get_active_extract_provider() is not provider
    assert get_active_search_provider() is not provider


def test_a_key_makes_deepseek_available(keyed):
    assert keyed.is_available()


def test_explicit_backends_select_deepseek(tmp_path, monkeypatch):
    from agent.web_search_registry import get_active_extract_provider, get_active_search_provider

    found = install(tmp_path, monkeypatch, web={"search_backend": "deepseek", "extract_backend": "deepseek"})
    assert get_active_search_provider() is found
    assert get_active_extract_provider() is found


def test_extract_posts_the_url_and_returns_markdown(keyed, requests):
    seen = requests(fetch_ok)

    [doc] = extract(keyed, ["https://example.com/a"])

    assert doc["url"] == "https://example.com/a"
    assert doc["title"] == "Title of https://example.com/a"
    assert doc["content"] == doc["raw_content"] == "# Page\n\nBody of https://example.com/a"
    assert "error" not in doc
    [request] = seen
    assert request.method == "POST"
    assert str(request.url) == "https://api.deepseek.com/v1/tools/fetch"
    assert json.loads(request.content) == {"url": "https://example.com/a"}
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["accept"] == "application/json"


def test_extract_without_a_key_fails_every_url_without_a_request(provider, requests):
    seen = requests(lambda r: pytest.fail("no request expected"))

    docs = extract(provider, ["https://example.com/a", "https://example.com/b"])

    assert [d["url"] for d in docs] == ["https://example.com/a", "https://example.com/b"]
    assert all("DEEPSEEK_API_KEY" in d["error"] for d in docs)
    assert seen == []


def test_per_url_failures_keep_order_and_never_raise(keyed, requests):
    def handler(request):
        url = json.loads(request.content)["url"]
        if url.endswith("/limited"):
            return deepseek_error(429, "Fetch QPS limit exceeded")
        if url.endswith("/gone"):
            return deepseek_error(404, "no usable content")
        return fetch_ok(request)

    seen = requests(handler)
    urls = [
        "https://example.com/ok",
        "https://example.com/limited",
        "file:///etc/passwd",
        "https://user:pw@example.com/secret",
        "https://example.com/" + "x" * 8200,
        "https://example.com/gone",
        "https://example.com/ok2",
    ]

    docs = extract(keyed, urls)

    assert [d["url"] for d in docs] == urls
    assert "error" not in docs[0] and "error" not in docs[6]
    assert "HTTP 429" in docs[1]["error"] and "Fetch QPS limit exceeded" in docs[1]["error"]
    assert "http" in docs[2]["error"]
    assert "username or password" in docs[3]["error"]
    assert "8192" in docs[4]["error"]
    assert "HTTP 404" in docs[5]["error"] and "no usable content" in docs[5]["error"].lower()
    assert len(seen) == 4, "URLs DeepSeek would reject must not be sent"


def test_empty_page_is_reported_as_a_per_url_error(keyed, requests):
    requests(lambda r: httpx.Response(200, json={"title": "", "content": "  "}))

    [doc] = extract(keyed, ["https://x.test/"])

    assert doc["error"]
    assert doc["content"] == ""


def test_transport_failures_become_readable_errors(keyed, requests):
    def handler(request):
        raise httpx.ConnectTimeout("boom", request=request)

    requests(handler)

    [doc] = extract(keyed, ["https://example.com/a"])

    assert doc["error"] == "DeepSeek request timed out. Try again later."


def test_search_without_a_key_fails_without_a_request(provider, requests):
    seen = requests(lambda r: pytest.fail("no request expected"))

    result = provider.search("hermes agent")

    assert result["success"] is False
    assert "DEEPSEEK_API_KEY" in result["error"]
    assert seen == []


def test_search_maps_results_and_honours_limit(keyed, requests):
    rows = [{
        "rerank_score": 2.0, "title": f"T{i}", "url": f"https://r{i}.test/", "snippet": f"S{i}",
        "summary": f"Long summary {i}", "content": "", "site_name": None, "site_icon": None,
        "date": None, "authority": -1,
    } for i in range(5)]
    rows.insert(1, {"title": "no url"})
    rows[3]["summary"] = ""
    seen = requests(lambda r: httpx.Response(200, json={"results": rows}))

    result = keyed.search("hermes agent", limit=3)

    assert result == {"success": True, "data": {"web": [
        {"title": "T0", "url": "https://r0.test/", "description": "Long summary 0", "position": 1},
        {"title": "T1", "url": "https://r1.test/", "description": "Long summary 1", "position": 2},
        {"title": "T2", "url": "https://r2.test/", "description": "S2", "position": 3},
    ]}}
    [request] = seen
    assert request.method == "POST"
    assert str(request.url) == "https://api.deepseek.com/v1/tools/search"
    assert json.loads(request.content) == {"query": "hermes agent", "count": 3}
    assert request.headers["authorization"] == f"Bearer {KEY}"


def test_search_clamps_the_limit_to_deepseeks_range(keyed, requests):
    seen = requests(lambda r: httpx.Response(200, json={"results": []}))

    assert keyed.search("q", limit=500) == {"success": True, "data": {"web": []}}
    keyed.search("q", limit=0)

    assert [json.loads(r.content)["count"] for r in seen] == [50, 5]


def test_search_refuses_queries_deepseek_would_reject(keyed, requests):
    seen = requests(lambda r: pytest.fail("no request expected"))

    too_long = keyed.search("x" * 129)
    blank = keyed.search("   ")

    assert too_long["success"] is False and "128" in too_long["error"]
    assert blank["success"] is False
    assert seen == []


@pytest.mark.parametrize("status,hint", [
    (401, "DEEPSEEK_API_KEY"),
    (402, "top up"),
    (429, "Rate limit"),
    (504, "status.deepseek.com"),
])
def test_search_errors_explain_what_to_do_but_never_leak_the_key(keyed, requests, status, hint):
    requests(lambda r: deepseek_error(status, "upstream says no"))

    result = keyed.search("q")

    assert result["success"] is False
    assert f"HTTP {status}" in result["error"] and hint in result["error"]
    assert "upstream says no" in result["error"]
    assert KEY not in result["error"]


def test_search_rejects_a_malformed_envelope(keyed, requests):
    requests(lambda r: httpx.Response(200, json={"results": {"not": "a list"}}))

    result = keyed.search("q")

    assert result["success"] is False
