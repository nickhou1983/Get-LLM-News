#!/usr/bin/env python3
"""
Get-LLM-News 主入口脚本。

串联完整的采集 → 去重 → 摘要 → 日报生成流程。

用法:
    python main.py                          # 运行所有数据源
    python main.py --sources hackernews     # 只运行 Hacker News
    python main.py --sources hackernews,reddit  # 运行指定数据源
    python main.py --dry-run                # 不调用 LLM，仅采集数据
    python main.py --days 3                 # 回溯 3 天
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collectors.base import load_settings, load_kol_list, NewsItem
from collectors.hackernews import HackerNewsCollector
from collectors.reddit import RedditCollector
from collectors.twitter import TwitterCollector
from collectors.weibo_zhihu import WeiboZhihuCollector
from collectors.tech_news import TechNewsCollector
from processors.dedup import Deduplicator, sort_by_engagement
from processors.summarizer import Summarizer
from output.markdown_report import MarkdownReportGenerator


# 数据源名称 → 采集器类的映射
COLLECTOR_MAP = {
    "hackernews": HackerNewsCollector,
    "reddit": RedditCollector,
    "twitter": TwitterCollector,
    "weibo_zhihu": WeiboZhihuCollector,
    "tech_news": TechNewsCollector,
}

# 所有数据源
ALL_SOURCES = list(COLLECTOR_MAP.keys())


def setup_logging(level: str = "INFO") -> None:
    """配置日志。"""
    from rich.logging import RichHandler

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )


async def run_pipeline(
    sources: list[str],
    days: int = 1,
    dry_run: bool = False,
    max_items: int | None = None,
) -> Path | None:
    """
    执行完整的采集管道。

    Args:
        sources: 要采集的数据源列表
        days: 回溯天数
        dry_run: 是否跳过 LLM 摘要
        max_items: 每份报告最大条目数

    Returns:
        生成的报告文件路径，失败返回 None
    """
    logger = logging.getLogger("pipeline")

    # ===== 1. 加载配置 =====
    logger.info("📋 加载配置...")
    settings = load_settings()
    kol_config = load_kol_list()

    # 覆盖回溯天数
    if days:
        settings.setdefault("collection", {})["lookback_days"] = days

    if max_items:
        settings.setdefault("collection", {})["max_items_per_report"] = max_items

    max_report_items = settings.get("collection", {}).get("max_items_per_report", 50)

    # ===== 2. 初始化采集器 =====
    logger.info(f"🔧 初始化采集器: {', '.join(sources)}")
    collectors = []
    for source_name in sources:
        if source_name not in COLLECTOR_MAP:
            logger.warning(f"未知数据源: {source_name}，跳过")
            continue
        collector_cls = COLLECTOR_MAP[source_name]
        collectors.append(collector_cls(settings, kol_config))

    if not collectors:
        logger.error("没有有效的采集器，退出")
        return None

    # ===== 3. 并行采集 =====
    logger.info("🚀 开始采集数据...")
    tasks = [c.safe_collect() for c in collectors]
    results = await asyncio.gather(*tasks)

    # 合并所有结果
    all_items: list[NewsItem] = []
    for result in results:
        all_items.extend(result)

    logger.info(f"📊 采集完成，共 {len(all_items)} 条原始数据")

    if not all_items:
        logger.warning("未采集到任何数据，生成空报告")

    # ===== 4. 去重 =====
    logger.info("🔍 数据去重...")
    deduplicator = Deduplicator(similarity_threshold=0.75)
    unique_items = deduplicator.deduplicate(all_items)
    logger.info(f"去重后: {len(unique_items)} 条（去除 {len(all_items) - len(unique_items)} 条重复）")

    # 排序并截取 top N
    unique_items = sort_by_engagement(unique_items)[:max_report_items]

    # ===== 5. LLM 摘要 =====
    daily_summary = ""
    if not dry_run:
        logger.info("🤖 生成 LLM 智能摘要...")
        summarizer = Summarizer(settings)

        # 生成逐条摘要
        await summarizer.summarize_items(unique_items)

        # 生成日报总结
        daily_summary = await summarizer.generate_daily_summary(unique_items)
        logger.info("✅ LLM 摘要生成完成")
    else:
        logger.info("⏭️ 跳过 LLM 摘要（dry-run 模式）")

    # ===== 6. 生成 Markdown 日报 =====
    logger.info("📝 生成 Markdown 日报...")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_gen = MarkdownReportGenerator(settings)
    report_path = report_gen.save(unique_items, daily_summary, today)

    logger.info(f"✅ 日报已保存: {report_path}")

    # 打印摘要统计
    _print_summary_stats(unique_items, logger)

    return report_path


def _print_summary_stats(items: list[NewsItem], logger: logging.Logger) -> None:
    """打印采集统计信息。"""
    if not items:
        return

    from collections import Counter

    source_counts = Counter(item.source for item in items)
    product_counts: Counter = Counter()
    for item in items:
        for tag in item.tags:
            product_counts[tag] += 1

    kol_count = sum(1 for item in items if item.is_kol)

    logger.info("\n📊 采集统计:")
    logger.info(f"  总条目: {len(items)}")
    logger.info(f"  KOL 条目: {kol_count}")

    logger.info("  按来源:")
    for source, count in source_counts.most_common():
        logger.info(f"    {source}: {count}")

    if product_counts:
        logger.info("  按产品:")
        for product, count in product_counts.most_common():
            logger.info(f"    {product}: {count}")


@click.command()
@click.option(
    "--sources",
    "-s",
    default=",".join(ALL_SOURCES),
    help=f"逗号分隔的数据源列表。可选: {', '.join(ALL_SOURCES)}",
)
@click.option(
    "--days",
    "-d",
    default=1,
    type=int,
    help="回溯天数（采集最近 N 天的数据）",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="不调用 LLM API，仅采集数据和生成原始报告",
)
@click.option(
    "--max-items",
    "-n",
    default=None,
    type=int,
    help="每份报告最大条目数",
)
@click.option(
    "--log-level",
    "-l",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    help="日志级别",
)
def main(
    sources: str,
    days: int,
    dry_run: bool,
    max_items: int | None,
    log_level: str,
) -> None:
    """
    🤖 Get-LLM-News — AI 编程工具社交媒体舆情采集系统

    从 Twitter/X、Reddit、Hacker News、微博/知乎、技术新闻站采集
    Claude、Codex、GitHub Copilot 等 AI 编程工具的最新动态和 KOL 观点。
    """
    setup_logging(log_level)
    logger = logging.getLogger("main")

    # 解析数据源列表
    source_list = [s.strip() for s in sources.split(",") if s.strip()]

    logger.info("=" * 60)
    logger.info("🤖 Get-LLM-News — AI 编程工具舆情采集")
    logger.info("=" * 60)
    logger.info(f"数据源: {', '.join(source_list)}")
    logger.info(f"回溯天数: {days}")
    logger.info(f"Dry-run: {dry_run}")
    logger.info("")

    # 运行异步管道
    report_path = asyncio.run(
        run_pipeline(
            sources=source_list,
            days=days,
            dry_run=dry_run,
            max_items=max_items,
        )
    )

    if report_path:
        logger.info(f"\n🎉 完成！报告路径: {report_path}")
    else:
        logger.error("\n❌ 管道执行失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
