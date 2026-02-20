# 🤖 Get-LLM-News

> AI 编程工具社交媒体舆情采集系统 — 自动从主流社交媒体采集 Claude、Codex、GitHub Copilot 等 AI 编程工具的最新动态和 KOL 观点。

## ✨ 功能特点

- **5 大数据源**：Twitter/X、Reddit、Hacker News、微博/知乎、技术新闻站
- **KOL 追踪**：预设 AI 领域头部 KOL 列表，自动标记 KOL 内容并加权排序
- **智能去重**：URL 精确去重 + 标题相似度模糊去重
- **LLM 摘要**：使用 Claude/GPT 生成今日热点、产品动态、KOL 观点、趋势分析
- **Markdown 日报**：自动生成结构化的 Markdown 格式日报
- **定时运行**：通过 GitHub Actions 每日自动采集并提交报告
- **零成本起步**：Hacker News + Reddit + 技术新闻站无需 API Key 即可运行

## 📦 关注的产品

| 产品 | 关键词 |
|------|--------|
| 🟠 Claude | Claude, Claude Code, Claude Opus, Anthropic |
| 🔵 GitHub Copilot | GitHub Copilot, Copilot Chat, Copilot Agent |
| 🟢 Codex | OpenAI Codex, Codex CLI, Codex agent |
| 🟣 Cursor | Cursor IDE, Cursor AI |
| 🩷 Windsurf | Windsurf, Codeium |
| ⚪ 其他 | AI coding, vibe coding, agentic coding |

## 🚀 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/your-username/Get-LLM-News.git
cd Get-LLM-News
```

### 2. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
```

### 3. 配置环境变量（可选）

```bash
cp .env.example .env
# 编辑 .env 填入各平台的 API Key
```

> 💡 **没有 API Key 也能运行！** Hacker News 和技术新闻站无需任何 API Key。

### 4. 运行

```bash
# 最简运行：只采集 Hacker News（无需任何 API Key）
python main.py --sources hackernews --dry-run

# 采集 Hacker News + 技术新闻
python main.py --sources hackernews,tech_news --dry-run

# 完整运行（需要各平台 API Key + LLM API Key）
python main.py

# 回溯 3 天数据
python main.py --days 3

# 调试模式
python main.py --sources hackernews --dry-run --log-level DEBUG
```

### 5. 查看报告

生成的日报在 `reports/` 目录下，格式为 `YYYY-MM-DD.md`。

## ⚙️ 配置说明

### 数据源配置

编辑 `config/settings.yaml` 调整：

- **关注的产品和关键词**
- **互动量筛选阈值**（过滤低质量内容）
- **LLM 模型选择**
- **输出格式**

### KOL 列表

编辑 `config/kol_list.yaml` 管理各平台的 KOL 列表。KOL 按 S/A/B 三个等级分类：

- **S 级**：3x 权重（如 @karpathy, @sama, Cursor CEO 等）
- **A 级**：2x 权重
- **B 级**：1.5x 权重

## 🔑 API Key 申请指南

### 必需（如需完整功能）

| 平台 | 申请地址 | 费用 | 用途 |
|------|----------|------|------|
| **Anthropic Claude** | [console.anthropic.com](https://console.anthropic.com/) | 按量付费 | LLM 智能摘要 |
| **OpenAI** (备用) | [platform.openai.com](https://platform.openai.com/api-keys) | 按量付费 | Claude 备用 |

### 可选

| 平台 | 申请地址 | 费用 | 用途 |
|------|----------|------|------|
| **Twitter/X** | [developer.twitter.com](https://developer.twitter.com/en/portal) | 免费/付费额度 | 推文采集 |
| **Reddit** | [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps/) | 完全免费 | 更稳定的 Reddit 采集 |

### 无需 API Key ✅

- **Hacker News**：使用 Algolia Search API（完全公开）
- **技术新闻站**：通过 RSS 采集（公开数据）
- **Reddit**：有降级 JSON 模式（功能受限）

## 🔄 GitHub Actions 自动化

### 配置 Secrets

在仓库 Settings → Secrets and variables → Actions 中添加：

```
TWITTER_BEARER_TOKEN    # Twitter API v2 Bearer Token
REDDIT_CLIENT_ID        # Reddit App Client ID
REDDIT_CLIENT_SECRET    # Reddit App Client Secret
ANTHROPIC_API_KEY       # Claude API Key
OPENAI_API_KEY          # OpenAI API Key (可选备用)
WEIBO_COOKIE            # 微博登录 Cookie (可选)
ZHIHU_COOKIE            # 知乎登录 Cookie (可选)
```

### 运行方式

- **自动运行**：每天北京时间 09:00 自动执行
- **手动触发**：GitHub → Actions → Daily LLM News Collection → Run workflow

### 定制定时计划

编辑 `.github/workflows/daily_news.yml` 中的 cron 表达式：

```yaml
schedule:
  - cron: '0 1 * * *'   # UTC 01:00 = 北京时间 09:00
  - cron: '0 9 * * *'   # 可加第二个时间点，如 UTC 09:00 = 北京时间 17:00
```

## 📁 项目结构

```
Get-LLM-News/
├── config/
│   ├── settings.yaml          # 全局配置（关键词、频率、LLM 设置）
│   └── kol_list.yaml          # KOL 账号列表（按平台分类）
├── collectors/
│   ├── base.py                # 采集器基类 + NewsItem 数据模型
│   ├── hackernews.py          # Hacker News 采集器（公开API）
│   ├── reddit.py              # Reddit 采集器（API/降级）
│   ├── twitter.py             # Twitter/X 采集器（需API）
│   ├── weibo_zhihu.py         # 微博/知乎采集器（HTTP+Cookie）
│   └── tech_news.py           # 技术新闻站采集器（RSS/HTML）
├── processors/
│   ├── dedup.py               # 去重 + 分组 + 排序
│   └── summarizer.py          # LLM 智能摘要生成
├── output/
│   └── markdown_report.py     # Markdown 日报生成器
├── reports/                   # 生成的日报（自动提交到 Git）
├── main.py                    # 入口脚本（CLI）
├── requirements.txt           # Python 依赖
├── .env.example               # 环境变量模板
├── .github/
│   └── workflows/
│       └── daily_news.yml     # GitHub Actions 定时任务
└── README.md
```

## 📊 日报示例

生成的日报包含以下板块：

1. **📊 数据概览**：采集统计表格
2. **🔥 今日摘要**：LLM 生成的热点总结
3. **📦 按产品分类**：Claude / Copilot / Codex / Cursor / Windsurf
4. **💬 KOL 观点精选**：头部 KOL 的核心观点
5. **📰 按来源详情**：各平台的详细条目
6. **📈 统计信息**：产品提及频次 + 来源分布

## 🛠️ 开发

```bash
# 安装开发依赖
pip install -r requirements.txt

# 测试单个采集器
python -c "
import asyncio
from collectors.base import load_settings, load_kol_list
from collectors.hackernews import HackerNewsCollector

async def test():
    settings = load_settings()
    kol = load_kol_list()
    c = HackerNewsCollector(settings, kol)
    items = await c.collect()
    for item in items[:5]:
        print(f'[{item.engagement}] {item.title[:80]}')
        print(f'  URL: {item.url}')
        print(f'  Tags: {item.tags}')
        print()

asyncio.run(test())
"
```

## 📝 命令行参数

```
Usage: main.py [OPTIONS]

Options:
  -s, --sources TEXT          数据源列表（逗号分隔）
  -d, --days INTEGER          回溯天数
  --dry-run                   不调用 LLM，仅采集
  -n, --max-items INTEGER     每份报告最大条目数
  -l, --log-level [DEBUG|INFO|WARNING|ERROR]
  --help                      显示帮助
```

## 📜 License

MIT
