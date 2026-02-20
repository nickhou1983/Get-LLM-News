"""
微博/知乎采集器。

使用 Playwright 无头浏览器模拟访问，支持：
1. 微博搜索：按关键词搜索 + KOL 主页采集
2. 知乎搜索：按关键词/话题搜索

注意：
- 微博/知乎反爬较严格，需要维护登录 Cookie
- 使用 WEIBO_COOKIE / ZHIHU_COOKIE 环境变量注入登录态
- 如果没有 Cookie，部分内容可能无法获取
"""

from __future__ import annotations

import os
import json
from datetime import datetime, timedelta, timezone

import httpx

from .base import BaseCollector, NewsItem


class WeiboZhihuCollector(BaseCollector):
    """微博/知乎采集器，使用 HTTP 请求 + Cookie 方式。"""

    @property
    def source_name(self) -> str:
        return "weibo_zhihu"

    async def collect(self) -> list[NewsItem]:
        items: list[NewsItem] = []

        # 微博采集
        weibo_items = await self._collect_weibo()
        items.extend(weibo_items)

        # 知乎采集
        zhihu_items = await self._collect_zhihu()
        items.extend(zhihu_items)

        # 排序
        items.sort(key=lambda x: x.engagement_score, reverse=True)
        return items[: self.max_items]

    # ===== 微博 =====

    async def _collect_weibo(self) -> list[NewsItem]:
        """采集微博内容。"""
        items: list[NewsItem] = []
        weibo_cookie = os.getenv("WEIBO_COOKIE", "")

        if not weibo_cookie:
            self.logger.info("未配置 WEIBO_COOKIE，使用微博公开搜索 API（功能受限）")

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://m.weibo.cn/",
        }
        if weibo_cookie:
            headers["Cookie"] = weibo_cookie

        async with httpx.AsyncClient(
            timeout=30, headers=headers, follow_redirects=True
        ) as client:
            # 策略 1: 按关键词搜索微博
            for keyword in self.keywords[:8]:
                try:
                    results = await self._search_weibo(client, keyword)
                    items.extend(results)
                except Exception as e:
                    self.logger.warning(f"微博搜索 '{keyword}' 失败: {e}")

            # 策略 2: 采集 KOL 微博
            weibo_kols = self.kol_config.get("weibo", [])
            for kol in weibo_kols:
                if kol.get("uid"):
                    try:
                        kol_items = await self._get_weibo_kol(client, kol)
                        items.extend(kol_items)
                    except Exception as e:
                        self.logger.warning(
                            f"采集微博 KOL {kol['name']} 失败: {e}"
                        )

        return items

    async def _search_weibo(
        self, client: httpx.AsyncClient, keyword: str
    ) -> list[NewsItem]:
        """通过微博移动端搜索 API 搜索关键词。"""
        items: list[NewsItem] = []

        # 使用微博移动端搜索 API
        params = {
            "containerid": f"100103type=1&q={keyword}",
            "page_type": "searchall",
        }

        try:
            resp = await client.get(
                "https://m.weibo.cn/api/container/getIndex",
                params=params,
            )

            if resp.status_code != 200:
                return items

            data = resp.json()
            cards = data.get("data", {}).get("cards", [])

            for card in cards:
                if card.get("card_type") != 9:
                    continue

                mblog = card.get("mblog", {})
                item = self._parse_weibo_post(mblog)
                if item:
                    items.append(item)

        except Exception as e:
            self.logger.debug(f"微博搜索失败: {e}")

        return items

    async def _get_weibo_kol(
        self, client: httpx.AsyncClient, kol: dict
    ) -> list[NewsItem]:
        """获取指定 KOL 的最新微博。"""
        items: list[NewsItem] = []
        uid = kol["uid"]

        params = {
            "containerid": f"107603{uid}",
            "page": 1,
        }

        try:
            resp = await client.get(
                "https://m.weibo.cn/api/container/getIndex",
                params=params,
            )

            if resp.status_code != 200:
                return items

            data = resp.json()
            cards = data.get("data", {}).get("cards", [])

            for card in cards:
                if card.get("card_type") != 9:
                    continue

                mblog = card.get("mblog", {})
                text = self._clean_weibo_html(mblog.get("text", ""))

                if not self.filter_by_keywords(text):
                    continue

                created_at = self._parse_weibo_time(mblog.get("created_at", ""))
                since = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
                if created_at < since:
                    continue

                reposts = mblog.get("reposts_count", 0)
                comments = mblog.get("comments_count", 0)
                attitudes = mblog.get("attitudes_count", 0)

                item = NewsItem(
                    title=text[:100],
                    content=text[:2000],
                    source="weibo",
                    url=f"https://m.weibo.cn/detail/{mblog.get('id', '')}",
                    published_at=created_at,
                    author=kol["name"],
                    author_handle=f"uid:{uid}",
                    engagement=attitudes + reposts,
                    comments_count=comments,
                    is_kol=True,
                    kol_tier=kol.get("tier", "B"),
                    language="zh",
                )

                self.tag_products(item)
                items.append(item)

        except Exception as e:
            self.logger.debug(f"获取微博 KOL {kol['name']} 失败: {e}")

        return items

    def _parse_weibo_post(self, mblog: dict) -> NewsItem | None:
        """解析微博帖子。"""
        text = self._clean_weibo_html(mblog.get("text", ""))

        if not self.filter_by_keywords(text):
            return None

        reposts = mblog.get("reposts_count", 0)
        comments = mblog.get("comments_count", 0)
        attitudes = mblog.get("attitudes_count", 0)

        min_engagement = (
            self.settings.get("collection", {})
            .get("min_engagement", {})
            .get("weibo", 50)
        )
        if (attitudes + reposts) < min_engagement:
            return None

        created_at = self._parse_weibo_time(mblog.get("created_at", ""))
        since = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        if created_at < since:
            return None

        user = mblog.get("user", {})
        author_name = user.get("screen_name", "")

        # 检查是否为已知 KOL
        is_kol = False
        kol_tier = ""
        for kol in self.kol_config.get("weibo", []):
            if kol["name"] == author_name:
                is_kol = True
                kol_tier = kol.get("tier", "B")
                break

        item = NewsItem(
            title=text[:100],
            content=text[:2000],
            source="weibo",
            url=f"https://m.weibo.cn/detail/{mblog.get('id', '')}",
            published_at=created_at,
            author=author_name,
            author_handle=f"uid:{user.get('id', '')}",
            engagement=attitudes + reposts,
            comments_count=comments,
            is_kol=is_kol,
            kol_tier=kol_tier,
            language="zh",
        )

        self.tag_products(item)
        return item

    # ===== 知乎 =====

    async def _collect_zhihu(self) -> list[NewsItem]:
        """采集知乎内容。"""
        items: list[NewsItem] = []
        zhihu_cookie = os.getenv("ZHIHU_COOKIE", "")

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.zhihu.com/",
        }
        if zhihu_cookie:
            headers["Cookie"] = zhihu_cookie

        zhihu_config = self.kol_config.get("zhihu", {})
        topics = zhihu_config.get("topics", [])

        # 使用知乎搜索 API
        async with httpx.AsyncClient(
            timeout=30, headers=headers, follow_redirects=True
        ) as client:
            search_keywords = topics + self.keywords[:5]
            for keyword in search_keywords[:10]:
                try:
                    results = await self._search_zhihu(client, keyword)
                    items.extend(results)
                except Exception as e:
                    self.logger.warning(f"知乎搜索 '{keyword}' 失败: {e}")

        return items

    async def _search_zhihu(
        self, client: httpx.AsyncClient, keyword: str
    ) -> list[NewsItem]:
        """搜索知乎内容。"""
        items: list[NewsItem] = []

        params = {
            "type": "content",
            "q": keyword,
            "limit": 10,
            "offset": 0,
        }

        try:
            resp = await client.get(
                "https://www.zhihu.com/api/v4/search_v3",
                params=params,
            )

            if resp.status_code != 200:
                self.logger.debug(f"知乎搜索返回 {resp.status_code}")
                return items

            data = resp.json()

            for result in data.get("data", []):
                obj = result.get("object", {})
                obj_type = result.get("type", "")

                if obj_type not in ("answer", "article", "zvideo"):
                    continue

                title = obj.get("question", {}).get("name", "") or obj.get("title", "")
                content = obj.get("excerpt", "") or obj.get("content", "")[:500]
                full_text = f"{title} {content}"

                if not self.filter_by_keywords(full_text):
                    continue

                voteup = obj.get("voteup_count", 0)
                min_engagement = (
                    self.settings.get("collection", {})
                    .get("min_engagement", {})
                    .get("zhihu", 10)
                )
                if voteup < min_engagement:
                    continue

                # 构建 URL
                if obj_type == "answer":
                    question_id = obj.get("question", {}).get("id", "")
                    answer_id = obj.get("id", "")
                    url = f"https://www.zhihu.com/question/{question_id}/answer/{answer_id}"
                elif obj_type == "article":
                    url = f"https://zhuanlan.zhihu.com/p/{obj.get('id', '')}"
                else:
                    url = obj.get("url", "")

                author = obj.get("author", {})
                author_name = author.get("name", "")

                # 检查 KOL
                is_kol = False
                kol_tier = ""
                for known_author in self.kol_config.get("zhihu", {}).get("authors", []):
                    if known_author["name"] == author_name:
                        is_kol = True
                        kol_tier = known_author.get("tier", "B")
                        break

                created_time = obj.get("created_time", 0) or obj.get("created", 0)
                if created_time:
                    published_at = datetime.fromtimestamp(created_time, tz=timezone.utc)
                else:
                    published_at = datetime.now(timezone.utc)

                item = NewsItem(
                    title=title[:200] if title else content[:100],
                    content=content[:2000],
                    source="zhihu",
                    url=url,
                    published_at=published_at,
                    author=author_name,
                    engagement=voteup,
                    comments_count=obj.get("comment_count", 0),
                    is_kol=is_kol,
                    kol_tier=kol_tier,
                    language="zh",
                )

                self.tag_products(item)
                items.append(item)

        except Exception as e:
            self.logger.debug(f"知乎搜索解析失败: {e}")

        return items

    # ===== 工具方法 =====

    @staticmethod
    def _clean_weibo_html(html_text: str) -> str:
        """清理微博 HTML 标签，提取纯文本。"""
        import re
        # 移除 HTML 标签
        text = re.sub(r"<[^>]+>", "", html_text)
        # 移除多余空白
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _parse_weibo_time(time_str: str) -> datetime:
        """解析微博时间字符串（如 '刚刚', '5分钟前', '今天 10:00'）。"""
        import re
        now = datetime.now(timezone.utc)

        if not time_str:
            return now

        if "刚刚" in time_str:
            return now

        # X分钟前
        m = re.search(r"(\d+)\s*分钟前", time_str)
        if m:
            return now - timedelta(minutes=int(m.group(1)))

        # X小时前
        m = re.search(r"(\d+)\s*小时前", time_str)
        if m:
            return now - timedelta(hours=int(m.group(1)))

        # 今天 HH:MM
        m = re.search(r"今天\s*(\d{1,2}):(\d{2})", time_str)
        if m:
            return now.replace(hour=int(m.group(1)), minute=int(m.group(2)))

        # 昨天 HH:MM
        m = re.search(r"昨天\s*(\d{1,2}):(\d{2})", time_str)
        if m:
            yesterday = now - timedelta(days=1)
            return yesterday.replace(hour=int(m.group(1)), minute=int(m.group(2)))

        # MM-DD 或 YYYY-MM-DD
        m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", time_str)
        if m:
            try:
                return datetime(
                    int(m.group(1)), int(m.group(2)), int(m.group(3)),
                    tzinfo=timezone.utc,
                )
            except ValueError:
                pass

        m = re.search(r"(\d{1,2})-(\d{1,2})", time_str)
        if m:
            try:
                return datetime(
                    now.year, int(m.group(1)), int(m.group(2)),
                    tzinfo=timezone.utc,
                )
            except ValueError:
                pass

        return now
