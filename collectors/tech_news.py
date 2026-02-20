"""
技术新闻站采集器。

通过 RSS 和 HTML 解析采集技术新闻。
支持的来源：
- The Verge (RSS)
- TechCrunch (RSS)
- Ars Technica (RSS)
- 36Kr (HTML)
- 其他 RSS 源

无需 API Key，完全使用公开信息。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx
import feedparser
from bs4 import BeautifulSoup

from .base import BaseCollector, NewsItem


class TechNewsCollector(BaseCollector):
    """技术新闻站采集器，支持 RSS 和 HTML 模式。"""

    @property
    def source_name(self) -> str:
        return "tech_news"

    async def collect(self) -> list[NewsItem]:
        items: list[NewsItem] = []
        news_config = self.kol_config.get("tech_news", {})
        sources = news_config.get("sources", [])

        async with httpx.AsyncClient(
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
            },
            follow_redirects=True,
        ) as client:
            for source in sources:
                try:
                    source_type = source.get("type", "rss")
                    if source_type == "rss":
                        results = await self._collect_rss(client, source)
                    else:
                        results = await self._collect_html(client, source)
                    items.extend(results)
                    self.logger.debug(
                        f"[{source['name']}] 采集到 {len(results)} 条"
                    )
                except Exception as e:
                    self.logger.warning(f"采集 {source['name']} 失败: {e}")

        # 去重 + 排序
        seen: set[str] = set()
        unique: list[NewsItem] = []
        for item in items:
            if item.url not in seen:
                seen.add(item.url)
                unique.append(item)

        unique.sort(key=lambda x: x.published_at, reverse=True)
        return unique[: self.max_items]

    async def _collect_rss(
        self, client: httpx.AsyncClient, source: dict
    ) -> list[NewsItem]:
        """通过 RSS 采集新闻。"""
        items: list[NewsItem] = []
        feed_url = source["url"]
        source_name = source["name"]
        lang = source.get("language", "en")

        resp = await client.get(feed_url)
        if resp.status_code != 200:
            self.logger.warning(f"RSS {source_name} 返回 {resp.status_code}")
            return items

        feed = feedparser.parse(resp.text)
        since = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)

        for entry in feed.entries[:30]:
            title = entry.get("title", "")
            summary = entry.get("summary", "") or entry.get("description", "")
            content = entry.get("content", [{}])
            if isinstance(content, list) and content:
                full_content = content[0].get("value", summary)
            else:
                full_content = summary

            # 清理 HTML
            full_content = self._strip_html(full_content)
            full_text = f"{title} {full_content}"

            # 关键词过滤
            if not self.filter_by_keywords(full_text):
                continue

            # 时间过滤
            published_at = self._parse_feed_time(entry)
            if published_at < since:
                continue

            link = entry.get("link", "")
            author = entry.get("author", source_name)

            item = NewsItem(
                title=title,
                content=full_content[:2000],
                source=self.source_name,
                url=link,
                published_at=published_at,
                author=author,
                language=lang,
            )

            self.tag_products(item)
            items.append(item)

        return items

    async def _collect_html(
        self, client: httpx.AsyncClient, source: dict
    ) -> list[NewsItem]:
        """通过 HTML 解析采集新闻（用于不支持 RSS 的源，如 36Kr）。"""
        items: list[NewsItem] = []
        page_url = source["url"]
        source_name = source["name"]
        lang = source.get("language", "zh")

        resp = await client.get(page_url)
        if resp.status_code != 200:
            self.logger.warning(f"HTML {source_name} 返回 {resp.status_code}")
            return items

        soup = BeautifulSoup(resp.text, "lxml")
        since = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)

        # 36Kr 文章列表解析
        if "36kr" in page_url:
            items.extend(self._parse_36kr(soup, source_name, lang))
        else:
            # 通用 HTML 解析：查找包含关键词的文章链接
            items.extend(self._parse_generic_html(soup, page_url, source_name, lang))

        return items

    def _parse_36kr(
        self, soup: BeautifulSoup, source_name: str, lang: str
    ) -> list[NewsItem]:
        """解析 36Kr 文章列表。"""
        items: list[NewsItem] = []

        # 36Kr 的文章通常包含在特定的 CSS 类中
        articles = soup.find_all("a", class_=re.compile(r"article|item|flow"))
        if not articles:
            articles = soup.find_all("a", href=re.compile(r"/p/\d+"))

        for article in articles[:20]:
            title = article.get_text(strip=True)
            href = article.get("href", "")

            if not title or not href:
                continue

            if not self.filter_by_keywords(title):
                continue

            if not href.startswith("http"):
                href = f"https://36kr.com{href}"

            item = NewsItem(
                title=title[:200],
                content=title,
                source=self.source_name,
                url=href,
                published_at=datetime.now(timezone.utc),
                author=source_name,
                language=lang,
            )

            self.tag_products(item)
            items.append(item)

        return items

    def _parse_generic_html(
        self,
        soup: BeautifulSoup,
        base_url: str,
        source_name: str,
        lang: str,
    ) -> list[NewsItem]:
        """通用 HTML 解析，查找包含关键词的链接。"""
        items: list[NewsItem] = []

        for link in soup.find_all("a", href=True):
            text = link.get_text(strip=True)
            if len(text) < 10:
                continue

            if not self.filter_by_keywords(text):
                continue

            href = link["href"]
            if not href.startswith("http"):
                # 构建绝对 URL
                from urllib.parse import urljoin
                href = urljoin(base_url, href)

            item = NewsItem(
                title=text[:200],
                content=text,
                source=self.source_name,
                url=href,
                published_at=datetime.now(timezone.utc),
                author=source_name,
                language=lang,
            )

            self.tag_products(item)
            items.append(item)

        return items

    # ===== 工具方法 =====

    @staticmethod
    def _strip_html(html_text: str) -> str:
        """移除 HTML 标签。"""
        if not html_text:
            return ""
        soup = BeautifulSoup(html_text, "lxml")
        return soup.get_text(separator=" ", strip=True)

    @staticmethod
    def _parse_feed_time(entry) -> datetime:
        """解析 RSS feed 条目的发布时间。"""
        # 优先用 published_parsed
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            from time import mktime
            try:
                return datetime.fromtimestamp(
                    mktime(entry.published_parsed), tz=timezone.utc
                )
            except (OverflowError, ValueError, OSError):
                pass

        # 尝试 updated_parsed
        if hasattr(entry, "updated_parsed") and entry.updated_parsed:
            from time import mktime
            try:
                return datetime.fromtimestamp(
                    mktime(entry.updated_parsed), tz=timezone.utc
                )
            except (OverflowError, ValueError, OSError):
                pass

        # 尝试解析 published 字符串
        published_str = getattr(entry, "published", "") or getattr(entry, "updated", "")
        if published_str:
            try:
                return parsedate_to_datetime(published_str).astimezone(timezone.utc)
            except (ValueError, TypeError):
                pass
            try:
                return datetime.fromisoformat(
                    published_str.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                pass

        return datetime.now(timezone.utc)
