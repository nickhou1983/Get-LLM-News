"""
Reddit 采集器。

使用 PRAW (Python Reddit API Wrapper) 库。
需要 Reddit API 凭据（免费申请）。

如果没有 API Key，使用 Reddit 的公开 JSON 端点作为降级方案。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx

from .base import BaseCollector, NewsItem


class RedditCollector(BaseCollector):
    """Reddit 采集器，支持 API 模式和公开 JSON 降级模式。"""

    @property
    def source_name(self) -> str:
        return "reddit"

    async def collect(self) -> list[NewsItem]:
        items: list[NewsItem] = []

        # Reddit 子版块配置
        reddit_config = self.kol_config.get("reddit", {})
        subreddits = reddit_config.get("subreddits", [])

        client_id = os.getenv("REDDIT_CLIENT_ID", "")
        client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")

        if client_id and client_secret:
            items = await self._collect_with_api(subreddits, client_id, client_secret)
        else:
            self.logger.info("未配置 Reddit API Key，使用公开 JSON 端点（降级模式）")
            items = await self._collect_with_json(subreddits)

        # 去重 + 排序
        seen: set[str] = set()
        unique: list[NewsItem] = []
        for item in items:
            if item.url not in seen:
                seen.add(item.url)
                unique.append(item)

        unique.sort(key=lambda x: x.engagement_score, reverse=True)
        return unique[: self.max_items]

    async def _collect_with_api(
        self, subreddits: list[dict], client_id: str, client_secret: str
    ) -> list[NewsItem]:
        """使用 Reddit OAuth API 采集（需要 API Key）。"""
        items: list[NewsItem] = []
        user_agent = os.getenv("REDDIT_USER_AGENT", "Get-LLM-News/1.0")

        async with httpx.AsyncClient(timeout=30) as client:
            # 获取 OAuth token
            auth_resp = await client.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(client_id, client_secret),
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": user_agent},
            )
            auth_resp.raise_for_status()
            token = auth_resp.json()["access_token"]

            headers = {
                "Authorization": f"Bearer {token}",
                "User-Agent": user_agent,
            }

            for sub_info in subreddits:
                sub_name = sub_info["name"]
                try:
                    results = await self._fetch_subreddit_api(
                        client, headers, sub_name
                    )
                    items.extend(results)
                except Exception as e:
                    self.logger.warning(f"采集 r/{sub_name} 失败: {e}")

        return items

    async def _fetch_subreddit_api(
        self, client: httpx.AsyncClient, headers: dict, subreddit: str
    ) -> list[NewsItem]:
        """通过 API 采集单个子版块的热帖。"""
        items: list[NewsItem] = []
        since = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)

        # 搜索模式：在子版块内搜索关键词
        for keyword in self.keywords[:10]:  # 限制搜索次数
            params = {
                "q": keyword,
                "restrict_sr": "true",
                "sort": "relevance",
                "t": "day" if self.lookback_days <= 1 else "week",
                "limit": 10,
            }

            resp = await client.get(
                f"https://oauth.reddit.com/r/{subreddit}/search",
                headers=headers,
                params=params,
            )

            if resp.status_code != 200:
                continue

            data = resp.json()
            for post in data.get("data", {}).get("children", []):
                post_data = post.get("data", {})
                item = self._parse_reddit_post(post_data, subreddit)
                if item:
                    items.append(item)

        return items

    async def _collect_with_json(self, subreddits: list[dict]) -> list[NewsItem]:
        """使用 Reddit 公开 JSON 端点（降级模式，无需 API Key）。"""
        items: list[NewsItem] = []
        since = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)

        async with httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "Get-LLM-News/1.0"},
            follow_redirects=True,
        ) as client:
            for sub_info in subreddits:
                sub_name = sub_info["name"]
                try:
                    # 使用公开搜索端点
                    for keyword in self.keywords[:8]:
                        resp = await client.get(
                            f"https://www.reddit.com/r/{sub_name}/search.json",
                            params={
                                "q": keyword,
                                "restrict_sr": "on",
                                "sort": "relevance",
                                "t": "day" if self.lookback_days <= 1 else "week",
                                "limit": 10,
                            },
                        )

                        if resp.status_code != 200:
                            self.logger.debug(
                                f"r/{sub_name} 搜索 '{keyword}' 返回 {resp.status_code}"
                            )
                            continue

                        data = resp.json()
                        for post in data.get("data", {}).get("children", []):
                            post_data = post.get("data", {})
                            item = self._parse_reddit_post(post_data, sub_name)
                            if item:
                                items.append(item)

                except Exception as e:
                    self.logger.warning(f"采集 r/{sub_name} 失败: {e}")

        return items

    def _parse_reddit_post(
        self, post_data: dict, subreddit: str
    ) -> NewsItem | None:
        """解析 Reddit 帖子数据为 NewsItem。"""
        title = post_data.get("title", "")
        selftext = post_data.get("selftext", "")
        full_text = f"{title} {selftext}"

        # 关键词过滤
        if not self.filter_by_keywords(full_text):
            return None

        # 互动量过滤
        ups = post_data.get("ups", 0)
        min_engagement = (
            self.settings.get("collection", {})
            .get("min_engagement", {})
            .get("reddit", 10)
        )
        if ups < min_engagement:
            return None

        # 时间过滤
        created_utc = post_data.get("created_utc", 0)
        published_at = datetime.fromtimestamp(created_utc, tz=timezone.utc)
        since = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        if published_at < since:
            return None

        permalink = post_data.get("permalink", "")
        url = f"https://www.reddit.com{permalink}" if permalink else post_data.get("url", "")

        item = NewsItem(
            title=title,
            content=selftext[:2000] if selftext else title,
            source=self.source_name,
            url=url,
            published_at=published_at,
            author=post_data.get("author", ""),
            author_handle=f"u/{post_data.get('author', '')}",
            engagement=ups,
            comments_count=post_data.get("num_comments", 0),
            language=self.detect_language(full_text),
        )

        self.tag_products(item)
        return item
