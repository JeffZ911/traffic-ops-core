# 代码需求表（CODE-SPEC）

> 配套文档：PRD-AI-Site-Operator.md v0.9 + CREDENTIALS-SETUP.md v1.0
> 文档版本：v1.3
> 目的：给 AI / 工程师执行的完整技术规格，从这份文档可以直接展开为可运行的代码
> 适用范围：Phase 1.A / 1.B / 1.C
>
> **变更记录**：
> - v1.3 (2026-05)：LLM 从 Claude 改 Gemini；新增 model_catalog 数据表；新增 BaseLLMProvider 抽象；写作 + 质检双模型互检方案；凭证多站隔离方案
> - v1.2 (2026-05)：取消钉钉；Mission Control 页；OutlineAgent 内容多样性；首站 NTE
> - v1.1 (2026-05)：Gemini Nano Banana 图像生成；Settings 页
> - v1.0 (2026-05)：初版

---

## 0. 阅读说明

- 本文档是 **PRD 的工程实现规格**，不重复 PRD 里已有的业务说明
- 每一节标注了 **优先级**（P0=必做，P1=应做，P2=可选）和 **依赖关系**
- 所有"AI 实现"任务，目标是让 AI 编程工具（如 Claude Code、Cursor）能直接基于这份规格写代码
- 涉及外部 API 的部分不写完整代码，只写**接口契约**和**关键约束**

---

## 1. 项目仓库结构

需要创建 **3 个独立 GitHub repo**：

| Repo | 用途 | 技术栈 |
|------|------|-------|
| `traffic-ops-core` | 后端 Pipeline + 数据采集 + 调度 | Python 3.11 |
| `traffic-ops-dashboard` | 只读前端管理界面 | Next.js 15 + Supabase |
| `ntecodex-site`（站点 repo，按域名命名）| Astro 站点本身 | Astro 4+ |

每个站点一个独立 site repo（未来加站时复制此模板）。

### 1.1 traffic-ops-core 目录结构

```
traffic-ops-core/
├── .github/
│   └── workflows/
│       ├── content-pipeline.yml       # 每日内容生产
│       ├── data-collection.yml        # 每日数据采集
│       ├── daily-report.yml           # 每日日报
│       ├── alert-monitor.yml          # 实时告警检查（每小时）
│       └── log-cleanup.yml            # Agent 日志清理（每日）
├── src/
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py                    # Agent 基类
│   │   ├── keyword_research.py        # 关键词初始化调研
│   │   ├── keyword_expansion.py       # 关键词每日扩展
│   │   ├── keyword_selector.py        # 选择今日要写的关键词
│   │   ├── outline.py                 # 大纲生成
│   │   ├── writing.py                 # 正文写作
│   │   ├── qa.py                      # 质检
│   │   ├── image.py                   # 配图
│   │   └── publish.py                 # 发布到 site repo
│   ├── collectors/
│   │   ├── ga4.py
│   │   ├── gsc.py
│   │   ├── adsense.py
│   │   ├── fb_ads.py
│   │   └── cloudflare.py
│   ├── reporters/
│   │   ├── daily_report.py
│   │   └── alert.py
│   ├── decisions/
│   │   └── rules_engine.py            # 决策硬规则
│   ├── db/
│   │   ├── client.py                  # Supabase Client 封装
│   │   ├── models.py                  # Pydantic 模型
│   │   └── migrations/                # SQL migration 文件
│   ├── notifiers/
│   │   └── email.py
│   ├── config/
│   │   ├── loader.py                  # site.config.yaml 加载
│   │   └── prompts/                   # Agent prompt 模板
│   │       ├── outline.md
│   │       ├── writing.md
│   │       ├── qa.md
│   │       └── ...
│   └── utils/
│       ├── logger.py
│       ├── retry.py
│       ├── llm.py                     # Claude API 封装
│       └── git.py                     # site repo 操作
├── tests/
├── pyproject.toml
├── .env.example
└── README.md
```

### 1.2 traffic-ops-dashboard 目录结构

```
traffic-ops-dashboard/
├── app/
│   ├── (auth)/
│   │   └── login/
│   ├── (dashboard)/
│   │   ├── page.tsx                   # 总览
│   │   ├── sites/
│   │   │   └── [domain]/
│   │   │       ├── page.tsx           # 单站详情
│   │   │       ├── keywords/
│   │   │       ├── articles/
│   │   │       │   └── [id]/
│   │   │       ├── agent-runs/
│   │   │       ├── metrics/
│   │   │       └── alerts/
│   │   └── reports/
│   ├── api/                           # 仅必要的 BFF route，主流程走 Supabase 直连
│   └── layout.tsx
├── components/
│   ├── ui/                            # shadcn/ui
│   └── charts/                        # Recharts 封装
├── lib/
│   ├── supabase/
│   │   ├── client.ts
│   │   ├── server.ts
│   │   └── types.ts                   # 由 Supabase CLI 自动生成
│   └── utils.ts
├── public/
├── package.json
├── next.config.js
├── tailwind.config.ts
└── README.md
```

### 1.3 ntecodex-site 目录结构

```
ntecodex-site/
├── src/
│   ├── content/
│   │   ├── config.ts
│   │   └── blog/                      # AI 写入的文章
│   ├── pages/
│   │   ├── index.astro
│   │   ├── [...slug].astro
│   │   ├── tier-list/
│   │   ├── builds/
│   │   ├── guide/
│   │   ├── database/
│   │   └── news/
│   ├── components/
│   ├── layouts/
│   └── styles/
├── public/
│   ├── images/                        # AI 写入的配图
│   ├── ads.txt
│   ├── robots.txt
│   └── favicon.svg
├── astro.config.mjs
├── package.json
└── site.config.yaml                   # 站点元配置
```

---

## 2. Supabase 数据库 Schema【P0】

> 全部 SQL 必须以 migration 文件形式管理（`db/migrations/001_initial.sql` 等），禁止手动建表。

### 2.1 总览（10 张表）

```
sites              站点元数据
keywords           关键词池
article_keywords   文章-关键词关联（多对多）
articles           文章
agent_runs         Agent 执行日志（保留 3 天）
images             配图记录
metrics_raw        原始指标数据（按天分区思想存储）
metrics_daily      日度聚合指标
ad_campaigns       FB 广告系列状态
alerts             告警历史
daily_reports      日报快照
```

### 2.2 详细 Schema

#### 2.2.1 sites — 站点表

```sql
create table sites (
  id          uuid primary key default gen_random_uuid(),
  domain      text unique not null,
  site_name   text not null,
  status      text not null default 'active',  -- active | paused | archived
  config      jsonb not null default '{}',     -- 镜像 site.config.yaml
  owner_id    uuid references auth.users(id),  -- 多用户用
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index idx_sites_owner on sites(owner_id);
```

`config` jsonb 字段示例：

```json
{
  "site_slug": "ntecodex",
  "target_lang": "en",
  "content_strategy": "gacha_guide",
  "game_target": "Neverness to Everness",
  "current_phase": "1.A",

  "content_plan": {
    "daily_articles": 3,
    "min_word_count": 1200,
    "max_word_count": 2500,
    "internal_links_per_article": 3,
    "images_per_article": 3,
    "diversity": {
      "max_type_share_7d": 0.40,
      "min_types_per_batch": 2
    }
  },

  "qa_thresholds": {
    "min_quality_score": 7,
    "max_retry_rounds": 3,
    "consecutive_failure_alert": 5,
    "weekly_pass_rate_min": 0.40
  },

  "text_provider": {
    "provider": "gemini",
    "writing_model": "gemini-3-flash-preview",
    "qa_model": "gemini-3.1-pro-preview",
    "outline_model": "gemini-3-flash-preview",
    "keyword_research_model": "gemini-3.1-flash-lite-preview",
    "report_summary_model": "gemini-3-flash-preview",
    "fallback_provider": null
  },

  "image_provider": {
    "provider": "gemini",
    "model": "gemini-2.5-flash-image",
    "default_aspect_ratio": "16:9",
    "fallback_provider": null,
    "extra_params": {}
  },

  "ad_budget": {
    "daily_max_usd": 20,
    "total_test_usd": 200,
    "loss_stop_threshold_usd": 200
  }
}
```

> ⚠️ **重要**：`site_slug` 字段是**凭证多站隔离方案**的关键。代码会根据 site_slug 拼接环境变量名读取该站专属凭证（如 `NTECODEX_FB_ACCESS_TOKEN`）。详见 §9.3 和 CREDENTIALS-SETUP.md。

> ⚠️ **双模型互检**：`writing_model` 和 `qa_model` 必须不同（前者较便宜的 Flash，后者较强的 Pro），让"写"和"评"互相挑错，避免单模型自评偏差。

#### 2.2.2 keywords — 关键词池

```sql
create table keywords (
  id              uuid primary key default gen_random_uuid(),
  site_id         uuid not null references sites(id) on delete cascade,
  keyword         text not null,
  intent          text,                 -- informational | comparison | how-to | list
  search_volume   integer,              -- 月搜索量（估算）
  competition     numeric(3,2),         -- 0.00-1.00
  status          text not null default 'planned',
                  -- planned | in_progress | completed | skipped | archived
  priority_score  numeric(5,2),         -- 综合打分，调度用
  source          text,                 -- initial_research | gsc_expansion | manual | competitor_gap
  cluster_id      uuid,                 -- 关键词簇 ID，同簇用于内链
  notes           text,
  last_used_at    timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique(site_id, keyword)
);
create index idx_keywords_site_status on keywords(site_id, status);
create index idx_keywords_priority on keywords(site_id, priority_score desc) where status = 'planned';
create index idx_keywords_cluster on keywords(cluster_id);
```

#### 2.2.3 article_keywords — 文章-关键词多对多

```sql
create table article_keywords (
  article_id   uuid not null references articles(id) on delete cascade,
  keyword_id   uuid not null references keywords(id) on delete cascade,
  is_primary   boolean not null default false,  -- 是否主关键词
  primary key (article_id, keyword_id)
);
create index idx_ak_keyword on article_keywords(keyword_id);
```

#### 2.2.4 articles — 文章

```sql
create table articles (
  id               uuid primary key default gen_random_uuid(),
  site_id          uuid not null references sites(id) on delete cascade,
  slug             text not null,
  title            text,
  article_type     text,                        -- build | tier_list | boss_guide | reroll | character_db | weapon_db | news | faq | comparison
  outline          jsonb,                       -- 大纲 JSON
  content_md       text,                        -- 正文 Markdown
  word_count       integer,
  status           text not null default 'draft',
                   -- draft | writing | qa_pending | qa_failed
                   -- | qa_passed | published | archived | failed
  qa_score         numeric(3,1),                -- 0-10
  qa_attempts      integer not null default 0,
  qa_feedback      jsonb,                       -- 最近一次质检反馈
  published_url    text,
  published_at     timestamptz,
  failure_reason   text,
  total_tokens     integer not null default 0,  -- 累计消耗 token
  total_cost_usd   numeric(8,4) not null default 0,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique(site_id, slug)
);
create index idx_articles_site_status on articles(site_id, status);
create index idx_articles_published on articles(site_id, published_at desc);
create index idx_articles_type on articles(site_id, article_type);
```

**状态机约束（应用层 + DB CHECK）**：

```
draft → writing → qa_pending → qa_passed → published
                    ↓
                qa_failed → writing  (重试，attempts++)
                    ↓
                  failed  (attempts >= 3)
```

#### 2.2.5 agent_runs — Agent 执行日志（保留 3 天）

```sql
create table agent_runs (
  id            uuid primary key default gen_random_uuid(),
  site_id       uuid not null references sites(id) on delete cascade,
  article_id    uuid references articles(id) on delete cascade,
  agent_name    text not null,        -- keyword_research | outline | writing | qa | image | publish
  status        text not null,        -- success | failure | retry
  input         jsonb,                -- prompt + 参数
  output        jsonb,                -- 模型返回
  error_msg     text,
  tokens_in     integer,
  tokens_out    integer,
  cost_usd      numeric(8,4),
  duration_ms   integer,
  model         text,                 -- 实际使用的模型 ID，如 gemini-3-flash / gemini-3.1-pro-preview
  created_at    timestamptz not null default now()
);
create index idx_agent_runs_site_time on agent_runs(site_id, created_at desc);
create index idx_agent_runs_article on agent_runs(article_id);
create index idx_agent_runs_cleanup on agent_runs(created_at);  -- 清理用
```

**清理策略**：每日 GitHub Actions 删除 `created_at < now() - interval '3 days'` 的记录。

> ⚠️ 实施清理任务前，需要先做"摘要保留"：每天清理前把当天数据聚合成一行写入 `agent_runs_summary`（见 2.2.11），保留每日的 token 总量、失败率等关键统计，不丢失长期可观测性。

#### 2.2.6 images — 配图

```sql
create table images (
  id            uuid primary key default gen_random_uuid(),
  site_id       uuid not null references sites(id) on delete cascade,
  article_id    uuid references articles(id) on delete cascade,
  prompt        text,
  url           text not null,        -- repo 内路径或 R2 URL
  alt_text      text,
  provider      text,                 -- gemini | replicate | ...
  model         text,                 -- 具体模型 ID
  aspect_ratio  text,
  cost_usd      numeric(8,4),
  created_at    timestamptz not null default now()
);
create index idx_images_article on images(article_id);
```

#### 2.2.7 metrics_raw — 原始指标

```sql
create table metrics_raw (
  id             bigserial primary key,
  site_id        uuid not null references sites(id) on delete cascade,
  source         text not null,       -- ga4 | gsc | adsense | fb_ads | cloudflare
  metric_date    date not null,       -- 数据所属日期（不是采集日期）
  payload        jsonb not null,      -- 原始 API 返回
  fetched_at     timestamptz not null default now()
);
create index idx_metrics_raw_lookup on metrics_raw(site_id, source, metric_date);
```

> 注：raw 数据按月分区可后续加，前期单表足够。保留期 90 天，后续按需调整。

#### 2.2.8 metrics_daily — 日度聚合

```sql
create table metrics_daily (
  site_id              uuid not null references sites(id) on delete cascade,
  metric_date          date not null,

  -- 流量
  sessions             integer,
  pageviews            integer,
  pv_per_session       numeric(5,2),
  avg_duration_sec     integer,
  bounce_rate          numeric(5,4),

  -- AdSense
  adsense_revenue_usd  numeric(10,4),
  adsense_pageviews    integer,
  adsense_impressions  integer,
  adsense_ctr          numeric(5,4),
  page_rpm_usd         numeric(8,4),
  invalid_traffic_pct  numeric(5,4),

  -- FB Ads
  fb_spend_usd         numeric(10,4),
  fb_clicks            integer,
  fb_impressions       integer,
  fb_cpc_usd           numeric(8,4),
  fb_ctr               numeric(5,4),
  fb_frequency         numeric(4,2),

  -- 衍生
  ecpc_usd             numeric(8,4),     -- adsense_revenue / sessions
  roi                  numeric(7,4),     -- (revenue - spend) / spend

  -- SEO
  gsc_clicks           integer,
  gsc_impressions      integer,
  gsc_avg_position     numeric(5,2),

  computed_at          timestamptz not null default now(),
  primary key (site_id, metric_date)
);
create index idx_metrics_daily_date on metrics_daily(metric_date desc);
```

#### 2.2.9 ad_campaigns — FB 广告系列

```sql
create table ad_campaigns (
  id              uuid primary key default gen_random_uuid(),
  site_id         uuid not null references sites(id) on delete cascade,
  fb_campaign_id  text unique not null,
  name            text,
  status          text,                  -- active | paused | archived
  daily_budget    numeric(8,2),
  objective       text,
  last_synced_at  timestamptz,
  created_at      timestamptz not null default now()
);
```

#### 2.2.10 alerts — 告警历史

```sql
create table alerts (
  id            uuid primary key default gen_random_uuid(),
  site_id       uuid references sites(id) on delete cascade,
  level         text not null,           -- info | warning | critical
  category      text not null,           -- adsense_invalid_traffic | fb_overspend | qa_failure | ...
  title         text not null,
  message       text not null,
  context       jsonb,
  acknowledged  boolean not null default false,
  acknowledged_by uuid references auth.users(id),
  acknowledged_at timestamptz,
  created_at    timestamptz not null default now()
);
create index idx_alerts_site_unack on alerts(site_id, acknowledged, created_at desc);
```

#### 2.2.11 agent_runs_summary — Agent 日聚合（清理后保留）

```sql
create table agent_runs_summary (
  site_id           uuid not null references sites(id) on delete cascade,
  summary_date      date not null,
  agent_name        text not null,
  total_runs        integer not null,
  success_count     integer not null,
  failure_count     integer not null,
  total_tokens_in   bigint not null,
  total_tokens_out  bigint not null,
  total_cost_usd    numeric(10,4) not null,
  avg_duration_ms   integer,
  primary key (site_id, summary_date, agent_name)
);
```

#### 2.2.12 daily_reports — 日报快照

```sql
create table daily_reports (
  id            uuid primary key default gen_random_uuid(),
  site_id       uuid not null references sites(id) on delete cascade,
  report_date   date not null,
  markdown      text not null,
  ai_summary    text,
  data_snapshot jsonb,                   -- 当时数据快照
  sent_to       text[],                  -- ['dashboard'] / ['email'] / both
  created_at    timestamptz not null default now(),
  unique(site_id, report_date)
);
```

#### 2.2.13 model_catalog — LLM/图像模型目录（关键 ⚠️）

**目的**：把"系统支持哪些模型"做成数据而不是代码。新模型出来时，前端加一行配置即可，无需改代码 + 重新部署。

```sql
create table model_catalog (
  id                  uuid primary key default gen_random_uuid(),
  provider            text not null,          -- gemini | openai | anthropic
  model_id            text not null,          -- gemini-3.1-pro-preview / gemini-2.5-flash-image
  display_name        text not null,          -- 'Gemini 3.1 Pro (推荐)'
  modality            text not null,          -- text | image
  task_types          text[] not null,        -- ['writing','qa','keyword_research','image_gen','keyword_research_light']
  tier                text,                   -- pro | flash | flash-lite
  input_cost_per_1m   numeric(8,4),           -- 每百万 input token 价格 (USD)
  output_cost_per_1m  numeric(8,4),           -- 每百万 output token 价格 (USD)
  per_image_cost      numeric(8,4),           -- 图像模型用，每张成本 (USD)
  context_window      integer,                -- 上下文窗口 token 数
  supports_json_mode  boolean default false,
  status              text not null default 'active',  -- preview | active | deprecated
  is_recommended      boolean not null default false,
  released_at         date,
  deprecate_at        date,                   -- Google 公告的 EOL 日期
  last_verified_at    timestamptz,            -- 最后一次健康检查通过时间
  last_verify_error   text,                   -- 上次健康检查失败时的错误信息
  notes               text,
  added_at            timestamptz default now()
);
create unique index uq_model_catalog on model_catalog(provider, model_id);
create index idx_model_catalog_modality_status on model_catalog(modality, status);
```

**初始化数据（migration 时插入，2026-05 时点）**：

```sql
insert into model_catalog (provider, model_id, display_name, modality, task_types, tier,
                           input_cost_per_1m, output_cost_per_1m, per_image_cost, context_window,
                           supports_json_mode, status, is_recommended, released_at, notes) values

-- 文本模型（推荐组合：写作 Flash + 质检 Pro 双互检）
('gemini', 'gemini-3.1-pro-preview', 'Gemini 3.1 Pro (Preview) — 推荐用于质检', 'text',
 ARRAY['writing','qa','outline','keyword_research'], 'pro',
 2.00, 12.00, NULL, 1000000, true, 'preview', true, '2026-02-19',
 '当前最强推理模型；质检 Agent 首选，挑出 Flash 写作的问题'),

('gemini', 'gemini-3-flash-preview', 'Gemini 3 Flash (Preview) — 推荐用于写作', 'text',
 ARRAY['writing','outline','keyword_research','report_summary'], 'flash',
 0.30, 2.50, NULL, 1000000, true, 'preview', true, '2025-12-01',
 '写作 Agent 首选；速度快、成本低、质量足够'),

('gemini', 'gemini-3.1-flash-lite-preview', 'Gemini 3.1 Flash Lite (Preview)', 'text',
 ARRAY['keyword_research','keyword_expansion'], 'flash-lite',
 0.10, 0.40, NULL, 1000000, true, 'preview', false, '2026-03-01',
 '最便宜，适合大批量轻任务如关键词扩展'),

-- 图像模型
('gemini', 'gemini-2.5-flash-image', 'Nano Banana — 默认配图', 'image',
 ARRAY['image_gen'], 'flash',
 NULL, NULL, 0.039, NULL, false, 'active', true, '2025-10-07',
 '~$0.039/张，快速文章配图首选'),

('gemini', 'gemini-3.1-flash-image-preview', 'Nano Banana 2 (Preview)', 'image',
 ARRAY['image_gen'], 'flash',
 NULL, NULL, 0.067, NULL, false, 'preview', false, '2026-03-01',
 '~$0.067/张，速度+质量平衡'),

('gemini', 'gemini-3-pro-image-preview', 'Nano Banana Pro (Preview)', 'image',
 ARRAY['image_gen'], 'pro',
 NULL, NULL, 0.12, NULL, false, 'preview', false, '2025-12-15',
 '~$0.12/张，含文字渲染的高质量图');
```

**模型升级 SOP**（运营者操作）：

```
当 Google 发布 Gemini 4.0 时：
1. 进 Dashboard 的 /models 页（前端 Settings 二级菜单）
2. 点 "Add Model"，填入新模型的 model_id / 价格 / 能力
3. 保存
   ↓
4. 进 /sites/<domain>/settings 站点配置
5. Writing Model 下拉选 "Gemini 4.0 Pro"
6. 保存
   ↓
7. 下次 Pipeline 跑就用新模型
8. 旧模型可在 model_catalog 中标记 is_active=false，但记录保留作为历史

零代码改动，零部署。
```

**重要约束**：
- `model_id` 必须严格匹配 Provider API 的真实 ID（不能拼写错）
- 添加新模型前应在 AI Studio 的 model 列表里确认 ID 真实存在
- 设置 `is_active=false` 不会删除该模型，仅在前端隐藏

### 2.3 RLS（行级安全）策略

```sql
-- 启用所有表
alter table sites enable row level security;
alter table keywords enable row level security;
alter table articles enable row level security;
-- ... (每张表都启用)

-- 用户只能读 owner_id = 自己 的 site 及其子数据
create policy "users read own sites"
  on sites for select
  using (owner_id = auth.uid());

create policy "users read keywords of own sites"
  on keywords for select
  using (site_id in (select id from sites where owner_id = auth.uid()));

-- ... (其他子表类似)

-- 服务端用 service_role key 绕过 RLS，不需要额外策略
```

**前端用 anon key + RLS 安全**，服务端 Pipeline 用 service_role key 全权限。

---

## 3. 内容生产 Pipeline【P0】

### 3.1 LLM Provider 抽象（关键 ⚠️）

**目的**：模型实现与 Agent 业务逻辑解耦。代码不依赖具体的 Gemini SDK，而是依赖一个抽象接口。未来切换到 Claude / GPT 时，只需新增一个 Provider 实现，所有 Agent 代码不动。

```python
from abc import ABC, abstractmethod
from typing import Optional
from pydantic import BaseModel

class LLMResponse(BaseModel):
    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    duration_ms: int
    model: str
    raw: dict = {}             # 原始 API 响应


class BaseLLMProvider(ABC):
    """所有文本 LLM Provider 的统一接口"""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        model: str,                        # 从 model_catalog 拿到的具体 model_id
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        json_mode: bool = False,           # 是否要求严格 JSON 输出
        **kwargs
    ) -> LLMResponse:
        """生成文本"""
        ...

    @abstractmethod
    def estimate_cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        """根据 model_catalog 表中的价格计算成本"""
        ...


class GeminiLLMProvider(BaseLLMProvider):
    """Google Gemini 实现"""

    def __init__(self, api_key: str, model_catalog_client):
        from google import genai
        self.client = genai.Client(api_key=api_key)
        self.catalog = model_catalog_client    # 用于查询定价

    def generate(self, prompt, model, system_prompt=None,
                 max_tokens=4096, temperature=0.7, json_mode=False, **kwargs):
        from google.genai import types
        import time

        config = types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
            system_instruction=system_prompt,
        )
        if json_mode:
            config.response_mime_type = "application/json"

        start = time.time()
        response = self.client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
        duration_ms = int((time.time() - start) * 1000)

        usage = response.usage_metadata
        cost = self.estimate_cost(model, usage.prompt_token_count, usage.candidates_token_count)

        return LLMResponse(
            text=response.text,
            tokens_in=usage.prompt_token_count,
            tokens_out=usage.candidates_token_count,
            cost_usd=cost,
            duration_ms=duration_ms,
            model=model,
            raw=response.to_dict() if hasattr(response, 'to_dict') else {}
        )

    def estimate_cost(self, model, tokens_in, tokens_out):
        info = self.catalog.get_model(provider="gemini", model_id=model)
        if not info:
            return 0.0
        return (tokens_in / 1_000_000 * info.input_cost_per_1m +
                tokens_out / 1_000_000 * info.output_cost_per_1m)


# 单例工厂
_PROVIDER_REGISTRY = {
    "gemini": GeminiLLMProvider,
    # 未来：
    # "anthropic": AnthropicLLMProvider,
    # "openai": OpenAILLMProvider,
}

def get_llm_provider(provider_name: str) -> BaseLLMProvider:
    cls = _PROVIDER_REGISTRY[provider_name]
    api_key = os.getenv(f"{provider_name.upper()}_API_KEY")
    return cls(api_key=api_key, model_catalog_client=get_catalog_client())
```

### 3.2 Agent 基类契约

所有 Agent 继承同一基类，确保统一的执行语义和日志：

```python
class BaseAgent:
    """所有 Agent 的基类。
    
    各 Agent 子类不再硬编码 model 字段，而是从 site_config 读取本任务对应的 model。
    """
    name: str
    task_type: str    # writing | qa | keyword_research | outline | image_gen | ...
    max_retries: int = 3

    def __init__(self, llm: BaseLLMProvider, site_config: dict):
        self.llm = llm
        self.site_config = site_config

    def get_model(self) -> str:
        """根据当前 task_type 从 site_config.text_provider 中找到对应模型 ID"""
        return self.site_config["text_provider"][f"{self.task_type}_model"]

    def run(self, site_id: UUID, article_id: UUID | None, input_data: dict) -> dict:
        """
        - 自动写 agent_runs 日志（含实际使用的 model）
        - 自动统计 token 用量和成本
        - 自动重试（带退避）
        - 抛出 AgentFailure 异常时，调用方决定如何处理
        """
        ...
```

### 3.2 各 Agent 接口规范

#### 3.2.1 KeywordResearchAgent（一次性，新站初始化用）

```
输入：
  - site_id
  - seed_topic: str（如 "Neverness to Everness"）
  - target_count: int = 200

动作：
  1. 调 web_search 收集相关关键词（用同义词、长尾词、问题词扩展）
  2. 调 LLM 分类意图、估算搜索量和竞争度
  3. 聚类（cluster），相似词归入同 cluster_id
  4. 批量 insert keywords 表（status=planned）

输出：
  - inserted_count: int
  - cluster_count: int
```

#### 3.2.2 KeywordExpansionAgent（每日跑）

```
输入：
  - site_id

动作：
  1. 从 GSC API 拉"过去 28 天" position 11-30 的查询词
  2. 找出未在 keywords 表中的，加入（source=gsc_expansion）
  3. 可选：抓竞品 sitemap 找内容空缺（Phase 2 再做）

输出：
  - new_keywords_count: int
```

#### 3.2.3 KeywordSelectorAgent（每日跑，在写作前）

```
输入：
  - site_id
  - count: int = 3（来自 site.config）

动作：
  1. 从 keywords 表选 status=planned，按 priority_score 排序候选
  2. 优先级公式：priority_score = log(search_volume + 1) * (1 - competition) * recency_boost
     - recency_boost: 近期才入库的词 +20%
  3. **内容多样性强制（关键）**：
     - 查询过去 7 天已发布文章的 article_type 分布
     - 统计每种 type 的 7 天累计数量
     - 在选择本次 N 个关键词时，**优先选择最近 7 天数量最少的 type**
     - 同一批次的 N 个文章，至少覆盖 2 种 article_type
  4. 关键词到 article_type 的映射规则（基于关键词意图分析）：
     - "build" / "best [character] team" → build
     - "tier list" / "best characters" → tier_list
     - "how to beat" / "[boss] guide" → boss_guide
     - "reroll" / "starter pulls" → reroll
     - "release date" / "update" / "version" → news
     - "is X on Y" / "does X have Y" → faq
     - "X vs Y" → comparison
     - 角色/武器/物品名独立词 → character_db / weapon_db
  5. 标记选中的关键词为 in_progress
  6. 创建对应 articles 记录（status=draft，article_type 已确定）

输出：
  - article_ids: list[UUID]（每个含已确定的 article_type）
```

#### 3.2.4 OutlineAgent

```
输入：
  - article_id（已含 article_type）
  - keyword: str
  - article_type: str          # 由 KeywordSelectorAgent 决定
  - related_keywords: list[str]（同 cluster 的其他词，用于内链建议）
  - site_config: dict

动作：
  1. 根据 article_type 加载对应 prompt 模板（不同类型用不同结构）
  2. 调 LLM 生成大纲

输出（JSON）：
{
  "article_type": "build",
  "title": "...",
  "meta_description": "...",
  "h1": "...",
  "sections": [
    {
      "h2": "...",
      "key_points": ["...", "..."],
      "data_required": ["weapon stats table", "..."],
      "h3_subsections": [...]
    }
  ],
  "internal_links": [
    {"anchor_text": "...", "target_keyword": "..."}
  ],
  "image_specs": [
    {"position": "after H2-1", "description": "..."}
  ],
  "estimated_word_count": 1500
}

存储：写入 articles.outline，状态切到 writing
```

**article_type 对应的结构模板（写在不同 prompt 里）**：

| article_type | 必含结构 |
|--------------|---------|
| **build** | Overview / Best Weapons / Best Artifacts / Team Comp / Rotation / FAQ |
| **tier_list** | Methodology / S Tier / A Tier / B Tier / C Tier / Recent Changes |
| **boss_guide** | Boss Stats / Attack Patterns / Step-by-Step Strategy / Recommended Team / Loot |
| **reroll** | Why Reroll / How to Reroll / Best Starters / Time Estimate / FAQ |
| **character_db** | Profile / Skills / Materials / Best Builds / Synergies |
| **weapon_db** | Stats / Effect / Best On / How to Get / Comparison |
| **news** | What Happened / Key Changes / Player Reactions / What's Next |
| **faq** | Question Restated / Direct Answer / Detailed Explanation / Related |
| **comparison** | TL;DR Verdict / Side-by-Side Table / Detailed Comparison / Recommendation |

每个模板 prompt 文件存在 `src/config/prompts/outline_<article_type>.md`。

#### 3.2.5 WritingAgent

```
输入：
  - article_id
  - outline: 上一步的输出
  - site_config: dict（语言、字数范围）

输出：
  - markdown_content: str（含 frontmatter）
  - word_count: int

强制要求（写在 prompt 里 + 后处理校验）：
  - 字数在 site_config.content_plan 范围内
  - 至少有 1 个表格或 1 个数据列表
  - 内链数量符合 outline.internal_links 长度
  - 不出现 AI 痕迹词列表（见附录 A）

存储：写入 articles.content_md，状态切到 qa_pending
```

#### 3.2.6 QAAgent ⚠️（独立调用，不能合并到 writing）

```
输入：
  - article_id
  - content: str
  - keyword: str
  - outline: 大纲

动作：
  独立调用 LLM，按以下维度评分（每项 0-2 分，总分 10）：
  1. 关键词意图匹配度
  2. 信息密度（具体数字、表格、列表）
  3. 结构合理性
  4. AI 痕迹检测（用程序化检查 + LLM 双重）
  5. 内链 / SEO 元素完整

输出：
{
  "score": 8.5,
  "passed": true,
  "feedback": {
    "intent_match": 2,
    "info_density": 1.5,
    "structure": 2,
    "ai_pattern": 1.5,
    "seo": 1.5,
    "issues": ["..."],
    "suggestions": ["..."]
  }
}

判定：
  - passed=true → articles.status = qa_passed
  - passed=false 且 attempts < 3 → 回到 writing（带 feedback）
  - passed=false 且 attempts >= 3 → articles.status = failed
```

#### 3.2.7 ImageAgent

**重要**：图像模型 **不写死**，从数据库 `sites.config.image_provider` 读取，运营者可在前端切换。

```
输入：
  - article_id
  - image_specs: list[{"position": str, "description": str, "aspect_ratio": str}]
  - site_config (含 image_provider 配置块)

site.config.image_provider 字段（数据库 sites.config.image_provider）：
{
  "provider": "gemini",                    // gemini | replicate | openai_dalle | ...
  "model": "gemini-2.5-flash-image",       // 具体模型 ID
  "default_aspect_ratio": "16:9",
  "fallback_provider": null,               // 可选，主 provider 失败时用
  "extra_params": {}                       // 各 provider 特殊参数
}

动作：
  1. 根据 image_provider.provider 路由到对应 ImageProvider 实现
  2. 调用模型生成图（按 image_specs 数量）
  3. 保存图片：
     - 先写入临时目录
     - 上传到 site repo 的 public/images/<slug>/<idx>.webp
     - 转换为 webp 格式（节省带宽）
  4. 写入 images 表（含 model 字段，便于追踪哪个模型生成的）

约束（写入 prompt）：
  - 不生成名人脸、品牌 logo、知名 IP
  - 风格：游戏概念艺术 / 数字插画
  - 默认比例：16:9 主图，方形配图按 spec 指定
```

**ImageProvider 抽象**（核心设计）：

```python
class BaseImageProvider(ABC):
    @abstractmethod
    def generate(
        self,
        prompt: str,
        aspect_ratio: str = "16:9",
        model: str = None,
        **kwargs
    ) -> bytes:
        """返回图像二进制数据（PNG/JPEG）"""
        ...

    @abstractmethod
    def estimate_cost_usd(self, num_images: int) -> float:
        ...

class GeminiImageProvider(BaseImageProvider):
    """
    支持模型：
      - gemini-2.5-flash-image (Nano Banana, 默认, ~$0.039/张)
      - gemini-3.1-flash-image-preview (Nano Banana 2, ~$0.067/张)
      - gemini-3-pro-image-preview (Nano Banana Pro, ~$0.12/张)
    """
    DEFAULT_MODEL = "gemini-2.5-flash-image"

    def __init__(self, api_key: str):
        from google import genai
        self.client = genai.Client(api_key=api_key)

    def generate(self, prompt: str, aspect_ratio: str = "16:9",
                 model: str = None, **kwargs) -> bytes:
        from google.genai import types
        target_model = model or self.DEFAULT_MODEL

        response = self.client.models.generate_content(
            model=target_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["Image"],
                image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
            ),
        )

        # 提取 inline_data
        for part in response.candidates[0].content.parts:
            if getattr(part, "inline_data", None):
                return part.inline_data.data

        raise ImageGenerationError("No image returned from Gemini")

class ReplicateImageProvider(BaseImageProvider):
    """保留作为备选 / 后期对比测试"""
    ...

# Provider 注册表
IMAGE_PROVIDERS = {
    "gemini": GeminiImageProvider,
    "replicate": ReplicateImageProvider,
    # 后续加 dalle / sdxl 等
}

def get_image_provider(provider_name: str) -> BaseImageProvider:
    cls = IMAGE_PROVIDERS.get(provider_name)
    if not cls:
        raise ValueError(f"Unknown image provider: {provider_name}")
    return cls(api_key=os.getenv(f"{provider_name.upper()}_API_KEY"))
```

**前端配置入口（dashboard）**：

虽然 MVP 前端只读，但 **图像模型设置是个例外**：在 `/sites/[domain]/settings` 页面允许修改 `sites.config.image_provider` 字段（写入数据库即可，不会触发危险操作）。这是 MVP 前端唯一的写功能。

UI 组件：
- Provider 下拉（gemini / replicate / ...）
- Model 下拉（根据选中的 provider 动态展示该 provider 支持的模型列表）
- Aspect ratio 默认值
- 单图预估成本展示（基于上面的 estimate_cost_usd）
- "测试生成"按钮（让运营者用一个示例 prompt 试一下当前配置）

#### 3.2.8 PublishAgent

```
输入：
  - article_id

动作：
  1. 从 articles 表读 content_md + 关联 images
  2. 写入 site repo 的 src/content/blog/<slug>.md
  3. git add + commit (message: "[auto] new post: <title>")
  4. git push 到主分支
  5. 等待 Cloudflare Pages deploy 完成（轮询 API）
  6. 调 GSC API 提交 URL 索引
  7. 更新 articles.status = published, published_url, published_at

失败回滚：
  - git push 失败 → 回滚 commit，articles.status = qa_passed（重发）
  - GSC 提交失败 → 不影响 published 状态，记 alert 即可
```

### 3.3 Pipeline 编排（每日 cron）

```
GitHub Actions: content-pipeline.yml
  cron: '0 2 * * *'  （UTC 02:00 = 北京时间 10:00）

  步骤：
    1. KeywordExpansionAgent.run()       # 扩充词池
    2. KeywordSelectorAgent.run(count=3) # 选今日要写的
    3. for each article_id:
         OutlineAgent → WritingAgent → QAAgent (循环最多 3 轮)
         → ImageAgent → PublishAgent
    4. 写入告警（如果有失败）
```

并发：单站日 3 篇，串行跑就行，不需要并行。

---

## 4. 数据采集层【P0】

### 4.1 各 Collector 接口规范

```python
class BaseCollector:
    source: str  # ga4 | gsc | adsense | fb_ads | cloudflare

    def fetch(self, site_id: UUID, target_date: date) -> dict:
        """拉取指定日期的数据，返回原始 payload"""
        ...

    def store_raw(self, site_id: UUID, target_date: date, payload: dict):
        """写入 metrics_raw"""
        ...

    def aggregate_to_daily(self, site_id: UUID, target_date: date):
        """从 raw 聚合写入 metrics_daily（仅本 source 涉及的字段）"""
        ...
```

### 4.2 各数据源关键字段映射

#### GA4
- API: `runReport` on Data API
- 维度: `date`
- 指标: `sessions`, `screenPageViews`, `averageSessionDuration`, `bounceRate`
- 写入 `metrics_daily`：`sessions`, `pageviews`, `avg_duration_sec`, `bounce_rate`

#### GSC
- API: `searchanalytics.query`
- 维度: `date`, `query`（前 100 个查询词）
- 写入：`gsc_clicks`, `gsc_impressions`, `gsc_avg_position`
- top_queries 单独存到 metrics_raw 的 payload

#### AdSense
- API: AdSense Management API v2 `accounts.reports.generate`
- 指标: `ESTIMATED_EARNINGS`, `PAGE_VIEWS`, `IMPRESSIONS`, `CLICKS`, `PAGE_VIEWS_RPM`
- 写入：`adsense_revenue_usd`, `adsense_pageviews`, ..., `page_rpm_usd`

#### FB Ads
- API: Facebook Marketing API `act_<id>/insights`
- 拉取频率：**每 6 小时**（其他每日 1 次）
- 指标: `spend`, `clicks`, `impressions`, `cpc`, `ctr`, `frequency`
- 同时拉 ad_campaigns 状态写入 ad_campaigns 表

#### Cloudflare
- API: Cloudflare Analytics API
- 用于交叉校验 GA4（验证流量真实性）

### 4.3 数据采集调度

```
GitHub Actions: data-collection.yml
  cron: '0 1 * * *'  （UTC 01:00 = 北京时间 09:00，先于内容 Pipeline）

  采集昨日数据（target_date = today - 1）

GitHub Actions: fb-ads-fast.yml
  cron: '0 */6 * * *'  （每 6 小时）

  仅采集 FB Ads 当日数据（target_date = today）
```

---

## 5. 决策硬规则引擎【P0】

### 5.1 输入

每次跑读取最近 N 天的 metrics_daily。

### 5.2 规则定义（伪代码，需可配置）

```yaml
# rules.yaml
rules:
  - id: ad_group_no_conversion
    scope: ad_group
    condition: "spend_today > 20 and conversions == 0"
    action: SUGGEST_PAUSE

  - id: ad_group_roi_negative
    scope: ad_group
    condition: "rolling_3day_roi < -0.7"
    action: SUGGEST_PAUSE

  - id: ad_group_roi_positive
    scope: ad_group
    condition: "rolling_2day_roi > 0"
    action: SUGGEST_INCREASE_BUDGET_50PCT

  - id: ad_frequency_high
    scope: ad_group
    condition: "frequency > 4"
    action: SUGGEST_REPLACE_CREATIVES

  - id: site_direction_failed
    scope: site
    condition: "rolling_7day_roi < -0.6"
    action: SUGGEST_REVIEW_DIRECTION

  - id: site_scale_up
    scope: site
    condition: "rolling_14day_roi > 0"
    action: SUGGEST_SCALE_UP
```

### 5.3 输出

不直接执行，只把建议写入 `daily_reports.data_snapshot.suggestions`，由日报展示。

---

## 6. 日报生成器【P0】

### 6.1 触发与组装

```
GitHub Actions: daily-report.yml
  cron: '0 1 * * *'  （UTC 01:00，紧跟数据采集后）

  步骤：
    1. 读最近 1/3/7/14 天的 metrics_daily
    2. 算所有衍生指标（趋势、对比）
    3. 跑决策规则引擎
    4. 调 LLM 生成"AI 解读"段落（数据用代码渲染，不让 AI 编数字）
    5. 组装成 Markdown
    6. **写入 daily_reports 表**（前端 Dashboard 即时可见）
    7. **如果有 🔴 紧急告警**，调用 Email Notifier 发送
```

### 6.2 Email Notifier 实现

```python
# src/notifiers/email.py
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os

class EmailNotifier:
    def __init__(self):
        self.host = os.getenv("SMTP_HOST")
        self.port = int(os.getenv("SMTP_PORT", "587"))
        self.user = os.getenv("SMTP_USER")
        self.password = os.getenv("SMTP_PASS")
        self.recipient = os.getenv("ALERT_RECIPIENT_EMAIL")

    def send_alert(self, severity: str, category: str,
                   title: str, message: str, dashboard_url: str = None):
        """
        severity: critical | warning | info
        仅 critical 级别会真的发邮件，其他只写入 alerts 表
        """
        if severity != "critical":
            return  # 非紧急，不发邮件，避免麻木

        subject = f"[Traffic Ops Alert] {severity.upper()} - {category}"
        body = f"""
{title}

{message}

{f'View in Dashboard: {dashboard_url}' if dashboard_url else ''}

--
Traffic Ops Auto Alert System
"""
        msg = MIMEMultipart()
        msg["From"] = self.user
        msg["To"] = self.recipient
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(self.host, self.port) as server:
            server.starttls()
            server.login(self.user, self.password)
            server.send_message(msg)
```

### 6.3 通道选型说明

- **MVP 阶段**：用 Gmail App Password 即可（免费，需开启 2FA 后生成）
- **正式运营**：迁移到 SendGrid Free Tier（100 封/天免费），更稳定
- **不发普通日报到邮箱**：日报每天 1 封 × N 站会让你脱敏；只发紧急告警

---

## 7. 告警监控【P0】

### 7.1 触发与频率

```
GitHub Actions: alert-monitor.yml
  cron: '0 * * * *'  （每小时整点）
```

### 7.2 检查项（与 PRD §5.4 对齐）

| 检查 | SQL/逻辑 |
|------|---------|
| AdSense 无效流量 > 10% | `metrics_daily.invalid_traffic_pct > 0.10` |
| AdSense CTR > 5% | `adsense_ctr > 0.05` |
| FB 单日花费 > 阈值 150% | 与 site.config 中预算阈值对比 |
| 站点 5xx | 拉 Cloudflare Analytics |
| AI 调用连续失败 ≥ 5 | 查最近 agent_runs |
| LLM 调用异常（>1000/天 或单篇 >50K token） | 同上 |
| 内容连续 3 篇质检不过 | 查 articles 状态 |
| 连续 5 篇首轮质检不过 | 查 agent_runs.qa 历史 |
| 7 天首轮质检通过率 < 40% | 聚合查询 |

### 7.3 去重

同一 (site_id, category) 的告警 1 小时内只发一次，由 `alerts.created_at` 判断。

---

## 8. 前端 Dashboard【P0】

### 8.1 技术栈细节

```json
{
  "next": "^15.0.0",
  "react": "^19.0.0",
  "@supabase/supabase-js": "^2.x",
  "@supabase/ssr": "^0.x",
  "tailwindcss": "^4.x",
  "shadcn/ui": "latest",
  "recharts": "^2.x",
  "date-fns": "^3.x",
  "zod": "^3.x"
}
```

### 8.2 路由与功能清单

| 路由 | 功能 | 数据源 |
|------|------|--------|
| `/login` | Magic Link 登录 | Supabase Auth |
| `/` | **Mission Control（项目级总览）** | sites + 项目里程碑 + 健康度计算 |
| `/sites/[domain]` | 单站总览 | metrics_daily 最近 14 天 + alerts 未确认 |
| `/sites/[domain]/keywords` | 关键词池表格 | keywords，支持按 status / cluster / search_volume 筛选 |
| `/sites/[domain]/articles` | 文章列表 | articles，含状态/qa_score/published_url/article_type |
| `/sites/[domain]/articles/[id]` | 文章详情 | 单篇 articles + 关联 images + agent_runs |
| `/sites/[domain]/agent-runs` | Agent 执行日志（最近 3 天） | agent_runs |
| `/sites/[domain]/metrics` | 指标趋势图 | metrics_daily（折线图、堆叠图）|
| `/sites/[domain]/alerts` | 告警历史 | alerts |
| `/sites/[domain]/settings` | **站点设置（含图像模型切换）** | sites.config（**唯一允许写入**）|
| `/reports` | 日报归档 | daily_reports |

### 8.3 关键交互约束

- 数据展示**默认只读**，无任何资金类、内容类的写按钮
- **唯一允许的写操作**：`/sites/[domain]/settings` 页修改 `sites.config`（特别是 image_provider 配置）
  - 这是因为图像模型选择是运营者的核心创作决策权
  - 修改后立即生效，下一次 ImageAgent 执行时使用新配置
- "确认告警"（acknowledged=true）也是写操作但低风险，可做可不做
- 数据自动刷新：进入页面拉一次 + 用户手动点刷新（不做实时订阅，避免复杂）

### 8.4 类型生成

```bash
npx supabase gen types typescript --project-id <id> --schema public > lib/supabase/types.ts
```

写入 CI，schema 改动时自动重新生成。

### 8.5 Settings 页详细规格（前端唯一可写页）

**路由**：`/sites/[domain]/settings`

**功能区分块**：

#### 块 1：文本模型配置（核心 — 4 层模型管理的"第 3 层"）

UI 草图：
```
+--------------------------------------------------+
| 文本模型配置                                      |
+--------------------------------------------------+
| Writing Agent                                    |
|   [Gemini 3 Flash (Preview) ▼]                   |
|   $0.30 / $2.50 per 1M tokens                    |
|                                                  |
| QA Agent ⚠️ (建议选不同模型)                     |
|   [Gemini 3.1 Pro (Preview) ▼]                   |
|   $2.00 / $12.00 per 1M tokens                   |
|                                                  |
| Outline Agent                                    |
|   [Gemini 3 Flash (Preview) ▼]                   |
|                                                  |
| Keyword Research                                 |
|   [Gemini 3.1 Flash Lite ▼]                      |
|   $0.10 / $0.40 per 1M tokens                    |
|                                                  |
| Report Summary                                   |
|   [Gemini 3 Flash (Preview) ▼]                   |
|                                                  |
| 月度预估（按 daily_articles=3）: ~$8             |
| [保存]   [测试调用]                              |
+--------------------------------------------------+
```

**下拉选项**：从 `model_catalog` 表读取（**不硬编码**）：

```typescript
// 前端伪代码
async function loadTextModels() {
  const { data } = await supabase
    .from('model_catalog')
    .select('*')
    .eq('modality', 'text')
    .in('status', ['active', 'preview'])
    .contains('task_types', [currentRole]);  // 按 task_types 过滤
  return data;
}
```

**保存逻辑**：直接 update `sites.config.text_provider.{role}_model` 字段，下次 Pipeline 跑就用新模型。

**校验规则**：
- `writing_model` 和 `qa_model` 不能相同（前端阻止保存，提示双模型互检原则）
- 未知 `model_id` 拒绝保存

#### 块 2：图像模型配置

```
+------------------------------------------+
| 图像生成配置                             |
+------------------------------------------+
| Provider:    [Gemini ▼]                  |
| Model:       [Nano Banana ▼]             |
| Aspect:      [16:9 ▼]                    |
|                                          |
| 单图预估成本: $0.039                     |
| 月度预估（按 90 张/月）: ~$3.50          |
|                                          |
| [保存]   [测试生成]                      |
+------------------------------------------+
```

下拉选项同样**从 model_catalog 读**（filter modality='image'）。

**保存逻辑**：update `sites.config.image_provider`。

**测试生成按钮**：
- 走 Next.js API route（BFF），不能从前端直接调 Gemini API（API Key 在服务端）
- 使用固定示例 prompt（如 `"A fantasy sword in dark misty forest, concept art"`）
- 显示：生成图 + 实际耗时 + 实际花费

#### 块 3：模型管理（一个独立小窗口/弹窗）

让运营者**新增/标记弃用模型**，对应"4 层模型管理的第 1 层（catalog）"：

```
+------------------------------------------+
| 模型目录管理                             |
+------------------------------------------+
| [+ 添加新模型]                           |
|                                          |
| Provider | Model ID                | 状态 |
|----------|-------------------------|------|
| gemini   | gemini-3.1-pro-preview | preview |
| gemini   | gemini-3-flash-preview | preview |
| gemini   | gemini-2.5-flash-image | active  |
| ...                                      |
|                                          |
| 上次健康检查：2026-05-09 03:00 UTC ✅   |
+------------------------------------------+
```

**新增模型表单**：填 model_id / display_name / modality / task_types / 价格 → INSERT 到 model_catalog → 立即出现在块 1/2 下拉中。

**MVP 实施优先级**：块 1 + 块 2 必做；块 3 可后置（Phase 2 加，不阻塞 MVP）。

#### 块 4：内容计划（可选）

- `daily_articles`: 1-10
- `min_word_count`: 800-3000
- `max_word_count`: 1500-5000

#### 块 5：质检阈值（可选）

- `min_quality_score`: 6-9
- `max_retry_rounds`: 1-5

**MVP 实施优先级总结**：块 1+2 必做，块 3 Phase 2 加，块 4/5 看时间。

### 8.6 Mission Control 页详细规格（前端 `/`）

**目的**：运营者每天打开的第一页，回答"项目跑得怎么样、下一步该做什么"。

#### 区块 1：阶段进度条
- 当前 Phase（自动判断：Phase 1.A / 1.B / 1.C / Phase 2 等）
- 当前 Phase 第几天 / 总天数
- 视觉：横向进度条 + 文字"Phase 1.C 第 5 天 / 14 天"

#### 区块 2：里程碑列表
读取 `sites.config.milestones` 配置，配合实际数据自动判断完成状态：

```json
[
  {"id": "site_live", "label": "首站上线", "auto_check": "site has homepage"},
  {"id": "30_articles", "label": "30 篇内容", "auto_check": "articles.count(published) >= 30"},
  {"id": "adsense_submitted", "label": "AdSense 提交", "auto_check": "config.adsense.submitted_at not null"},
  {"id": "adsense_approved", "label": "AdSense 过审", "auto_check": "config.adsense.approved == true"},
  {"id": "first_seo_click", "label": "首次自然流量", "auto_check": "metrics_daily.gsc_clicks > 0 in last 7d"},
  {"id": "fb_ads_started", "label": "FB Ads 启动", "auto_check": "ad_campaigns.count(active) > 0"}
]
```

#### 区块 3：核心指标快照（昨日 + 趋势）
- 14 天累计 ROI（含目标比对）
- SEO 自然流量周环比（这是 FB Ads 真正 KPI）
- FB Ads 直接 ROI（标注"参考用，预期为负"）
- 项目健康度：🟢 / 🟡 / 🔴

#### 区块 4：下一关键决策点
基于 Phase 配置 + 当前进度，提示"再 X 天到决策点，届时按 ROI/SEO 数据决定 Scale Up / Pivot"。

#### 区块 5：今日 AI 解读 + 待办
- 取自当日 `daily_reports` 的 ai_summary 字段
- 待办项（来自决策规则引擎建议）

#### 区块 6：跳转入口
- 完整日报、告警、指标趋势、文章列表的快捷入口

#### 健康度计算规则

```python
def compute_health_status(site_id: UUID) -> str:
    metrics = get_recent_metrics(site_id, days=7)

    # 红：紧急问题
    if has_critical_alerts_unack(site_id):
        return "red"
    if metrics.adsense_invalid_traffic_pct > 0.10:
        return "red"
    if metrics.rolling_14d_total_roi < -0.6:
        return "red"

    # 黄：需关注
    if metrics.qa_failure_rate_7d > 0.6:
        return "yellow"
    if metrics.rolling_14d_total_roi < -0.3:
        return "yellow"
    if metrics.gsc_clicks_growth_wow < 0.2 and current_phase == "1.C":
        return "yellow"

    return "green"
```

---

## 9. 配置文件规范

### 9.1 site.config.yaml（每站一份，存于站点 repo）

```yaml
# 基础信息
domain: ntecodex.com
site_name: "NTE Codex"
target_lang: en
content_strategy: gacha_guide
game_target: "Neverness to Everness"

# 站点配置（仅前端展示需要的部分；Pipeline 用的是数据库 sites.config）
display:
  primary_color: "#1a1a2e"
  hero_title: "Neverness to Everness — Complete Guide"

# 内容策略
content_plan:
  daily_articles: 3
  min_word_count: 1200
  max_word_count: 2500
  internal_links_per_article: 3
  images_per_article: 3
  # 内容多样性强制：7 天滑动窗口，单种 type 占比上限
  diversity:
    max_type_share_7d: 0.40        # 任何单一 type 7 天占比不得超过 40%
    min_types_per_batch: 2         # 单批次至少 2 种 type

# 文章类型库（按需开启/关闭）
article_types:
  - build
  - tier_list
  - boss_guide
  - reroll
  - character_db
  - weapon_db
  - news
  - faq
  - comparison

# 质检阈值
qa_thresholds:
  min_quality_score: 7
  max_ai_pattern_count: 5
  max_retry_rounds: 3
  # 连续失败的"方向选错"判定
  consecutive_failure_alert: 5
  weekly_pass_rate_min: 0.40

# 投放预算（Phase 1.C 阶段使用）
ad_budget:
  daily_max_usd: 20
  total_test_usd: 200
  loss_stop_threshold_usd: 200

# 项目里程碑（Mission Control 用）
milestones:
  - id: site_live
    label: "首站上线"
  - id: 30_articles
    label: "30 篇内容达成"
  - id: adsense_submitted
    label: "AdSense 已提交"
  - id: adsense_approved
    label: "AdSense 过审"
  - id: first_seo_click
    label: "首次自然流量"
  - id: fb_ads_started
    label: "FB Ads 启动"

# 当前 Phase（系统自动推进，也可人工覆盖）
current_phase: "1.A"
```

### 9.2 .env.example（traffic-ops-core）

> 详细说明见 **CREDENTIALS-SETUP.md**。本节给出完整变量列表。

```
# === 全局共享 (所有站共用) ===

# Supabase
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=

# LLM + 图像统一 API（写作 / 质检 / 配图都用 Gemini）
GEMINI_API_KEY=

# Email 告警 (SMTP)
SMTP_HOST=                     # smtp.gmail.com 或 smtp.sendgrid.net
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
ALERT_RECIPIENT_EMAIL=

# Cloudflare
CLOUDFLARE_API_TOKEN=
CLOUDFLARE_ACCOUNT_ID=

# Git（PublishAgent 推送 site repo）
GIT_USER_NAME=traffic-ops-bot
GIT_USER_EMAIL=bot@example.com
GITHUB_TOKEN=


# === 每站专属 (按 site_slug 大写加前缀) ===
# 命名规则：<SITE_SLUG_UPPER>_<RESOURCE>

# 首站 NTE 示例（site_slug=ntecodex）：
NTECODEX_FB_AD_ACCOUNT_ID=
NTECODEX_FB_PIXEL_ID=
NTECODEX_FB_ACCESS_TOKEN=
NTECODEX_GA4_PROPERTY_ID=
NTECODEX_ADSENSE_PUBLISHER_ID=        # 过审后填，前期留空
NTECODEX_GOOGLE_SERVICE_ACCOUNT_JSON= # GA4 / GSC / AdSense 共用一份 JSON

# 加新站时：
# GENSHIN2_FB_AD_ACCOUNT_ID=
# GENSHIN2_FB_PIXEL_ID=
# ... 以此类推
```

### 9.3 多站凭证读取代码模式

```python
# src/db/credentials.py
import os
import json
from typing import Optional

def get_site_credentials(site_slug: str) -> dict:
    """根据 site_slug 拼接环境变量名读取该站专属凭证。
    
    优势：
    - 代码通用，加新站不用改代码
    - 凭证按命名空间隔离，单个 secret 泄漏不影响其他站
    - GitHub Secrets 自然按前缀分组，方便审计
    """
    prefix = site_slug.upper().replace("-", "_")
    return {
        "fb_ad_account_id": os.getenv(f"{prefix}_FB_AD_ACCOUNT_ID"),
        "fb_pixel_id": os.getenv(f"{prefix}_FB_PIXEL_ID"),
        "fb_access_token": os.getenv(f"{prefix}_FB_ACCESS_TOKEN"),
        "ga4_property_id": os.getenv(f"{prefix}_GA4_PROPERTY_ID"),
        "adsense_publisher_id": os.getenv(f"{prefix}_ADSENSE_PUBLISHER_ID"),
        "google_service_account_json": _parse_json_env(
            f"{prefix}_GOOGLE_SERVICE_ACCOUNT_JSON"
        ),
    }

def _parse_json_env(var_name: str) -> Optional[dict]:
    raw = os.getenv(var_name)
    return json.loads(raw) if raw else None


# 使用示例
def fetch_ga4_data(site_slug: str, target_date):
    creds = get_site_credentials(site_slug)
    if not creds["ga4_property_id"]:
        raise ValueError(f"GA4 not configured for site {site_slug}")
    # 调 GA4 API ...
```

代码先从数据库 sites 表读 `config.site_slug`，再用 site_slug 拼接环境变量名取凭证。**数据库永远不存凭证。**

---

## 10. GitHub Actions 工作流【P0】

### 10.1 content-pipeline.yml

```yaml
name: Content Pipeline
on:
  schedule:
    - cron: '0 2 * * *'  # UTC 02:00 = 北京时间 10:00
  workflow_dispatch:      # 手动触发用

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -e .
      - run: python -m src.pipelines.content
        env:
          # 全局共享
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_PORT: ${{ secrets.SMTP_PORT }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASS: ${{ secrets.SMTP_PASS }}
          ALERT_RECIPIENT_EMAIL: ${{ secrets.ALERT_RECIPIENT_EMAIL }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          # 站点专属（注意：这里要列出所有运行站的 slug 前缀变量）
          NTECODEX_GA4_PROPERTY_ID: ${{ secrets.NTECODEX_GA4_PROPERTY_ID }}
          NTECODEX_FB_AD_ACCOUNT_ID: ${{ secrets.NTECODEX_FB_AD_ACCOUNT_ID }}
          NTECODEX_FB_PIXEL_ID: ${{ secrets.NTECODEX_FB_PIXEL_ID }}
          NTECODEX_FB_ACCESS_TOKEN: ${{ secrets.NTECODEX_FB_ACCESS_TOKEN }}
          NTECODEX_ADSENSE_PUBLISHER_ID: ${{ secrets.NTECODEX_ADSENSE_PUBLISHER_ID }}
          NTECODEX_GOOGLE_SERVICE_ACCOUNT_JSON: ${{ secrets.NTECODEX_GOOGLE_SERVICE_ACCOUNT_JSON }}
          # 加新站时这里追加一组
```

### 10.2 其余 workflow

类似结构，cron 时间见前述章节。

### 10.3 model_health_check.yml（每周一次）

**目的**：检测 model_catalog 中的模型是否还能调用，捕捉 Google 弃用通知。

```yaml
name: Model Health Check
on:
  schedule:
    - cron: '0 3 * * 1'  # 每周一 UTC 03:00
  workflow_dispatch:

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -e .
      - run: python -m src.maintenance.model_health_check
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_PORT: ${{ secrets.SMTP_PORT }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASS: ${{ secrets.SMTP_PASS }}
          ALERT_RECIPIENT_EMAIL: ${{ secrets.ALERT_RECIPIENT_EMAIL }}
```

**实现**（伪代码）：

```python
# src/maintenance/model_health_check.py
def check_all_models():
    models = db.query("select * from model_catalog where status in ('active', 'preview')")
    
    for m in models:
        try:
            # 最小测试调用
            if m.modality == "text":
                provider = get_llm_provider(m.provider)
                provider.generate(prompt="ping", model=m.model_id, max_tokens=10)
            elif m.modality == "image":
                provider = get_image_provider(m.provider)
                provider.generate(prompt="a red dot", model=m.model_id)
            
            db.update("model_catalog", m.id, {
                "last_verified_at": now(),
                "last_verify_error": None
            })
        except ModelNotFoundError as e:
            db.update("model_catalog", m.id, {
                "status": "deprecated",
                "last_verify_error": str(e)
            })
            send_email_alert(
                severity="critical",
                title=f"Model deprecated: {m.model_id}",
                message=f"Provider {m.provider} returned 'model not found'.\n"
                        f"Sites using this model will fail at next run.\n"
                        f"Action: open Dashboard /models, switch sites to a working model."
            )
        except Exception as e:
            # 临时性错误（quota 满了等），不标记 deprecated 但记 error
            db.update("model_catalog", m.id, {"last_verify_error": str(e)})
```

### 10.4 失败重试

- workflow 级别用 `continue-on-error: false`
- Agent 内部用 tenacity 库重试（指数退避）
- 全部失败时通过 Email 告警（如果级别为 critical）

---

## 11. 测试要求【P1】

### 11.1 单元测试

每个 Agent 必须有：
- 正常输入 → 预期输出
- 失败重试逻辑
- LLM 调用 mock（不真实调用）

### 11.2 集成测试

- 完整跑通 Outline → Writing → QA → Publish 一篇文章（用 mock LLM）
- 数据采集 → metrics_daily 聚合验证

### 11.3 手动验收

每个 Agent 至少手动跑 3 次真实 LLM 调用，人工 review 输出。

---

## 12. 部署清单

### 12.1 Supabase 部署
- [ ] 创建项目（区域：Southeast Asia）
- [ ] 跑 db/migrations/ 下所有 SQL
- [ ] 配置 RLS 策略
- [ ] 创建第一个用户（运营者）

### 12.2 后端部署
- [ ] traffic-ops-core repo 创建
- [ ] 所有 GitHub Secrets 配置
- [ ] 推第一个 commit，验证 Actions 能跑
- [ ] 手动触发 workflow_dispatch 验证

### 12.3 前端部署
- [ ] traffic-ops-dashboard repo 创建
- [ ] 创建 Cloudflare Pages 项目
- [ ] 环境变量：`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- [ ] 绑定子域名
- [ ] 在 Supabase Auth 加入回调 URL

### 12.4 站点部署
- [ ] ntecodex-site repo 创建
- [ ] Cloudflare Pages 创建项目
- [ ] 绑定主域名
- [ ] 配置 ads.txt、robots.txt、sitemap.xml

---

## 13. 验收标准（与 PRD §10 对齐 + 工程细化）

### Schema 验收
- [ ] 全部 10+ 张表创建成功
- [ ] RLS 策略测试：anon key 无法访问其他用户数据
- [ ] 至少跑通 1 次 service_role key 的全表写入

### Pipeline 验收
- [ ] 单元测试覆盖率 ≥ 60%
- [ ] 端到端：从 keyword 到 published 单篇文章成功
- [ ] 失败重试：人工注入故障，验证恢复
- [ ] Agent 日志正常写入，3 天清理任务正常

### 前端验收
- [ ] 登录流程通畅
- [ ] 8 个核心页面全部可访问
- [ ] 图表正常渲染
- [ ] Lighthouse Performance ≥ 80

### 集成验收
- [ ] 连续 72 小时无人值守跑 Pipeline，至少产出 6 篇合格文章
- [ ] 日报每天准时写入 Dashboard（连续 7 天无遗漏）
- [ ] Email 紧急告警通道至少触发并送达过 1 次
- [ ] 至少触发过 1 次真实告警

---

## 附录 A：AI 痕迹词检测列表（写作 Agent 后置过滤）

```
furthermore, moreover, in conclusion, additionally
delve into, navigate the landscape, in today's fast-paced world
it's worth noting, it's important to note
let's dive into, embark on a journey
unleash, harness the power of
seamless, robust, comprehensive (when used as filler)
```

匹配到 5 个以上 → QA 自动标记 issue。

## 附录 B：状态机转换矩阵

```
articles.status:
  draft → writing                  (OutlineAgent 完成)
  writing → qa_pending             (WritingAgent 完成)
  qa_pending → qa_passed           (QA 通过)
  qa_pending → qa_failed           (QA 不过，attempts < 3)
  qa_failed → writing              (重试)
  qa_passed → published            (PublishAgent 完成)
  qa_passed → failed               (PublishAgent 失败)
  qa_pending → failed              (attempts >= 3)
  published → archived             (人工归档)
```

应用层用 enum + 状态转换函数集中管理，禁止直接 update status。

---

**文档结束**

下一步：人员操作指引（HUMAN-RUNBOOK.md）描述运营者一次性需要做的全部人工操作。
