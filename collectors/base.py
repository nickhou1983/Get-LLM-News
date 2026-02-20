"""
采集器基类与数据模型定义。

所有平台采集器都继承 BaseCollector，实现统一的 collect() 接口。
NewsItem 是采集到的每条信息的标准数据结构。
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_settings() -> dict:
    """加载全局配置文件。"""
    settings_path = PROJECT_ROOT / "config" / "settings.yaml"
    with open(settings_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_kol_list() -> dict:
    """加载 KOL 列表配置。"""
    kol_path = PROJECT_ROOT / "config" / "kol_list.yaml"
    with open(kol_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_all_keywords(settings: dict) -> list[str]:
    """从配置中提取所有关注的关键词（去重）。"""
    keywords = []
    for product in settings.get("products", []):
        keywords.extend(product.get("keywords", []))
    return list(dict.fromkeys(keywords))  # 保持顺序去重


def match_product(text: str, settings: dict) -> list[str]:
    """匹配文本中提到的产品，返回产品名列表。"""
    matched = []
    text_lower = text.lower()
    for product in settings.get("products", []):
        for keyword in product.get("keywords", []):
            if keyword.lower() in text_lower:
                matched.append(product["name"])
                break
    return matched


@dataclass
class NewsItem:
    """
    采集到的单条信息的标准数据结构。

    所有采集器都需要将平台原始数据转换为此结构。
    """

    # 必填字段
    title: str                          # 标题/推文前100字
    content: str                        # 完整内容
    source: str                         # 来源平台: hackernews/reddit/twitter/weibo/zhihu/tech_news
    url: str                            # 原始链接
    published_at: datetime              # 发布时间 (UTC)

    # 可选字段
    author: str = ""                    # 作者名
    author_handle: str = ""             # 作者平台 ID (如 Twitter handle)
    engagement: int = 0                 # 互动量 (点赞/赞同/分数)
    comments_count: int = 0             # 评论数
    tags: list[str] = field(default_factory=list)   # 匹配到的产品标签
    is_kol: bool = False                # 是否为 KOL 发布
    kol_tier: str = ""                  # KOL 等级: S/A/B
    summary: str = ""                   # LLM 生成的摘要（后处理阶段填充）
    sentiment: str = ""                 # 情感分析: positive/neutral/negative
    language: str = "en"                # 内容语言: en/zh

    # 内部字段
    collected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        """转为字典（JSON 序列化友好）。"""
        d = asdict(self)
        d["published_at"] = self.published_at.isoformat()
        d["collected_at"] = self.collected_at.isoformat()
        return d

    @property
    def engagement_score(self) -> float:
        """综合互动分数，用于排序。"""
        # KOL 加权
        kol_multiplier = {"S": 3.0, "A": 2.0, "B": 1.5}.get(self.kol_tier, 1.0)
        return (self.engagement + self.comments_count * 2) * kol_multiplier

    def __repr__(self) -> str:
        return (
            f"NewsItem(source={self.source!r}, author={self.author!r}, "
            f"engagement={self.engagement}, title={self.title[:50]!r}...)"
        )


class BaseCollector(ABC):
    """
    采集器抽象基类。

    所有平台采集器必须继承此类并实现 collect() 方法。
    """

    def __init__(self, settings: dict, kol_config: dict):
        self.settings = settings
        self.kol_config = kol_config
        self.keywords = get_all_keywords(settings)
        self.logger = logging.getLogger(self.__class__.__name__)

        # 采集配置
        collection_cfg = settings.get("collection", {})
        self.lookback_days: int = collection_cfg.get("lookback_days", 1)
        self.max_items: int = collection_cfg.get("max_items_per_source", 30)

    @property
    @abstractmethod
    def source_name(self) -> str:
        """数据源名称，如 'hackernews', 'reddit', 'twitter'。"""
        ...

    @abstractmethod
    async def collect(self) -> list[NewsItem]:
        """
        执行采集，返回 NewsItem 列表。

        子类实现此方法，完成：
        1. 调用平台 API 或爬取网页
        2. 解析原始数据
        3. 过滤低互动量内容
        4. 匹配产品标签
        5. 标记 KOL 内容
        6. 返回 NewsItem 列表
        """
        ...

    def filter_by_keywords(self, text: str) -> bool:
        """检查文本是否包含任何关注的关键词。"""
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in self.keywords)

    def tag_products(self, item: NewsItem) -> None:
        """为 NewsItem 匹配产品标签。"""
        text = f"{item.title} {item.content}"
        item.tags = match_product(text, self.settings)

    def detect_language(self, text: str) -> str:
        """简单的语言检测（中文/英文）。"""
        chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        return "zh" if chinese_chars > len(text) * 0.1 else "en"

    async def safe_collect(self) -> list[NewsItem]:
        """安全的采集包装器，捕获异常避免单个源失败影响整体。"""
        try:
            self.logger.info(f"开始采集 [{self.source_name}] ...")
            items = await self.collect()
            self.logger.info(f"[{self.source_name}] 采集完成，共 {len(items)} 条")
            return items
        except Exception as e:
            self.logger.error(f"[{self.source_name}] 采集失败: {e}", exc_info=True)
            return []
