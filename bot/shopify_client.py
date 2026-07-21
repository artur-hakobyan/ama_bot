import httpx


class ShopifyError(Exception):
    pass


ARTICLE_FIELDS = "id title handle isPublished"

CREATE_ARTICLE = f"""
mutation CreateArticle($article: ArticleCreateInput!) {{
  articleCreate(article: $article) {{
    article {{ {ARTICLE_FIELDS} }}
    userErrors {{ field message }}
  }}
}}"""

UPDATE_ARTICLE = f"""
mutation UpdateArticle($id: ID!, $article: ArticleUpdateInput!) {{
  articleUpdate(id: $id, article: $article) {{
    article {{ {ARTICLE_FIELDS} }}
    userErrors {{ field message }}
  }}
}}"""

DELETE_ARTICLE = """
mutation DeleteArticle($id: ID!) {
  articleDelete(id: $id) {
    deletedArticleId
    userErrors { field message }
  }
}"""

GET_ARTICLE = f"""
query GetArticle($id: ID!) {{
  node(id: $id) {{
    ... on Article {{ {ARTICLE_FIELDS} body }}
  }}
}}"""

LIST_ARTICLES = f"""
query ListArticles($blogId: ID!, $first: Int!) {{
  blog(id: $blogId) {{
    handle
    articles(first: $first, sortKey: UPDATED_AT, reverse: true) {{
      nodes {{ {ARTICLE_FIELDS} publishedAt }}
    }}
  }}
}}"""


class ShopifyClient:
    def __init__(self, domain: str, token: str, version: str,
                 client: httpx.AsyncClient | None = None):
        self._domain = domain
        self._url = f"https://{domain}/admin/api/{version}/graphql.json"
        self._headers = {"X-Shopify-Access-Token": token,
                         "Content-Type": "application/json"}
        self._client = client or httpx.AsyncClient(timeout=30)

    async def _execute(self, query: str, variables: dict) -> dict:
        try:
            resp = await self._client.post(
                self._url, json={"query": query, "variables": variables},
                headers=self._headers)
        except httpx.HTTPError as e:
            raise ShopifyError(f"Shopify nicht erreichbar: {e}") from e
        if resp.status_code != 200:
            raise ShopifyError(f"Shopify HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if data.get("errors"):
            raise ShopifyError(f"Shopify GraphQL errors: {data['errors']}")
        return data["data"]

    @staticmethod
    def _check_user_errors(payload: dict, op: str):
        errors = payload.get("userErrors") or []
        if errors:
            msgs = "; ".join(
                f"{'/'.join(e.get('field') or ['?'])}: {e['message']}" for e in errors)
            raise ShopifyError(f"{op} failed: {msgs}")

    async def create_article(self, blog_id, title, body_html, summary, tags, author_name) -> dict:
        article = {
            "blogId": blog_id, "title": title, "body": body_html,
            "summary": summary, "tags": tags, "isPublished": False,
            "author": {"name": author_name},
        }
        data = await self._execute(CREATE_ARTICLE, {"article": article})
        payload = data["articleCreate"]
        self._check_user_errors(payload, "articleCreate")
        return payload["article"]

    async def update_article(self, article_gid: str, fields: dict) -> dict:
        data = await self._execute(UPDATE_ARTICLE, {"id": article_gid, "article": fields})
        payload = data["articleUpdate"]
        self._check_user_errors(payload, "articleUpdate")
        return payload["article"]

    async def publish_article(self, article_gid: str) -> dict:
        return await self.update_article(article_gid, {"isPublished": True})

    async def delete_article(self, article_gid: str) -> str:
        data = await self._execute(DELETE_ARTICLE, {"id": article_gid})
        payload = data["articleDelete"]
        self._check_user_errors(payload, "articleDelete")
        return payload["deletedArticleId"]

    async def get_article(self, article_gid: str) -> dict:
        data = await self._execute(GET_ARTICLE, {"id": article_gid})
        node = data.get("node")
        if not node:
            raise ShopifyError(f"Article not found: {article_gid}")
        return node

    async def list_articles(self, blog_id: str, first: int = 10):
        data = await self._execute(LIST_ARTICLES, {"blogId": blog_id, "first": first})
        blog = data.get("blog")
        if not blog:
            raise ShopifyError(f"Blog not found: {blog_id}")
        return blog["handle"], blog["articles"]["nodes"]

    def admin_url(self, article_gid: str) -> str:
        num = article_gid.rsplit("/", 1)[-1]
        return f"https://{self._domain}/admin/articles/{num}"

    def live_url(self, blog_handle: str, article_handle: str) -> str:
        return f"https://{self._domain}/blogs/{blog_handle}/{article_handle}"
