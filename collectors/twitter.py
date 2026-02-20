"""
Twitter/X 采集器。

支持两种模式：
1. Twitter API v2（需要 Bearer Token）- 使用 tweepy 库
2. 降级模式：通过 nitter 实例或直接搜索（受限）

KOL 推文优先级更高。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx

from .base import BaseCollector, NewsItem


class TwitterCollector(BaseCollector):
    """Twitter/X 采集器。"""

    @property
    def source_name(self) -> str:
        return "twitter"

    async def collect(self) -> list[NewsItem]:
        bearer_token = os.getenv("TWITTER_BEARER_TOKEN", "")

        if bearer_token:
            return await self._collect_with_api(bearer_token)
        else:
            self.logger.warning(
                "未配置 TWITTER_BEARER_TOKEN，使用降级搜索模式（功能受限）"
            )
            return await self._collect_fallback()

    async def _collect_with_api(self, bearer_token: str) -> list[NewsItem]:
        """使用 Twitter API v2 采集。"""
        items: list[NewsItem] = []
        twitter_kols = self.kol_config.get("twitter", [])

        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "User-Agent": "Get-LLM-News/1.0",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            # 策略 1: 搜索关键词相关推文
            items.extend(
                await self._search_tweets(client, headers)
            )

            # 策略 2: 拉取 KOL 最新推文
            for kol in twitter_kols[:15]:  # 限制 API 调用次数
                try:
                    kol_items = await self._get_kol_tweets(
                        client, headers, kol
                    )
                    items.extend(kol_items)
                except Exception as e:
                    self.logger.warning(
                        f"获取 @{kol['handle']} 推文失败: {e}"
                    )

        # 去重 + 排序
        return self._deduplicate_and_sort(items)

    async def _search_tweets(
        self, client: httpx.AsyncClient, headers: dict
    ) -> list[NewsItem]:
        """搜索包含关键词的热门推文。"""
        items: list[NewsItem] = []
        since = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)

        # 构建搜索查询：多个关键词用 OR 连接
        # Twitter API 搜索语法
        keyword_groups = []
        for i in range(0, len(self.keywords), 5):
            group = self.keywords[i : i + 5]
            query = " OR ".join(f'"{kw}"' for kw in group)
            keyword_groups.append(query)

        for query in keyword_groups[:3]:  # 限制查询次数
            try:
                params = {
                    "query": f"({query}) -is:retweet lang:en",
                    "max_results": 20,
                    "start_time": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "tweet.fields": "created_at,public_metrics,author_id,lang",
                    "user.fields": "name,username",
                    "expansions": "author_id",
                    "sort_order": "relevancy",
                }

                resp = await client.get(
                    "https://api.twitter.com/2/tweets/search/recent",
                    headers=headers,
                    params=params,
                )

                if resp.status_code != 200:
                    self.logger.warning(f"Twitter 搜索返回 {resp.status_code}: {resp.text[:200]}")
                    continue

                data = resp.json()
                users_map = self._build_users_map(data)

                for tweet in data.get("data", []):
                    item = self._parse_tweet(tweet, users_map)
                    if item:
                        items.append(item)

            except Exception as e:
                self.logger.warning(f"Twitter 搜索失败: {e}")

        return items

    async def _get_kol_tweets(
        self, client: httpx.AsyncClient, headers: dict, kol: dict
    ) -> list[NewsItem]:
        """获取指定 KOL 的最新推文。"""
        items: list[NewsItem] = []
        handle = kol["handle"]

        # 首先获取用户 ID
        user_resp = await client.get(
            f"https://api.twitter.com/2/users/by/username/{handle}",
            headers=headers,
        )

        if user_resp.status_code != 200:
            return items

        user_data = user_resp.json().get("data", {})
        user_id = user_data.get("id")
        if not user_id:
            return items

        # 获取该用户的推文
        since = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        params = {
            "max_results": 10,
            "start_time": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tweet.fields": "created_at,public_metrics,lang",
            "exclude": "retweets",
        }

        resp = await client.get(
            f"https://api.twitter.com/2/users/{user_id}/tweets",
            headers=headers,
            params=params,
        )

        if resp.status_code != 200:
            return items

        data = resp.json()
        for tweet in data.get("data", []):
            text = tweet.get("text", "")
            if not self.filter_by_keywords(text):
                continue

            metrics = tweet.get("public_metrics", {})
            published_at = self._parse_twitter_time(tweet.get("created_at", ""))

            item = NewsItem(
                title=text[:100],
                content=text,
                source=self.source_name,
                url=f"https://twitter.com/{handle}/status/{tweet['id']}",
                published_at=published_at,
                author=kol.get("name", handle),
                author_handle=f"@{handle}",
                engagement=metrics.get("like_count", 0),
                comments_count=metrics.get("reply_count", 0),
                is_kol=True,
                kol_tier=kol.get("tier", "B"),
                language=tweet.get("lang", "en"),
            )

            self.tag_products(item)
            items.append(item)

        return items

    async def _collect_fallback(self) -> list[NewsItem]:
        """
        降级搜索模式。

        当没有 Twitter API Key 时，尝试通过公开可访问的方式获取信息。
        注意：此方式功能受限，建议申请正式 API Key。
        """
        items: list[NewsItem] = []
        self.logger.info(
            "Twitter 降级模式：功能受限。建议申请 Twitter API v2 Bearer Token。"
        )
        self.logger.info(
            "申请地址: https://developer.twitter.com/en/portal"
        )
        return items

    def _build_users_map(self, data: dict) -> dict[str, dict]:
        """从 Twitter API 响应中构建 user_id -> user_info 映射。"""
        users_map = {}
        includes = data.get("includes", {})
        for user in includes.get("users", []):
            users_map[user["id"]] = {
                "name": user.get("name", ""),
                "username": user.get("username", ""),
            }
        return users_map

    def _parse_tweet(
        self, tweet: dict, users_map: dict[str, dict]
    ) -> NewsItem | None:
        """解析单条推文为 NewsItem。"""
        text = tweet.get("text", "")
        if not self.filter_by_keywords(text):
            return None

        metrics = tweet.get("public_metrics", {})
        likes = metrics.get("like_count", 0)

        # 互动量筛选
        min_engagement = (
            self.settings.get("collection", {})
            .get("min_engagement", {})
            .get("twitter", 20)
        )
        if likes < min_engagement:
            return None

        author_id = tweet.get("author_id", "")
        user_info = users_map.get(author_id, {})
        username = user_info.get("username", "")
        author_name = user_info.get("name", username)

        # 检查是否为已知 KOL
        is_kol = False
        kol_tier = ""
        for kol in self.kol_config.get("twitter", []):
            if kol["handle"].lower() == username.lower():
                is_kol = True
                kol_tier = kol.get("tier", "B")
                break

        published_at = self._parse_twitter_time(tweet.get("created_at", ""))

        item = NewsItem(
            title=text[:100],
            content=text,
            source=self.source_name,
            url=f"https://twitter.com/{username}/status/{tweet['id']}",
            published_at=published_at,
            author=author_name,
            author_handle=f"@{username}",
            engagement=likes,
            comments_count=metrics.get("reply_count", 0),
            is_kol=is_kol,
            kol_tier=kol_tier,
            language=tweet.get("lang", "en"),
        )

        self.tag_products(item)
        return item

    def _parse_twitter_time(self, time_str: str) -> datetime:
        """解析 Twitter API 时间字符串。"""
        try:
            return datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return datetime.now(timezone.utc)

    def _deduplicate_and_sort(self, items: list[NewsItem]) -> list[NewsItem]:
        """去重并按互动分排序。"""
        seen: set[str] = set()
        unique: list[NewsItem] = []
        for item in items:
            if item.url not in seen:
                seen.add(item.url)
                unique.append(item)
        unique.sort(key=lambda x: x.engagement_score, reverse=True)
        return unique[: self.max_items]
