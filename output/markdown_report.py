"""
Markdown 日报生成器。

使用 Jinja2 模板引擎生成结构化的 Markdown 日报：
- 今日热点（LLM 生成）
- 按产品分类的详细条目
- KOL 观点列表
- 技术新闻汇总
- 数据统计

输出到 reports/YYYY-MM-DD.md
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, BaseLoader

from collectors.base import NewsItem, PROJECT_ROOT
from processors.dedup import (
    group_by_product,
    group_by_source,
    filter_kol_items,
    sort_by_engagement,
)

logger = logging.getLogger(__name__)


# ===== Jinja2 Markdown 模板 =====

REPORT_TEMPLATE = """\
# 🤖 AI 编程工具日报 — {{ date }}

> 自动采集自 Twitter/X、Reddit、Hacker News、微博/知乎、技术新闻站
> 生成时间: {{ generated_at }}

---

## 📊 数据概览

| 指标 | 数值 |
|------|------|
| 采集条目总数 | {{ total_items }} |
| 涉及数据源 | {{ sources | join(', ') }} |
| KOL 相关 | {{ kol_count }} 条 |
| 涵盖产品 | {{ products | join(', ') }} |

---

## 🔥 今日摘要

{{ daily_summary }}

---

## 📦 按产品分类

{% for product, items in product_groups.items() %}
### {{ product_emoji(product) }} {{ product }}（{{ items | length }} 条）

{% for item in items[:10] %}
{{ loop.index }}. {% if item.is_kol %}🌟 **[KOL]**{% endif %} **{{ item.title | truncate(120) }}**
   - 来源: {{ source_label(item.source) }} | 作者: {{ item.author }}{% if item.author_handle %} ({{ item.author_handle }}){% endif %}
   - 互动: 👍 {{ item.engagement }} · 💬 {{ item.comments_count }}
   {% if item.summary %}- 📝 {{ item.summary }}{% endif %}
   - 🔗 [原文链接]({{ item.url }})

{% endfor %}
{% endfor %}

---

## 💬 KOL 观点精选

{% if kol_items %}
{% for item in kol_items[:15] %}
### {{ loop.index }}. {{ item.author }}{% if item.author_handle %} ({{ item.author_handle }}){% endif %} — {{ kol_tier_label(item.kol_tier) }}

> {{ item.content | truncate(300) }}

- 来源: {{ source_label(item.source) }} | 互动: 👍 {{ item.engagement }} · 💬 {{ item.comments_count }}
- 产品: {{ item.tags | join(', ') if item.tags else '综合' }}
- 🔗 [原文链接]({{ item.url }})

{% endfor %}
{% else %}
_今日暂无 KOL 相关内容采集到。_
{% endif %}

---

## 📰 按来源详情

{% for source, items in source_groups.items() %}
### {{ source_label(source) }}（{{ items | length }} 条）

{% for item in items[:8] %}
- {% if item.is_kol %}🌟{% endif %} [{{ item.title | truncate(80) }}]({{ item.url }}) — {{ item.author }} · 👍{{ item.engagement }}{% if item.summary %} — _{{ item.summary }}_{% endif %}
{% endfor %}

{% endfor %}

---

## 📈 统计信息

### 产品提及频次

| 产品 | 提及次数 | 平均互动量 |
|------|----------|------------|
{% for product, items in product_groups.items() %}
| {{ product }} | {{ items | length }} | {{ (items | map(attribute='engagement') | sum / items | length) | round(0) | int }} |
{% endfor %}

### 来源分布

| 来源 | 条目数 |
|------|--------|
{% for source, items in source_groups.items() %}
| {{ source_label(source) }} | {{ items | length }} |
{% endfor %}

---

<sub>📌 由 [Get-LLM-News](https://github.com/your-repo/Get-LLM-News) 自动生成 | 数据截止: {{ date }}</sub>
"""


class MarkdownReportGenerator:
    """Markdown 日报生成器。"""

    def __init__(self, settings: dict):
        self.settings = settings
        output_cfg = settings.get("output", {})
        self.report_dir = PROJECT_ROOT / output_cfg.get("report_dir", "reports")
        self.filename_template = output_cfg.get("filename_template", "%Y-%m-%d.md")

        # 设置 Jinja2 环境
        self.env = Environment(loader=BaseLoader(), autoescape=False)
        self.env.globals["product_emoji"] = self._product_emoji
        self.env.globals["source_label"] = self._source_label
        self.env.globals["kol_tier_label"] = self._kol_tier_label
        self.env.filters["truncate"] = self._truncate

    def generate(
        self,
        items: list[NewsItem],
        daily_summary: str = "",
        date: str | None = None,
    ) -> str:
        """
        生成 Markdown 日报内容。

        Args:
            items: 去重后的 NewsItem 列表
            daily_summary: LLM 生成的摘要文本
            date: 报告日期（默认为今天）

        Returns:
            Markdown 格式的报告字符串
        """
        if not date:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 数据处理
        product_groups = group_by_product(items)
        source_groups = group_by_source(items)
        kol_items = sort_by_engagement(filter_kol_items(items))

        # 所有涉及的来源和产品
        sources = list(source_groups.keys())
        products = [p for p in product_groups.keys() if p != "未分类"]

        # 渲染模板
        template = self.env.from_string(REPORT_TEMPLATE)
        report = template.render(
            date=date,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            total_items=len(items),
            sources=sources,
            products=products if products else ["暂无"],
            kol_count=len(kol_items),
            daily_summary=daily_summary or "_LLM 摘要未生成，请查看详细条目。_",
            product_groups=product_groups,
            source_groups=source_groups,
            kol_items=kol_items,
        )

        return report

    def save(
        self,
        items: list[NewsItem],
        daily_summary: str = "",
        date: str | None = None,
    ) -> Path:
        """
        生成并保存 Markdown 日报文件。

        Returns:
            保存的文件路径
        """
        if not date:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        report_content = self.generate(items, daily_summary, date)

        # 确保输出目录存在
        self.report_dir.mkdir(parents=True, exist_ok=True)

        # 生成文件名
        filename = datetime.strptime(date, "%Y-%m-%d").strftime(self.filename_template)
        filepath = self.report_dir / filename

        filepath.write_text(report_content, encoding="utf-8")
        logger.info(f"日报已保存到: {filepath}")

        return filepath

    # ===== 辅助方法 =====

    @staticmethod
    def _product_emoji(product: str) -> str:
        """产品名称对应的 emoji。"""
        mapping = {
            "Claude": "🟠",
            "GitHub Copilot": "🔵",
            "Codex": "🟢",
            "Cursor": "🟣",
            "Windsurf": "🩷",
            "Other AI Coding": "⚪",
            "未分类": "📎",
        }
        return mapping.get(product, "📦")

    @staticmethod
    def _source_label(source: str) -> str:
        """来源名称的中文标签。"""
        mapping = {
            "hackernews": "🔶 Hacker News",
            "reddit": "🟧 Reddit",
            "twitter": "🐦 Twitter/X",
            "weibo": "🔴 微博",
            "zhihu": "🔵 知乎",
            "weibo_zhihu": "🇨🇳 微博/知乎",
            "tech_news": "📰 技术新闻",
        }
        return mapping.get(source, source)

    @staticmethod
    def _kol_tier_label(tier: str) -> str:
        """KOL 等级标签。"""
        mapping = {
            "S": "⭐⭐⭐ 顶级影响力",
            "A": "⭐⭐ 高影响力",
            "B": "⭐ 影响力",
        }
        return mapping.get(tier, "")

    @staticmethod
    def _truncate(text: str, length: int = 100) -> str:
        """截断文本。"""
        if len(text) <= length:
            return text
        return text[:length] + "..."
