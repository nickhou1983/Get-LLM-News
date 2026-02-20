"""
LLM 智能摘要生成器。

使用 Claude (Anthropic) 或 GPT (OpenAI) 对采集到的信息进行：
1. 逐条一句话摘要
2. 按产品分类的趋势总结
3. KOL 核心观点提炼
4. 当日热点判断和整体分析

支持 Token 预算控制，避免成本失控。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime

from collectors.base import NewsItem

logger = logging.getLogger(__name__)


# ===== Prompt 模板 =====

DAILY_SUMMARY_PROMPT = """\
你是一个 AI 编程工具行业分析师。请根据以下从社交媒体和技术新闻采集到的信息，生成一份结构化的中文日报摘要。

## 采集到的信息

{items_text}

## 要求

请生成以下内容（使用中文）：

### 1. 今日热点（2-3条最重要的新闻/动态）
- 每条热点用 1-2 句话概括
- 标注相关产品和来源

### 2. 产品动态总结
针对每个被提及的产品（Claude, GitHub Copilot, Codex, Cursor, Windsurf 等），总结：
- 新功能/更新
- 用户反馈和评价
- 已知问题或争议

### 3. KOL 核心观点
提炼 KOL（标记为 [KOL] 的条目）的关键观点，包括：
- 谁说了什么（简短引述）
- 观点的核心立场

### 4. 趋势分析
基于所有采集的信息，分析：
- 行业趋势信号
- 值得关注的变化
- 对开发者的建议

### 5. 情感分析概览
对主要产品的舆情风向做简短判断（正面/中性/负面）

注意：
- 保持客观中立
- 用中文输出
- 不要编造信息，仅基于提供的数据分析
- 如果某个维度没有相关信息，标注"暂无相关数据"
"""

ITEM_SUMMARY_PROMPT = """\
请为以下内容生成一句话中文摘要（不超过50字），并判断情感倾向（positive/neutral/negative）。

标题: {title}
内容: {content}
来源: {source}
作者: {author}

请用以下 JSON 格式回复：
{{"summary": "一句话摘要", "sentiment": "positive/neutral/negative"}}
"""


class Summarizer:
    """LLM 智能摘要生成器。"""

    def __init__(self, settings: dict):
        self.settings = settings
        summarizer_cfg = settings.get("summarizer", {})
        self.provider = os.getenv("LLM_PROVIDER", summarizer_cfg.get("provider", "claude"))
        self.claude_model = summarizer_cfg.get("claude_model", "claude-sonnet-4-20250514")
        self.openai_model = summarizer_cfg.get("openai_model", "gpt-4o")
        self.max_tokens = summarizer_cfg.get("max_tokens", 4096)
        self.temperature = summarizer_cfg.get("temperature", 0.3)

    async def generate_daily_summary(self, items: list[NewsItem]) -> str:
        """
        生成日报摘要文本。

        Args:
            items: 去重后的 NewsItem 列表

        Returns:
            Markdown 格式的摘要文本
        """
        if not items:
            return "今日暂无相关信息采集到。"

        # 构建输入文本
        items_text = self._format_items_for_prompt(items)
        prompt = DAILY_SUMMARY_PROMPT.format(items_text=items_text)

        # 调用 LLM
        summary = await self._call_llm(prompt)
        return summary

    async def summarize_items(self, items: list[NewsItem]) -> list[NewsItem]:
        """
        为每条 NewsItem 生成一句话摘要和情感分析。

        直接修改传入的 items（设置 summary 和 sentiment 字段）。
        批量处理以节省 API 调用。
        """
        if not items:
            return items

        # 批量处理：将多条合并为一次 API 调用
        batch_size = 10
        for i in range(0, len(items), batch_size):
            batch = items[i : i + batch_size]
            await self._summarize_batch(batch)

        return items

    async def _summarize_batch(self, items: list[NewsItem]) -> None:
        """批量为一组条目生成摘要。"""
        batch_prompt = "请为以下每条内容生成一句话中文摘要（不超过50字）和情感倾向判断。\n\n"

        for idx, item in enumerate(items):
            batch_prompt += f"## 条目 {idx + 1}\n"
            batch_prompt += f"标题: {item.title[:200]}\n"
            batch_prompt += f"内容: {item.content[:300]}\n"
            batch_prompt += f"来源: {item.source}\n\n"

        batch_prompt += (
            "\n请用 JSON 数组格式回复，每个元素包含 index, summary, sentiment 字段。\n"
            '例如: [{"index": 1, "summary": "摘要", "sentiment": "positive"}]\n'
            "只返回 JSON，不要其他文本。"
        )

        try:
            response = await self._call_llm(batch_prompt)
            # 尝试解析 JSON
            # 移除可能的 markdown 代码块标记
            clean_response = response.strip()
            if clean_response.startswith("```"):
                clean_response = clean_response.split("\n", 1)[-1]
                if clean_response.endswith("```"):
                    clean_response = clean_response[:-3]

            results = json.loads(clean_response)

            for result in results:
                idx = result.get("index", 0) - 1
                if 0 <= idx < len(items):
                    items[idx].summary = result.get("summary", "")
                    items[idx].sentiment = result.get("sentiment", "neutral")

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"批量摘要解析失败: {e}")
            # 降级：设置默认值
            for item in items:
                if not item.summary:
                    item.summary = item.title[:50]
                if not item.sentiment:
                    item.sentiment = "neutral"

    async def _call_llm(self, prompt: str) -> str:
        """调用 LLM API。"""
        if self.provider == "claude":
            return await self._call_claude(prompt)
        elif self.provider == "openai":
            return await self._call_openai(prompt)
        else:
            logger.warning(f"未知 LLM 提供商: {self.provider}，尝试 Claude")
            return await self._call_claude(prompt)

    async def _call_claude(self, prompt: str) -> str:
        """调用 Anthropic Claude API。"""
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.warning("未配置 ANTHROPIC_API_KEY，跳过 LLM 摘要")
            return self._generate_fallback_summary(prompt)

        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=api_key)

            message = await client.messages.create(
                model=self.claude_model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=[
                    {"role": "user", "content": prompt}
                ],
            )

            return message.content[0].text

        except ImportError:
            logger.warning("anthropic 库未安装，尝试 OpenAI 作为备份")
            return await self._call_openai(prompt)
        except Exception as e:
            logger.error(f"Claude API 调用失败: {e}")
            # Fallback to OpenAI
            if os.getenv("OPENAI_API_KEY"):
                logger.info("切换到 OpenAI 作为备份")
                return await self._call_openai(prompt)
            return self._generate_fallback_summary(prompt)

    async def _call_openai(self, prompt: str) -> str:
        """调用 OpenAI API。"""
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            logger.warning("未配置 OPENAI_API_KEY，跳过 LLM 摘要")
            return self._generate_fallback_summary(prompt)

        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key)

            response = await client.chat.completions.create(
                model=self.openai_model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=[
                    {
                        "role": "system",
                        "content": "你是一个 AI 编程工具行业分析师，擅长从社交媒体和新闻中提炼关键信息。",
                    },
                    {"role": "user", "content": prompt},
                ],
            )

            return response.choices[0].message.content or ""

        except ImportError:
            logger.error("openai 库未安装")
            return self._generate_fallback_summary(prompt)
        except Exception as e:
            logger.error(f"OpenAI API 调用失败: {e}")
            return self._generate_fallback_summary(prompt)

    def _format_items_for_prompt(self, items: list[NewsItem]) -> str:
        """将 NewsItem 列表格式化为 prompt 输入文本。"""
        lines = []
        for idx, item in enumerate(items[:50], 1):  # 限制最多 50 条
            kol_tag = " [KOL]" if item.is_kol else ""
            products = ", ".join(item.tags) if item.tags else "未分类"
            lines.append(
                f"### {idx}. [{item.source}]{kol_tag} {item.title}\n"
                f"- 作者: {item.author} ({item.author_handle})\n"
                f"- 产品: {products}\n"
                f"- 互动: 👍{item.engagement} 💬{item.comments_count}\n"
                f"- 链接: {item.url}\n"
                f"- 内容摘要: {item.content[:300]}\n"
            )
        return "\n".join(lines)

    @staticmethod
    def _generate_fallback_summary(prompt: str) -> str:
        """当 LLM API 不可用时的降级摘要（简单的统计信息）。"""
        return (
            "> ⚠️ LLM API 未配置或调用失败，以下为原始数据汇总。\n"
            "> 请配置 ANTHROPIC_API_KEY 或 OPENAI_API_KEY 以获得智能摘要。\n\n"
            "请查看下方各数据源的详细条目。"
        )
