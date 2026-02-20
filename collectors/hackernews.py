"""
Hacker News 采集器。

使用 Algolia HN Search API（完全公开，无需 API Key）。
- 搜索端点: https://hn.algolia.com/api/v1/search
- 按关键词搜索 + 分数筛选
- 支持时间范围过滤
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from .base import BaseCollector, NewsItem


class HackerNewsCollector(BaseCollector):
    """Hacker News 采集器，使用 Algolia Search API。"""

    API_BASE = "https://hn.algolia.com/api/v1"

    @property
    def source_name(self) -> str:
        return "hackernews"

    async def collect(self) -> list[NewsItem]:
        items: list[NewsItem] = []

        # HN 配置
        hn_config = self.kol_config.get("hackernews", {})
        min_score = hn_config.get("min_score", 10)
        search_tags = hn_config.get("search_tags", ["story"])

        # 时间范围：最近 N 天
        since = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        since_ts = int(since.timestamp())

        async with httpx.AsyncClient(timeout=30) as client:
            for keyword in self.keywords:
                try:
                    results = await self._search(
                        client, keyword, since_ts, min_score, search_tags
                    )
                    items.extend(results)
                except Exception as e:
                    self.logger.warning(f"搜索关键词 '{keyword}' 失败: {e}")

                # 避免触发速率限制
                if len(items) >= self.max_items:
                    break

        # 去重（按 URL）
        seen_urls: set[str] = set()
        unique_items: list[NewsItem] = []
        for item in items:
            if item.url not in seen_urls:
                seen_urls.add(item.url)
                unique_items.append(item)

        # 按互动量排序，取 top N
        unique_items.sort(key=lambda x: x.engagement_score, reverse=True)
        return unique_items[: self.max_items]

    async def _search(
        self,
        client: httpx.AsyncClient,
        query: str,
        since_ts: int,
        min_score: int,
        search_tags: list[str],
    ) -> list[NewsItem]:
        """执行单个关键词搜索。"""
        items: list[NewsItem] = []

        # 构建搜索标签过滤
        # Algolia HN API: 括号内逗号表示 OR，括号间逗号表示 AND
        # (story,show_hn,ask_hn) = story OR show_hn OR ask_hn
        tags_filter = "(" + ",".join(search_tags) + ")"

        params = {
            "query": query,
            "tags": tags_filter,
            "numericFilters": f"created_at_i>{since_ts},points>{min_score}",
            "hitsPerPage": 20,
            "page": 0,
        }

        resp = await client.get(f"{self.API_BASE}/search", params=params)
        resp.raise_for_status()
        data = resp.json()

        for hit in data.get("hits", []):
            title = hit.get("title", "")
            story_text = hit.get("story_text") or ""
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
            author = hit.get("author", "")
            points = hit.get("points", 0)
            num_comments = hit.get("num_comments", 0)

            # 解析时间
            created_at_str = hit.get("created_at", "")
            try:
                published_at = datetime.fromisoformat(
                    created_at_str.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                published_at = datetime.now(timezone.utc)

            # 构建 content: 优先用 story_text，否则用 title
            content = story_text if story_text else title

            # 关键词二次确认
            full_text = f"{title} {content}"
            if not self.filter_by_keywords(full_text):
                continue

            news_item = NewsItem(
                title=title,
                content=content,
                source=self.source_name,
                url=url,
                published_at=published_at,
                author=author,
                engagement=points,
                comments_count=num_comments,
                language=self.detect_language(full_text),
            )

            # 打产品标签
            self.tag_products(news_item)
            items.append(news_item)

        self.logger.debug(
            f"关键词 '{query}' 搜索到 {len(items)} 条有效结果"
        )
        return items
