"""
数据去重与清洗处理器。

功能：
1. 基于 URL 精确去重
2. 基于标题相似度模糊去重
3. 按互动量排序
4. 按产品分类
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from collectors.base import NewsItem


class Deduplicator:
    """新闻条目去重器。"""

    def __init__(self, similarity_threshold: float = 0.75):
        """
        Args:
            similarity_threshold: 标题相似度阈值，超过此值视为重复（0-1）。
        """
        self.similarity_threshold = similarity_threshold

    def deduplicate(self, items: list[NewsItem]) -> list[NewsItem]:
        """
        执行完整的去重流程：
        1. URL 精确去重
        2. 标题相似度模糊去重
        3. 保留互动量更高的条目
        """
        # Step 1: URL 去重
        url_deduped = self._deduplicate_by_url(items)

        # Step 2: 标题相似度去重
        title_deduped = self._deduplicate_by_title(url_deduped)

        return title_deduped

    def _deduplicate_by_url(self, items: list[NewsItem]) -> list[NewsItem]:
        """基于 URL 精确去重，保留互动量更高的条目。"""
        url_map: dict[str, NewsItem] = {}

        for item in items:
            normalized_url = self._normalize_url(item.url)

            if normalized_url in url_map:
                existing = url_map[normalized_url]
                # 保留互动量更高的
                if item.engagement_score > existing.engagement_score:
                    url_map[normalized_url] = item
            else:
                url_map[normalized_url] = item

        return list(url_map.values())

    def _deduplicate_by_title(self, items: list[NewsItem]) -> list[NewsItem]:
        """基于标题相似度模糊去重。"""
        if not items:
            return items

        # 按互动量降序排列，优先保留高互动量条目
        sorted_items = sorted(items, key=lambda x: x.engagement_score, reverse=True)
        kept: list[NewsItem] = []

        for item in sorted_items:
            is_duplicate = False
            normalized_title = self._normalize_title(item.title)

            for kept_item in kept:
                kept_title = self._normalize_title(kept_item.title)
                similarity = SequenceMatcher(
                    None, normalized_title, kept_title
                ).ratio()

                if similarity >= self.similarity_threshold:
                    is_duplicate = True
                    # 合并标签
                    for tag in item.tags:
                        if tag not in kept_item.tags:
                            kept_item.tags.append(tag)
                    break

            if not is_duplicate:
                kept.append(item)

        return kept

    @staticmethod
    def _normalize_url(url: str) -> str:
        """规范化 URL（去除 tracking 参数等）。"""
        # 移除常见的 tracking 参数
        url = re.sub(r"[?&](utm_\w+|ref|source|fbclid|gclid)=[^&]*", "", url)
        # 移除尾部斜杠
        url = url.rstrip("/")
        # 统一 http/https
        url = url.replace("http://", "https://")
        return url

    @staticmethod
    def _normalize_title(title: str) -> str:
        """规范化标题用于比较。"""
        # 转小写
        title = title.lower().strip()
        # 移除特殊字符
        title = re.sub(r"[^\w\s\u4e00-\u9fff]", "", title)
        # 压缩空白
        title = re.sub(r"\s+", " ", title)
        return title


def sort_by_engagement(items: list[NewsItem]) -> list[NewsItem]:
    """按综合互动分数降序排列。"""
    return sorted(items, key=lambda x: x.engagement_score, reverse=True)


def group_by_product(items: list[NewsItem]) -> dict[str, list[NewsItem]]:
    """按产品标签分组。"""
    groups: dict[str, list[NewsItem]] = {}

    for item in items:
        if not item.tags:
            groups.setdefault("未分类", []).append(item)
        else:
            for tag in item.tags:
                groups.setdefault(tag, []).append(item)

    # 每组内按互动量排序
    for key in groups:
        groups[key] = sort_by_engagement(groups[key])

    return groups


def group_by_source(items: list[NewsItem]) -> dict[str, list[NewsItem]]:
    """按数据来源分组。"""
    groups: dict[str, list[NewsItem]] = {}

    for item in items:
        groups.setdefault(item.source, []).append(item)

    return groups


def filter_kol_items(items: list[NewsItem]) -> list[NewsItem]:
    """筛选出 KOL 发布的内容。"""
    return [item for item in items if item.is_kol]
