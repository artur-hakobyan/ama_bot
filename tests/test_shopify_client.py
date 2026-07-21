import json
import httpx
import pytest
from bot.shopify_client import ShopifyClient, ShopifyError

def make_client(responder):
    transport = httpx.MockTransport(responder)
    http = httpx.AsyncClient(transport=transport)
    return ShopifyClient("dev.myshopify.com", "tok", "2026-07", client=http)

def gql_response(payload, status=200):
    return httpx.Response(status, json=payload)

async def test_create_article_sends_draft_and_token():
    captured = {}
    def responder(request):
        captured["headers"] = request.headers
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return gql_response({"data": {"articleCreate": {
            "article": {"id": "gid://shopify/Article/1", "title": "T",
                        "handle": "t", "isPublished": False},
            "userErrors": []}}})
    c = make_client(responder)
    art = await c.create_article("gid://shopify/Blog/9", "T", "<p>b</p>", "s", ["x"], "Author")
    assert art["id"] == "gid://shopify/Article/1"
    assert captured["headers"]["x-shopify-access-token"] == "tok"
    assert captured["body"]["variables"]["article"]["isPublished"] is False
    assert captured["url"].endswith("/admin/api/2026-07/graphql.json")

async def test_user_errors_raise():
    def responder(request):
        return gql_response({"data": {"articleCreate": {
            "article": None,
            "userErrors": [{"field": ["title"], "message": "can't be blank"}]}}})
    c = make_client(responder)
    with pytest.raises(ShopifyError, match="can't be blank"):
        await c.create_article("gid://shopify/Blog/9", "", "b", "s", [], "A")

async def test_http_error_raises():
    c = make_client(lambda r: httpx.Response(401, text="denied"))
    with pytest.raises(ShopifyError, match="401"):
        await c.get_article("gid://shopify/Article/1")

async def test_list_articles():
    def responder(request):
        return gql_response({"data": {"blog": {"handle": "news", "articles": {"nodes": [
            {"id": "gid://shopify/Article/1", "title": "A", "handle": "a",
             "isPublished": True, "publishedAt": "2026-01-01"}]}}}})
    c = make_client(responder)
    handle, arts = await c.list_articles("gid://shopify/Blog/9")
    assert handle == "news" and arts[0]["title"] == "A"

def test_urls():
    c = ShopifyClient("dev.myshopify.com", "tok", "2026-07",
                      client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert c.admin_url("gid://shopify/Article/123") == "https://dev.myshopify.com/admin/articles/123"
    assert c.live_url("news", "my-post") == "https://dev.myshopify.com/blogs/news/my-post"
