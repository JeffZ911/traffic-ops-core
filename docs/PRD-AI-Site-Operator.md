# AI 自动化流量站运营系统 — 需求文档（PRD）

> 文档版本：v0.9（LLM 改 Gemini + 4 层模型管理 + 凭证多站隔离）
> 最后更新：2026-05
> 文档目的：用于自我对齐 + AI 协作 + 后续迭代基线
>
> **变更记录**：
> - v0.9 (2026-05)：
>   - **移除 Anthropic 依赖，文本生成全面切换 Gemini**（写作用 Flash、质检用 Pro，双模型互检）
>   - **引入 4 层模型管理**：model_catalog 表 + 角色映射 + 前端切换 + 健康检查 cron
>   - **凭证管理标准化**：全站共用 vs 站点专属（site_slug 前缀命名规范）
>   - 新增配套文档 CREDENTIALS-SETUP.md
> - v0.8 (2026-05)：换游戏 NTE / 取消钉钉 / Roadmap 拆分 / Mission Control
> - v0.7 (2026-05)：图像模型改 Gemini Nano Banana
> - v0.6 (2026-05)：锁定 Schema 5 项决策；拆分配套文档
> - v0.5 (2026-05)：Supabase 替代 CSV；前端提前到 Phase 1 并行
> - v0.4 (2026-05)：移除 LLM 成本限制
> - v0.3 (2026-05)：确认首站为 PBZ（v0.8 已废弃）
> - v0.2 (2026-05)：钉钉机器人通知（v0.8 已废弃）
> - v0.1 (2026-05)：初稿

---

## 0. 阅读说明

- 本文档已确认：**首期 1 个站跑通后再扩展**、**AI 全自动 + 质检 Agent**、**Python 技术栈**、**Email 紧急告警 + 前端 Dashboard 看日报**、**Supabase（美国区）作为统一数据存储**、**只读前端 Phase 1 并行开发**、**首站为 Neverness to Everness (NTE) Gacha 攻略**。
- 标注 ⚠️ 的章节是关键风险点，标注 🔵 的是可后置的 Nice-to-have。
- 所有 "AI 完成" 的环节，均指 "代码 + LLM API（默认 Claude）" 协同完成。
- 非编程的一次性人工操作清单见 §11。

---

## 1. 项目背景与目标

### 1.1 业务背景

通过 AI 批量建设和运营内容站点，以**付费广告（Facebook Ads 等）**导流验证商业模型，再用 SEO 长尾流量降低边际获客成本，**最终通过 Google AdSense 等广告联盟变现**。

本质是一个**流量套利 + AI 内容规模化**的混合模型。

### 1.2 项目目标（分阶段）

| 阶段 | 目标 | 验证标准 |
|------|------|---------|
| **MVP（4-6 周）** | 1 个站完整跑通 AI 自动化流程 | 7 天累计 ROI > -50%；日均自动产出 ≥ 3 篇内容；零人工干预运行 ≥ 72h |
| **验证（6-10 周）** | 验证商业模型 | 14 天累计 ROI > -20%；自然流量占比 > 10% |
| **复制（10-16 周）** | 复制到第 2、3 个站 | 新站启动到首批内容上线 ≤ 5 天；2 个站合计 ROI > 0 |
| **规模化（4 个月+）** | 5+ 站矩阵管理 | 单站平均维护成本 < 2 小时/周 |

### 1.3 非目标（Out of Scope）

- ❌ 不做收费产品 / 会员体系 / 电商
- ❌ 不做需要人工运营社区（论坛、UGC）的站点
- ❌ 不涉及 AdSense 政策禁止的内容方向（成人、赌博、毒品等）
- ❌ 不做需要实时数据推送的站点（如比分、股价）

---

## 2. 用户与角色

| 角色 | 描述 | 核心诉求 |
|------|------|---------|
| **运营者（你）** | 项目唯一决策者 | 看日报、做决策（放大/暂停/Pivot）、提供广告素材 |
| **AI Agents** | 系统内的多个 LLM 驱动模块 | 各司其职，按 SOP 完成内容生产、数据采集、报告生成 |
| **最终读者** | 站点访客（来自 FB 广告或自然搜索） | 获取攻略 / 解决方案 / 情绪价值 |

---

## 3. 系统总体架构

### 3.1 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                运营者操作界面                                 │
│  - Web 前端（Next.js）：Mission Control / 关键词 / 文章 /     │
│    指标 / 日报 / 告警，所有数据集中在此                       │
│  - Email：仅紧急告警推送（不看 Dashboard 也能知道）           │
└─────────────────────────────────────────────────────────────┘
                            ↑
┌─────────────────────────────────────────────────────────────┐
│            统一数据层（Supabase / PostgreSQL，美国区）        │
│  sites / keywords / articles / agent_runs / metrics / ...   │
│  REST API（自带）+ 实时订阅 + RLS 行级安全                    │
└─────────────────────────────────────────────────────────────┘
                            ↑
┌─────────────────────────────────────────────────────────────┐
│                   调度层（GitHub Actions Cron）              │
│   每日定时触发：内容生产 / 数据采集 / 日报生成 / 告警检查    │
└─────────────────────────────────────────────────────────────┘
            ↓                    ↓                    ↓
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  内容生产 Pipeline │  │   数据采集层      │  │   日报 + 告警    │
│ - 关键词调研 Agent │  │ - GA4 API        │  │ - 数据聚合       │
│ - 关键词扩展 Agent │  │ - GSC API        │  │ - 异常检测       │
│ - 大纲 Agent      │  │ - AdSense API    │  │ - AI 解读        │
│ - 写作 Agent      │  │ - FB Ads API     │  │ - 写入 Dashboard │
│ - 质检 Agent ⚠️   │  │ - Cloudflare     │  │ - Email 紧急推送 │
│ - 配图 Agent      │  │   Analytics      │  │                  │
│ - 发布 Agent      │  │                  │  │                  │
└──────────────────┘  └──────────────────┘  └──────────────────┘
            ↓
┌─────────────────────────────────────────────────────────────┐
│            站点层（Astro + Cloudflare Pages）                │
│  Git 仓库 → Cloudflare Pages 自动部署                        │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 技术栈

| 层 | 技术 | 备注 |
|----|------|------|
| 站点框架 | **Astro** | 静态生成，SEO 友好 |
| 部署 | **Cloudflare Pages** | 免费、无限带宽 |
| 域名 | GoDaddy（已有）→ DNS 托管到 Cloudflare | 一次性人工 |
| 仓库 | **GitHub**（每站独立 repo） | Cloudflare Pages 直接对接 |
| 后端语言 | **Python 3.11+** | AI 生态成熟 |
| LLM | **Gemini API**（写作 Flash + 质检 Pro 互检）| 模型 ID 不写死，存于 model_catalog 表，前端可切换；详见 §5.8 |
| AI 配图 | **Gemini Image API（Nano Banana 系列）** | 同一个 Gemini API Key，模型可前端切换 |
| 调度 | **GitHub Actions Cron** | 免费、零运维 |
| **数据存储** | **Supabase（美国区）** | 统一数据层；与 GitHub Actions 同区，访问最快 |
| **管理前端** | **Next.js + Supabase Client** | Mission Control + 数据看板，部署 Cloudflare Pages |
| 紧急告警 | **Email（SMTP / SendGrid）** | 仅用于紧急推送；日常数据走前端 |
| 配置管理 | **YAML + .env** | 站点元配置；运行态状态全部入库 |

### 3.3 多站隔离原则

每个域名 = **独立的 GitHub repo + 独立的 config + 独立的 API 凭据**。
共享的是 **代码模板、Agent Pipeline、数据看板逻辑**。

新增一个站 = 复制 repo 模板 + 改 config + 跑 onboarding checklist。

---

## 4. 域名配置规范（Domain Config）

### 4.1 配置文件示例

每个站的根目录有一份 `site.config.yaml`：

```yaml
# 基础信息
domain: gameguide-x.com
site_name: "Game Guide X"
target_lang: en           # en | id | vi | th | zh
content_strategy: game_guide   # game_guide | indo_lifestyle | tools | ...

# 数据接入
ga4:
  property_id: "123456789"
  measurement_id: "G-XXXXXXXXX"

gsc:
  site_url: "https://gameguide-x.com"

adsense:
  publisher_id: "ca-pub-XXXXXXXXXXXXXXXX"
  approved: false   # 上线初期 false，过审后改 true

facebook_ads:
  ad_account_id: "act_XXXXXXXXX"
  pixel_id: "XXXXXXXXXXXXXXX"

cloudflare:
  zone_id: "xxxxx"
  pages_project: "gameguide-x"

# AI 配置（仅做引用，权威值在数据库 sites.config，可前端 Settings 页修改）
text_provider:
  provider: gemini
  writing_model: gemini-3-flash
  qa_model: gemini-3.1-pro-preview
  keyword_research_model: gemini-3.1-flash-lite-preview
  outline_model: gemini-3-flash

image_provider:
  provider: gemini
  model: gemini-2.5-flash-image
  default_aspect_ratio: "16:9"

# 内容策略
content_plan:
  daily_articles: 3
  min_word_count: 1200
  max_word_count: 2500
  internal_links_per_article: 3
  images_per_article: 3

# 质检阈值
qa_thresholds:
  min_quality_score: 7      # 0-10
  max_ai_pattern_count: 5   # AI 痕迹词上限
  max_retry_rounds: 3
```

### 4.2 凭据管理

- API Key、Token 等敏感信息**只放 GitHub Secrets** 和本地 `.env`，不进 config
- 每个站一组独立 GitHub Secrets（不复用），降低单站封禁的连锁风险

---

## 5. 功能模块详细需求

### 5.1 内容生产 Pipeline ⚠️（核心模块）

**目的**：从一个关键词出发，自动生产一篇符合 SEO + AdSense 政策 + 用户需求的合格文章并发布。

#### 5.1.1 流程图

```
关键词池（CSV/Sheets）
  ↓
[关键词 Agent] 选择今日要写的 N 个关键词，分析意图、竞争度、长尾扩展
  ↓
[大纲 Agent] 生成 H1、H2、H3、内链建议、需要哪几张图
  ↓
[写作 Agent] 按大纲生成正文（含 markdown frontmatter）
  ↓
[质检 Agent] ⚠️ 评分 + 检查 AI 痕迹 + 验证关键词覆盖
  ├─ 通过 → 进入下一步
  └─ 不通过 → 反馈给写作 Agent 重写（最多 3 轮，仍不过则进人工队列）
  ↓
[配图 Agent] 按大纲指定的图片需求调 Replicate 生图
  ↓
[发布 Agent] 写入 repo 的 src/content/blog/ → git commit → push → Cloudflare 自动部署
  ↓
[索引提交] 调 GSC API 提交 URL Indexing 请求
```

#### 5.1.2 各 Agent 职责定义

##### 关键词 Agent
- **输入**：关键词池（人工初始化 + 后续 GSC API 自动扩展）
- **输出**：今日选题列表（含搜索量、竞争度、推荐文章类型）
- **筛选规则**：
  - 排除已写过的关键词
  - 排除竞争度过高的（KD > 60）
  - 优先长尾词（4 词以上）
  - 同主题词适当聚类（用于内链）

##### 大纲 Agent
- **输入**：单个关键词 + 站点 config（语言、内容类型）
- **输出**：JSON 结构（标题、H2/H3、内链锚文本、图片占位说明）
- **质量要求**：覆盖搜索意图、有数据/列表/表格元素

##### 写作 Agent
- **输入**：大纲 JSON
- **输出**：完整 Markdown（含 frontmatter）
- **强制要求**：
  - 字数在 config 范围内
  - 必须有具体数字、表格或列表
  - 不能出现 AI 痕迹词（"furthermore"、"in conclusion"、"navigating"、"delve into"、"In today's fast-paced world" 等）
  - 必须有 N 个内链（按 config）

##### 质检 Agent ⚠️
- **输入**：写作 Agent 的输出
- **输出**：评分（0-10） + 通过/打回 + 修改建议
- **评分维度**（每项 0-2 分）：
  - 关键词意图匹配度
  - 信息密度（是否有具体数据）
  - 结构合理性
  - 无 AI 痕迹
  - 内链 / SEO 元素完整
- **打回阈值**：总分 < `qa_thresholds.min_quality_score`
- **重要**：质检必须是**独立调用**，不能和写作放在一个 prompt 里

##### 配图 Agent
- **输入**：大纲中的图片需求描述
- **输出**：图片 URL（上传到 Cloudflare R2 / Images，或直接存 repo）
- **避免**：生成名人脸、知名 IP、品牌 logo

##### 发布 Agent
- **输入**：完整文章 + 图片
- **动作**：
  1. 将文章写入 `src/content/blog/<slug>.md`
  2. 图片放入 `public/images/<slug>/`
  3. `git add` + `git commit -m "[auto] new post: <title>"`
  4. `git push`（触发 Cloudflare Pages 自动构建）
  5. 验证 deploy 成功后，调 GSC API 请求索引
- **失败处理**：commit 失败时回滚，发告警

#### 5.1.3 节流与配额

- 每天每站发布 ≤ `content_plan.daily_articles` 篇（默认 3 篇）
- LLM 调用必须有 token 用量统计，单日超阈值（默认 $5/站）告警
- 失败重试上限 3 次，超过进入人工队列

---

### 5.2 数据采集层

**目的**：每天从所有数据源拉取昨日数据，统一存储。

#### 5.2.1 数据源与字段

| 数据源 | 关键字段 | 拉取频率 |
|--------|---------|---------|
| **GA4 API** | sessions, pageviews, avg_session_duration, bounce_rate, top_pages, traffic_sources | 每日 1 次 |
| **GSC API** | clicks, impressions, ctr, position, top_queries, top_pages | 每日 1 次 |
| **AdSense API** | earnings, page_rpm, impression_rpm, ctr, page_views, impressions | 每日 1 次 |
| **FB Marketing API** | spend, impressions, clicks, cpc, ctr, frequency, reach, conversions | 每 6 小时 |
| **Cloudflare Analytics** | unique_visitors, bandwidth, threats_blocked | 每日 1 次（用于交叉校验 GA4） |

#### 5.2.2 存储

- **统一存入 Supabase**：所有原始数据进 `metrics_raw` 表，每日聚合写入 `metrics_daily` 表
- 不再使用 CSV / Google Sheets
- Supabase 免费额度（500MB DB）足够前期 6+ 个月使用

#### 5.2.3 关键计算字段（衍生指标）

每天自动计算并写入 `daily_metrics` 表：

```
date | domain | spend | sessions | pv | adsense_revenue
     | cpc | rpm | ecpc (= revenue / sessions)
     | roi (= (revenue - spend) / spend)
     | pv_per_session | bounce_rate | avg_duration
     | invalid_traffic_pct | adsense_ctr
```

---

### 5.3 日报生成器

**目的**：每日生成数据快照，写入 Dashboard 的 `/reports` 页和总览 Mission Control 区。**不再用即时通讯推送**，因为前端 Dashboard 是统一入口。

**触发时间**：每天 UTC 01:00（北京 09:00 / 新加坡 09:00 / 美东 21:00 前一天）

#### 5.3.1 日报内容结构（写入 daily_reports 表的 Markdown 字段）

```
📊 每日运营报告 — 2026-05-08

━━━━━━━━━━━━━━━━━━
🌐 站点：nte-codex.com（NTE 攻略站）
━━━━━━━━━━━━━━━━━━

💰 财务表现（昨日）
  · 广告花费：$45.20
  · AdSense 收入：$32.10
  · ROI：-29%（接近警戒线 ⚠️）
  · eCPC：$0.13 / CPC：$0.18

📈 流量表现
  · Sessions：342（↑ 12%）
  · PV / Session：2.6（健康 ✅）
  · 平均停留：1m 23s（健康 ✅）
  · 跳出率：62%（健康 ✅）

🔍 SEO 信号（FB Ads 真正 KPI）
  · GSC 自然 clicks：12（上周 5，环比 +140%）✅
  · GSC impressions：4,230（上周 1,800）
  · 索引页面数：47 / 50（94%）

🎯 广告投放
  · 活跃广告组：3 个
  · 最佳：Hotori 角色组，CTR 2.1%
  · 最差：通用组，建议暂停（连续 3 天 ROI < -60%）
  · Frequency 预警：Build Guide 组已达 3.8

📝 内容产出
  · 昨日发布：3 篇（Build × 1 / Tier × 1 / Boss × 1）
  · 质检通过率：100%（首轮 67%）
  · 文章类型分布：均衡 ✅

⚠️ 异常告警（已发 Email）
  · 无 🟢

🤖 AI 解读
  昨日整体表现：FB Ads 直接 ROI 仍为负（预期内），但 SEO 信号本周
  环比上升 140%，符合"FB Ads 作为 SEO 加速器"的预期路径。建议：
  1. 暂停"通用组"广告，节省 $15/天
  2. "Hotori 组"加预算 50%（追新角色热度）
  3. 准备 5/13 上线 Hotori 后的攻略内容批次

📌 待办（需人工处理）
  · 暂停 [广告组 名]（前端"建议执行"按钮 → FB Ads Manager 确认）
```

#### 5.3.2 实现要点

- **数据层（确定性代码）**：所有数字、表格直接从存储层读出，不让 AI 碰
- **解读层（AI 生成）**：把结构化数据传给 Claude，让它生成"AI 解读"段落
- **决策层（硬规则代码）**：暂停 / 加预算建议按预设规则触发，不依赖 AI 判断

#### 5.3.3 呈现通道

- **Web 前端 `/` Mission Control**：当日核心指标 + 健康度（默认入口）
- **Web 前端 `/reports`**：历史日报归档，可按日期翻阅
- **Supabase `daily_reports` 表**：所有日报 Markdown 快照入库，可追溯
- **不再用钉钉/IM 推送**：日报属于"主动消费"信息，每天打开 Dashboard 看一遍即可

---

### 5.4 异常告警

**目的**：不等日报，关键事件实时推送。

#### 5.4.1 告警触发规则

| 事件 | 阈值 | 通道 | 严重度 |
|------|------|------|--------|
| AdSense 无效流量 > 10% | 实时 | **Email + Dashboard** | 🔴 紧急 |
| AdSense CTR > 5% | 实时 | **Email + Dashboard** | 🔴 紧急（疑似作弊） |
| FB 广告组单日花费 > 阈值的 150% | 每小时检查 | **Email + Dashboard** | 🟡 警告 |
| 站点 5xx 错误 | Cloudflare 上报 | **Email + Dashboard** | 🔴 紧急 |
| AI 调用失败连续 ≥ 5 次 | 实时 | Dashboard | 🟡 警告 |
| 内容连续 3 篇质检不过 | 实时 | Dashboard | 🟡 警告 |
| Cloudflare Pages deploy 失败 | 实时 | **Email + Dashboard** | 🟡 警告 |
| LLM 调用异常（单日 >1000 次或单篇 >50K token） | 实时 | Dashboard | 🟡 警告（防死循环，非成本问题）|

#### 5.4.2 告警去重

同一事件 1 小时内只发一次，避免轰炸。

#### 5.4.3 Email 推送设计原则

- **只推送 🔴 紧急告警**，🟡 警告类只写入 Dashboard 不发邮件（避免麻木）
- 邮件主题统一前缀：`[Traffic Ops Alert] <severity> - <category>`
- 邮件正文含：触发条件、当前值、相关 metric 链接（前端 URL）
- SMTP 渠道：用 SendGrid 免费 100 封/天 或 Gmail App Password
- 邮件落地后必须能在手机锁屏推送（操作系统级通知）— 这是 Email 替代 IM 的关键

---

### 5.5 决策硬规则引擎

**目的**：把人为情绪从日常运营决策中剔除。

#### 5.5.1 广告组级规则

```python
# 伪代码示意
if spend_today > 20 USD and conversions == 0:
    suggest("PAUSE")
    
if rolling_3day_roi < -70%:
    suggest("PAUSE")
    
if rolling_2day_roi > 0%:
    suggest("INCREASE_BUDGET_50%")
    
if frequency > 4:
    suggest("REPLACE_CREATIVES")
```

#### 5.5.2 站点级规则

```python
if rolling_7day_roi < -60%:
    suggest("REVIEW_DIRECTION")  # 方向有问题
    
if rolling_7day_roi between -20% and 0%:
    suggest("ADD_CONTENT")  # 加内容靠 SEO 补正 ROI
    
if rolling_14day_roi > 0%:
    suggest("SCALE_UP")  # 加预算 + 复制模式
```

#### 5.5.3 重要原则

- 系统**只输出建议**（在日报里展示），**不自动执行**资金类操作（暂停广告、调预算需要运营者点确认）
- 例外：内容生产可以全自动（不涉及资金风险）

---

### 5.6 管理前端（Web Dashboard - 只读为主）

**目的**：让运营者能可视化看到 AI 系统在做什么，无需打开 Supabase 后台或 GitHub Actions log。

#### 5.6.1 技术选型

- **Next.js 15+** (App Router)
- **Supabase Client**（直连，零后端代码）
- **shadcn/ui + Tailwind CSS**（UI 组件）
- 部署到独立的 Cloudflare Pages 项目（与内容站隔离）
- 用 Supabase Auth 做登录鉴权（Magic Link 或 Google OAuth）

#### 5.6.2 页面结构（MVP）

```
/login                  登录页（Supabase Auth）
/                       总览（多站汇总，MVP 阶段就 1 个站）
/sites/[domain]         单站详情
  ├── /keywords         关键词池（可看分类、状态、搜索量、是否已写）
  ├── /articles         文章列表（状态、质检评分、token 消耗、URL）
  ├── /articles/[id]    文章详情（可预览渲染后内容、看 Agent 执行历史）
  ├── /agent-runs       Agent 执行日志（输入/输出/耗时/失败原因）
  ├── /metrics          指标趋势图（日 PV、Sessions、ROI、AdSense 收入）
  └── /alerts           告警历史
/reports                每日报告归档
```

#### 5.6.3 功能边界

✅ **MVP 阶段做**：
- 所有数据**只读展示**
- 简单筛选、排序、搜索
- 文章 Markdown 预览
- 图表可视化（用 Recharts 或 Tremor）

❌ **MVP 阶段不做**（保留到 Phase 4）：
- 编辑关键词 / 文章内容
- 手动触发任务
- 暂停 / 加预算等资金类操作
- 多用户协作

#### 5.6.4 安全要求

- 必须登录访问，无匿名页
- Supabase RLS 策略：每个用户只能看到 ta 名下的 sites
- 不在前端暴露任何写入凭据（API Key 全部留在 GitHub Actions）
- 前端代码即使开源也不影响安全

### 5.7 GitHub 仓库结构

每个站独立 repo，统一模板：

```
gameguide-x/
├── .github/
│   └── workflows/
│       ├── content-pipeline.yml    # 每日内容生产 cron
│       ├── data-collection.yml     # 每日数据采集 cron
│       └── daily-report.yml        # 每日日报 cron
├── src/
│   ├── content/blog/               # 文章 Markdown
│   ├── pages/                      # Astro 页面
│   ├── components/                 # Astro 组件
│   └── layouts/
├── public/
│   ├── images/                     # 文章配图
│   └── ads.txt                     # AdSense 必备
├── scripts/                        # AI Agent 脚本
│   ├── agents/
│   │   ├── keyword.py
│   │   ├── outline.py
│   │   ├── writing.py
│   │   ├── qa.py
│   │   ├── image.py
│   │   └── publish.py
│   ├── data/
│   │   ├── ga4.py
│   │   ├── gsc.py
│   │   ├── adsense.py
│   │   └── fb_ads.py
│   ├── report/
│   │   └── daily.py
│   └── utils/
├── data/
│   └── keywords.csv                # 关键词池
├── site.config.yaml                # 站点配置
├── pyproject.toml                  # Python 依赖
└── astro.config.mjs
```

---

### 5.8 模型管理（4 层设计）⚠️ 关键

**设计动机**：Gemini 系列模型 3-6 个月迭代一次（Preview → Stable → Deprecation），不能让模型 ID 散落在代码里。一旦 Google 弃用某个模型，硬编码会让 Pipeline 集体失败。

#### 第 1 层：模型目录（model_catalog 表）

数据库存储所有可用模型的元数据：模型 ID、显示名、能力（text/image）、价格、状态（active/preview/deprecated）。**这是系统全局表，不分站**。

每条记录包含：
- `model_id`：调用 API 时用的具体 ID（如 `gemini-3.1-pro-preview`）
- `capability`：text / image / embedding
- `tier`：pro / flash / flash-lite
- `status`：preview / active / deprecated
- `deprecate_at`：已知 EOL 日期（如 Google 公告了的）

**初始 seed 数据**只在系统首次部署时插入一次，之后通过前端管理。

#### 第 2 层：站点配置引用（角色映射）

`sites.config.text_provider.models` 用**逻辑角色**而非具体模型 ID，例如：

```json
{
  "text_provider": {
    "provider": "gemini",
    "models": {
      "writing": "gemini-3-flash-preview",
      "qa": "gemini-3.1-pro-preview",
      "outline": "gemini-3-flash-preview",
      "keyword_research": "gemini-3.1-flash-lite-preview",
      "report_summary": "gemini-3-flash-preview"
    }
  }
}
```

代码里 **永远从 `site.config.text_provider.models[<role>]` 读取模型 ID，从不硬编码**。

#### 第 3 层：前端 Settings 页可视化切换

`/sites/[domain]/settings` 页提供 "Text Models" 区块，下拉选项动态从 `model_catalog` 读取，运营者可：
- 给每个角色（writing/qa/outline/...）选模型
- 看到每个模型的实时价格估算
- "测试调用"按钮验证 API 通畅

**加新模型 = 在 Settings 页 "Add Model" 输入 model_id → 数据库 INSERT 一行 → 下拉立刻可选**。零代码修改。

#### 第 4 层：模型健康检查（每周 cron）

每周一 GitHub Actions 跑 `model_health_check.yml`：
- 对 `model_catalog` 中所有 `status=active` 或 `preview` 的模型发一个最小测试调用
- 失败 → 标记 `status=deprecated`，发 Email 紧急告警
- 成功 → 更新 `last_verified_at`

这样 Google 弃用某个模型后，**最长 7 天内你就会知道**，可以从容切换；不至于某天 Pipeline 突然全停。

#### 模型升级 SOP（你以后唯一要做的事）

```
1. 看 ai.google.dev/gemini-api/docs/changelog（每月一次即可）
2. 发现新模型 → 前端 Settings → Add Model → 填新 model_id
3. 给某个角色（如 writing）切到新模型
4. 点 "测试调用" 验证
5. 保存。下一次 Pipeline 跑就用新模型
```

**整个过程零代码修改、零部署、零停机**。

---

## 6. 关键非功能性需求

### 6.1 合规与风控 ⚠️

#### 6.1.1 AdSense 合规
- 每个站必须有：隐私政策、Cookie 政策、关于、联系页面
- ads.txt 文件配置正确
- 无广告点击诱导（不放"点这里查看更多"在广告附近）
- 自己/团队**绝对不能点自己的广告**

#### 6.1.2 内容合规
- AI 生成的内容必须经过质检 Agent
- 不写 YMYL 内容的具体建议（医疗诊断、投资建议、法律建议）
- 不抄袭现有内容（写作 Agent 必须基于结构化大纲，不能"参考某篇文章"）
- 图片不能含名人脸、品牌 logo、知名 IP

#### 6.1.3 FB Ads 合规
- 落地页内容必须和广告承诺匹配（防 misleading 判定）
- 不用 clickbait 标题党
- 不引导虚假承诺

### 6.2 成本控制

| 项目 | 月度预算上限 | 监控方式 |
|------|-------------|---------|
| LLM API（每站） | **不设上限** | 仅监控异常调用模式（防死循环 bug） |
| 配图 API（每站） | **MVP 期免费**（Gemini 500/天免费 Tier） | 单站日 9 张 << 500，无需告警；超额走付费 |
| FB Ads（每站，验证期） | 自定 | 系统建议 + 人工确认 |
| 域名 | $10/年/个 | — |
| 托管 | $0（Cloudflare 免费） | — |

### 6.3 可观测性

- 所有 AI 调用必须记日志（输入、输出、token、成本、耗时）
- 所有 Git 操作必须记日志
- 关键操作（暂停广告、加预算）必须有审计日志

### 6.4 容灾与备份

- 每个站 GitHub repo 本身就是代码备份
- Google Sheets 数据每周自动导出 CSV 到 GitHub
- API 凭据用 1Password / Bitwarden 备份一份（防 GitHub Secrets 丢失）

---

## 7. 数据指标体系（KPI）

### 7.1 北极星指标

**单站 14 日累计 ROI**

### 7.2 关键过程指标

| 类别 | 指标 | 健康值 | 危险值 |
|------|------|--------|--------|
| 单位经济 | CPC | < $0.20（英文）/ < $0.02（小语种） | > $0.50 |
| 单位经济 | RPM | > $5（英文）/ > $1（小语种） | < $1 |
| 单位经济 | eCPC | > CPC × 1.5 | < CPC |
| 流量质量 | PV/Session | > 2.0 | < 1.5 |
| 流量质量 | 平均停留 | > 45s | < 30s |
| 流量质量 | 跳出率 | < 70% | > 85% |
| 广告健康 | AdSense CTR | 0.5%-3% | > 5% 或 < 0.3% |
| 广告健康 | 无效流量 | < 5% | > 10% |
| FB 广告 | Frequency | < 3.5 | > 4 |
| 内容生产 | 质检首轮通过率 | > 60% | < 40% |
| 内容生产 | 质检最终通过率 | > 90% | < 80% |
| 系统健康 | 任务失败率 | < 5% | > 15% |

---

## 8. 分阶段建造路径（Roadmap）

### Phase 1.A：基础设施 + 上线（Week 1-3）
**目标**：1 个站可访问，自动化内容生产线跑起来，前端可看状态。**不投广告**。

#### Day 1-2：Schema 设计（关键路径）
- Supabase 项目创建（**美国区**）
- 完整 schema 设计（12 张表）
- 评审锁定，**之后不再修改**

#### Day 3-12：双轨并行
**Track A — Pipeline & 数据采集**（Python）
- Domain Onboarding 完成（人工，见 §11）
- Astro 模板搭建（含 About / Privacy / Terms / Contact 4 个合规页）
- 内容生产 Pipeline（关键词调研 → 扩展 → 大纲 → 写作 → 质检 → 配图 → 发布）
- **OutlineAgent 强制内容多样性**：5-8 种文章类型按比例分配
- 数据采集层接入 GA4 / GSC（AdSense / FB Ads 等过审后再接）
- GitHub Actions 调度

**Track B — 前端 Dashboard**（Next.js + Supabase）
- 项目初始化、Supabase 集成、Auth
- Mission Control（`/`）— 项目级里程碑视图
- 关键词池页、文章列表/详情页、Agent 执行日志页
- 指标趋势页、告警页、日报归档页
- Settings 页（图像模型切换）

#### Day 13-15：联调
- Pipeline 写数据 ↔ 前端读数据，闭环验证
- Email 告警集成
- 修 bug

#### Day 15-21：内容铺量到 30 篇
- 内容稳定产出 ≥ 30 篇（含至少 5 种文章类型）
- About 页填充"编辑团队"虚构人设
- 提交 sitemap 到 GSC

❌ **Phase 1.A 不做**：
- AdSense 接入（Phase 1.B 才申请）
- FB Ads 投放（Phase 1.C 才开始）
- 任何前端资金类写操作

### Phase 1.B：AdSense 申请与等待期（Week 4-6）
**目标**：站点过 AdSense 审核，期间继续 SEO 内容铺设。**不投广告**。

✅ 范围：
- 提交 AdSense 申请
- AdSense 代码集成（占位，过审才显示）
- 内容继续产出（每天 3 篇），目标累计 70+ 篇
- 关键词扩展 Agent 跑起来（GSC 反馈数据回流）
- SEO 自然流量监控（GSC clicks 出现就是好信号）

⚠️ **节省策略**：审核期间**不投 FB Ads**，避免无变现的烧钱。

### Phase 1.C：广告投放与 ROI 验证（Week 6-9）
**目标**：FB Ads 跑起来，验证"加速 SEO + AdSense 收入" 的闭环。

✅ 范围：
- AdSense 过审后开始 FB Ads 投放
- FB Ads / AdSense API 接入数据采集
- 决策硬规则引擎跑起来
- 14 天累计 ROI 数据出来

⚠️ **关键**：**不要期望 FB Ads 直接 ROI 为正**。FB Ads 的真实 KPI 是：
- 14 天后 GSC 自然 clicks 是否周环比 +50% 以上
- 总 AdSense 收入（FB 流量 + 自然流量合计）覆盖广告成本

### Phase 2：复盘与决策（Week 9-10）
**目标**：基于 Phase 1.C 数据决定 Pivot 还是 Scale Up

决策树：
```
14 天 SEO 自然流量 +50% 以上 + 总 ROI > -20%
  → Scale Up：加预算 + 复制到第 2 个站
  
SEO 信号弱（< +20%）+ 总 ROI < -50%
  → Pivot：换游戏方向 / 换流量来源
  
中间区间
  → 优化：内容质量、广告素材、落地页
```

### Phase 3：多站复制（Week 10-16）
**目标**：第 2 个站启动 + SOP 固化

✅ 范围：
- 站点模板抽离为 repo template
- Onboarding checklist 文档化
- 跨站数据汇总报告
- 前端从单站升级为多站总览

### Phase 4：完整管理 Dashboard 🔵（Week 16+）
**目标**：3+ 站后再做

✅ 范围：
- 前端加入写操作（编辑关键词、触发任务、AI 决策建议中心）
- ROI 决策中心 + 一键操作（暂停 / 加预算）
- 多用户协作 + 角色权限

✅ 范围：
- 前端加入写操作（编辑关键词、触发任务、AI 决策建议中心）
- ROI 决策中心 + 一键操作（暂停 / 加预算）
- 多用户协作 + 角色权限

---

## 9. 风险登记册（Risk Register）

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|-----|---------|
| AdSense 账号被封 | 🔴 极高 | 中 | 内容质检严格、独立 GA Property、不点自家广告、监控无效流量 |
| AI 生成内容被判 scaled abuse | 🔴 高 | 中 | 质检 Agent 严格、加入信息密度要求、配图原创 |
| FB 广告 ROI 长期为负 | 🟡 中 | 高 | 预算硬限制、Pivot 触发条件、做好"亏 $300 当学费"心理准备 |
| LLM API 调用 bug 失控 | 🟡 中 | 中 | 异常调用模式监控（单日 >1K 次或单篇 >50K token 告警），**成本本身不限制** |
| GitHub Actions 配额超限 | 🟢 低 | 低 | 免费额度 2000 分钟/月足够 1-3 站 |
| 单点故障（key 泄露） | 🔴 高 | 低 | 每站独立凭据、定期轮换 |
| 内容方向选错 | 🔴 高 | 中 | MVP 阶段只做 1 个站，14 天硬决策点 |
| Cloudflare Pages 政策变化 | 🟡 中 | 低 | 保留迁移到 Vercel/Netlify 的能力（保持架构无锁定） |

---

## 10. 验收标准（DoD - Definition of Done）

### Phase 1.A 验收
- [ ] 1 个站可以无人值守运行 72 小时
- [ ] 自动产出 ≥ 30 篇内容，覆盖至少 5 种文章类型
- [ ] 质检通过率 ≥ 90%
- [ ] 站点 Lighthouse SEO 分 ≥ 90
- [ ] About / Privacy / Terms / Contact 4 个合规页齐全
- [ ] 前端 Mission Control + 8 个数据页全部可用
- [ ] 数据采集（GA4 + GSC）每日自动写入 Supabase
- [ ] Email 紧急告警通道测试通过

### Phase 1.B 验收
- [ ] AdSense 申请已提交
- [ ] 内容累计达 70+ 篇
- [ ] 至少 30 篇文章在 GSC 已被索引
- [ ] GSC clicks 出现首次自然流量（哪怕 1 个）

### Phase 1.C 验收
- [ ] AdSense 过审
- [ ] FB Ads 跑起来，ROI 数据完整回流
- [ ] 决策硬规则触发过 ≥ 1 次有效告警
- [ ] 14 天累计数据可在 Mission Control 查看

### Phase 2 验收
- [ ] 14 天复盘报告生成
- [ ] 已做出 Scale Up / Pivot / Optimize 决策

### Phase 3 验收
- [ ] 第 2 个站从 0 到首批内容上线 ≤ 5 天
- [ ] 跨站汇总报告可用

---

## 11. 一次性人工操作清单（你来做，不需要代码）

> 这部分是你说的"由人工完成"的环节。每开一个新站执行一次。

### 11.1 域名与 DNS
- [ ] 在 GoDaddy 购买域名
- [ ] 将 DNS 服务器改为 Cloudflare（更便宜、更快、必备）
- [ ] 在 Cloudflare 添加站点，验证 DNS 接管
- [ ] 设置 SSL 模式为 Full (Strict)

### 11.2 GitHub
- [ ] 用站点 repo 模板创建新 repo（私有）
- [ ] 配置 GitHub Secrets（参见下方清单）

### 11.3 Cloudflare Pages
- [ ] 创建 Pages 项目，连接 GitHub repo
- [ ] 配置 build 命令（Astro: `npm run build`，输出 `dist`）
- [ ] 绑定自定义域名

### 11.4 Google Analytics 4
- [ ] 创建新 GA4 Property（每个站独立，不要共用）
- [ ] 创建数据流，获取 Measurement ID
- [ ] 在 GA4 Admin 中创建服务账号并下载 JSON 凭据
- [ ] 把 Measurement ID 填入 `site.config.yaml`
- [ ] 把服务账号 JSON 加入 GitHub Secrets

### 11.5 Google Search Console
- [ ] 添加 Property（用 DNS 验证，永久有效）
- [ ] 提交 sitemap.xml
- [ ] 创建服务账号（可与 GA4 共用），授权 GSC 读权限
- [ ] 把凭据加入 GitHub Secrets

### 11.6 Google AdSense
- [ ] 内容铺到 30+ 篇后申请 AdSense
- [ ] 配置 ads.txt 到 `public/ads.txt`
- [ ] 过审后获取 Publisher ID，填入 config
- [ ] 创建 AdSense Management API 凭据，加入 GitHub Secrets

### 11.7 Facebook Business
- [ ] Business Manager 创建新广告账户（每站独立，**不要共用**，降低封号连锁）
- [ ] 创建 Pixel，安装到站点
- [ ] 创建 System User Token（长期有效）
- [ ] 把 Ad Account ID、Pixel ID、Token 加入 GitHub Secrets

### 11.8 LLM + 图像 API（Gemini 一站式）
- [ ] AI Studio 创建 Gemini API Key（一个 Key 同时支持文本 + 图像）
- [ ] 设置 Cloud Console Billing 预算告警（推荐 $20）
- [ ] 加入 GitHub Secrets（变量名 `GEMINI_API_KEY`）
- [ ] 不再需要 Anthropic / OpenAI / Replicate（MVP 阶段）

### 11.10 通知（Email）
- [ ] 准备一个专用收件邮箱（建议项目专用 Gmail）
- [ ] 选择 SMTP 方式：
  - 选项 A：Gmail App Password（免费，需开启 2FA）
  - 选项 B：SendGrid 免费 100 封/天（推荐，更稳定）
- [ ] 把 SMTP 凭据加入 GitHub Secrets
- [ ] 在手机邮件 App 给该收件邮箱开启**锁屏推送通知**（关键！）

### 11.11 Supabase（统一数据层）
- [ ] supabase.com 注册账号（免费 Tier 足够）
- [ ] 创建项目，区域选 **US East (N. Virginia)** — 与 GitHub Actions 同区，访问最快
- [ ] 记下 Project URL 和 anon key、service_role key
- [ ] 服务端用 service_role key（GitHub Secrets 配置）
- [ ] 前端用 anon key（搭配 RLS 策略保证安全）
- [ ] schema 由代码 migration 创建，**不要手动建表**

### 11.12 前端管理界面（首次设置）
- [ ] 单独创建一个 GitHub repo（如 `traffic-ops-dashboard`）
- [ ] Cloudflare Pages 创建第二个项目，绑定该 repo
- [ ] 子域名建议：`admin.<你某个域名>`（不需另购域名）
- [ ] 在 Supabase Auth 配置允许的回调 URL

### 11.13 GitHub Secrets 完整清单（多站隔离方案）

> **完整说明见 CREDENTIALS-SETUP.md**。本节给出参考列表。

```
# === 全局共享 (所有站共用) ===

# AI（Gemini 一站式：文本 + 图像）
GEMINI_API_KEY

# Supabase
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY

# Email (SMTP)
SMTP_HOST                     # smtp.gmail.com 或 smtp.sendgrid.net
SMTP_PORT                     # 587
SMTP_USER
SMTP_PASS
ALERT_RECIPIENT_EMAIL

# Cloudflare
CLOUDFLARE_API_TOKEN
CLOUDFLARE_ACCOUNT_ID

# Git
GITHUB_TOKEN
GIT_USER_NAME
GIT_USER_EMAIL


# === 每站专属（前缀：site_slug 大写）===

# 首站 NTE（site_slug=ntecodex）：
NTECODEX_GA4_PROPERTY_ID
NTECODEX_FB_AD_ACCOUNT_ID
NTECODEX_FB_PIXEL_ID
NTECODEX_FB_ACCESS_TOKEN
NTECODEX_ADSENSE_PUBLISHER_ID            # 过审后填，前期填 'pending'
NTECODEX_GOOGLE_SERVICE_ACCOUNT_JSON     # GA4 + GSC + AdSense 共用一份 JSON
```

加新站时新增一组 `<NEW_SLUG>_*` Secrets 即可，不改代码。

---

## 12. 首站确认信息（v0.8 已锁定）

### 12.1 首站：Neverness to Everness (NTE) 攻略站（英文）

**关于游戏选择的变更（v0.7 → v0.8）**：
原方案 Phantom Blade Zero 因预热期搜索意图弱、发售延期风险高，已改为 **NTE（已发售 Gacha）**。NTE 的优势：
- **2026/4/29 已全球上线**，目前是首发流量爆炸期
- Gacha 模式 → 每 6 周新角色 → 搜索量永不枯竭
- 现有竞争站（gachalab.gg / gamewith.net）已存在但 Game8 / Prydwen 尚未建专站，**1-2 个月窗口期**
- 内容结构高度规整，AI 极易批量生产（角色 Build / 武器 Arc / 队伍 / Tier / 抽卡模拟器）

**域名建议命名风格**（避开商标）：
- `ntecodex.com` ⭐⭐⭐⭐⭐
- `nteguide.com` ⭐⭐⭐⭐
- `nteversebuilds.com` ⭐⭐⭐⭐
- `nehub.gg` ⭐⭐⭐
- 避免：`neverness***.com`（与官方名一致，商标风险）

**内容策略分阶段**：

| 阶段 | 时间 | 内容类型 | 例子 |
|------|------|---------|------|
| **首发爆发期** | 2026/5（**当下**） | Build / Tier / Boss / Reroll | "Nanally Build Guide"、"NTE Tier List May 2026"、"How to Reroll in NTE" |
| **5/13 Hotori 上线** | 2026/5/13 - 5/27 | 新角色专题 | "Hotori Build"、"Misty Tipsy Style banner worth pulling?" |
| **持续运营期** | 2026/6+ | 每个新版本/新角色专题 | 跟着官方版本节奏出 |

**关键词种子分类（Phase 1 启动用，AI 调研后扩展到 200+）**：

| 类别 | 数量 | 例子 |
|------|------|------|
| 角色 Build | 20-30 | "Nanally build", "best Nanally team", "Nanally vs Hotori" |
| Tier List | 5-10 | "NTE tier list", "best NTE characters" |
| 抽卡相关 | 10-15 | "NTE pity system", "Scarborough Fair guide", "best banner to pull" |
| 武器 Arc | 15-20 | "best Arcs for [character]", "S-rank Arc list" |
| Boss 攻略 | 15-20 | "how to beat [boss]", "[boss] weak point" |
| Reroll 指南 | 5-8 | "NTE reroll guide", "best starter pulls" |
| 新闻/版本 | 5-10 | "Hotori release date", "NTE 1.1 update" |
| FAQ | 10-15 | "is NTE on PS5", "NTE PC requirements" |

**内容多样性强制要求**：每天 3 篇必须分散在至少 2 种不同类型，由 OutlineAgent 自动调度。

### 12.2 首期预算（已锁定 ~$210 + LLM/Gemini 不设上限）

| 项目 | 金额 | 说明 |
|------|------|------|
| FB Ads 验证预算 | $200 | Phase 1.C 阶段使用，约 14 天 |
| LLM API（Gemini，文本+图像） | **MVP 期免费** | 免费 Tier 充裕，单站日 3 篇内容远低于配额 |
| Gemini Image API | **MVP 期免费** | 500 张/天免费 Tier，单站 9 张/天远低于此 |
| 域名（一次性） | $10 | |
| **总计（不含 LLM）** | **$210** | |

**预算硬规则**（写入代码）：
- 单日 FB 花费上限 $20，超了系统强制暂停广告组
- ~~LLM 单日上限 $2~~ → **取消**，LLM 用量仅做"异常监控"
- 14 天累计亏损 > $200 → 系统强制停所有付费投放，触发"决策点"工单

**LLM/图像异常监控（保留但不阻断）**：
- 单日 LLM 调用 > 1000 次 → Email 提醒（防死循环 bug）
- 单篇文章 token 消耗 > 50K → Dashboard 提醒
- Gemini 单日调用 > 400 张 → Dashboard 提醒（接近免费 Tier 上限）

### 12.3 运营节奏

- **每天投入时间**：30 分钟看 Mission Control + 决策
- **日报呈现时间**：每天 UTC 01:00 生成（≈ 北京 09:00 / 新加坡 09:00 / 美东 21:00 前一天）
- **入口**：打开 Dashboard 的 `/`（Mission Control）即可看当日数据 + AI 解读 + 待办

### 12.4 质检失败决策规则（已锁定）

| 触发条件 | 系统动作 |
|---------|---------|
| 单篇质检 3 轮重试仍不过 | 进入人工队列（不浪费 token），Dashboard 标记 |
| 连续 5 篇首轮质检不过 | Dashboard 告警，自动暂停内容生产 |
| 连续 7 天首轮通过率 < 40% | Email + Dashboard 重要告警，标记为"方向选错"风险 |

理由：5 篇是"小样本但有信号"的临界，7 天是排除单日波动的窗口；3 轮重试是单篇成本上限。

### 12.5 项目级 Mission Control（前端 `/` 总览页）

这是运营者每天**第一眼**看到的页面，回答"项目跑得怎么样"：

```
🎯 阶段：Phase 1.C 广告投放期（第 X 天 / 21 天）

📊 关键里程碑：
✅ 首站上线（D5）
✅ AdSense 提交（D14）
✅ AdSense 过审（D24）
⏳ 14 天累计 ROI：-32%（目标：> -20%）
⏳ SEO 自然流量：本周 12 clicks（上周 5，环比 +140%）✅
🟡 FB Ads 直接 ROI：-58%（预期范围内）

🚦 项目健康度：🟢 良好（按预期推进）

下一关键决策点：D14（再 7 天）
若总 ROI > -20% 且 SEO 周增 +50% → Scale Up
若总 ROI < -50% 且 SEO 周增 < +20% → Pivot

[查看完整日报] [查看告警] [查看指标趋势]
```

实现要点：
- 当前 Phase 自动判断（基于配置 + 时间）
- 里程碑列表配置在 `sites.config.milestones`
- 健康度按规则引擎计算
- "下一决策点"提示项目即将进入的关键时刻

---

## 13. 附录

### 13.1 术语表

| 术语 | 定义 |
|------|------|
| ROI | Return on Investment = (广告收入 - 广告花费) / 广告花费 |
| RPM | Revenue Per Mille = 千次展示收入 |
| CPC | Cost Per Click |
| eCPC | Effective CPC = AdSense 收入 / Sessions |
| YMYL | Your Money or Your Life，Google 对医疗/财经等内容的高标准要求 |
| Scaled Content Abuse | Google 对低质量批量 AI 内容的打击政策 |
| E-E-A-T | Experience, Expertise, Authoritativeness, Trust |
| Frequency | 同一用户看到广告的次数（FB Ads 指标） |

### 13.2 参考资料链接（待补充）

- AdSense 政策：https://support.google.com/adsense/answer/48182
- Google Scaled Content Abuse 政策
- Astro 文档：https://docs.astro.build
- Cloudflare Pages 文档：https://developers.cloudflare.com/pages

---

**文档结束**

下一步：进入 Phase 1 实施（搭建脚手架 + 关键词调研 + 第一批内容生产 Pipeline）。
