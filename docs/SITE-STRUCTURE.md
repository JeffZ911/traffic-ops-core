# NTE Codex 站点结构设计（SITE-STRUCTURE）

> 配套文档：CODE-SPEC.md v1.3 §3.5 PublishAgent + Astro 站点
> 文档版本：v1.0
> 目的：定义 ntecodex.com 的页面结构、URL 路由、article_type 渲染映射
> 适用阶段：Phase 1.A 后期（PublishAgent + Astro 模板开发）
>
> **核心理念**：不做"一堆 blog post"的内容农场，做一个有结构的游戏 Wiki + 攻略门户

---

## 0. 设计动机

### 为什么不用纯博客结构

纯博客结构（单层 `/articles/<slug>`）的问题：

| 问题 | 后果 |
|------|------|
| 用户进站只看一篇就跳出 | 平均 PV/Session ≈ 1.2 |
| 没有"工具型"内容 | 平均停留 ≈ 30 秒 |
| AdSense 广告位单一 | CPM 低 |
| Google 看到的就是"普通 blog 站" | SEO 排名一般 |
| 缺乏"必读入口" | 没有用户回访动力 |

### 多层结构的优势（对标 GachaLab / GameWith / Game8）

| 优势 | 实现方式 |
|------|---------|
| 入口多样 | Tier List / Reroll / 角色页 / 工具 各自独立入口 |
| 用户停留长 | Pity Calculator 等工具页能停 5+ 分钟 |
| SEO 长尾覆盖 | 每个角色页一个独立 URL，可吃名字 + 别名 + 攻略词 |
| AdSense CPM 高 | 工具页用户停留长，广告曝光质量高 |
| 内容容易扩展 | 每个区块独立维护，不互相干扰 |

---

## 1. 完整 URL 结构

```
ntecodex.com/
│
├── /                              首页 — "门户"
│
├── /tier-list                     Tier List 总览页（动态聚合）
│
├── /characters/                   角色数据库
│   ├── /                          全部角色列表（带筛选器）
│   └── /<slug>                    单个角色详情页
│
├── /weapons/                      Arc 武器数据库
│   ├── /
│   └── /<slug>
│
├── /banners/                      抽卡 banner
│   ├── /current                   当前 banner
│   ├── /upcoming                  已确认未来 banner
│   └── /history/<slug>            历史 banner（SEO 长尾）
│
├── /tools/                        互动工具（Phase 2，预留位）
│   ├── /pity-tracker              抽卡保底计算器
│   ├── /pull-simulator            模拟抽卡
│   └── /budget-calculator         Annulith 预算计算
│
├── /guides/                       攻略文章库
│   ├── /                          全部攻略列表
│   ├── /reroll/<slug>             Reroll 指南（subdir）
│   ├── /beginners/<slug>          新手指南（subdir）
│   └── /<slug>                    通用长文攻略（build/comparison）
│
├── /boss/                         Boss 攻略
│   ├── /
│   └── /<slug>
│
├── /news/                         新闻 / 版本更新
│   ├── /
│   └── /<slug>
│
├── /faq                           常见问题（聚合页）
│
├── /search                        站内搜索（Phase 2）
│
├── /about                         关于
├── /privacy                       隐私政策
├── /terms                         使用条款
├── /contact                       联系
│
└── /sitemap.xml                   SEO sitemap
└── /robots.txt
└── /ads.txt                       AdSense 验证
```

---

## 2. article_type → URL 渲染映射表（关键）

CODE-SPEC §2.2.4 的 `articles.article_type` 共 9 个值，每个对应不同 URL 模式 + 渲染样式：

| article_type | 站点 URL 模式 | 渲染样式 | 数据来源 |
|--------------|--------------|---------|---------|
| `build` | `/guides/<slug>` | 长文模板 | articles 表 |
| `boss_guide` | `/boss/<slug>` | 长文 + Boss 数据卡 | articles 表 |
| `reroll` | `/guides/reroll/<slug>` | 长文 + 步骤列表 | articles 表 |
| `character_db` | `/characters/<slug>` | **结构化卡片** | articles 表 |
| `weapon_db` | `/weapons/<slug>` | **结构化卡片** | articles 表 |
| `tier_list` | **不渲染单页**，聚合到 `/tier-list` | 动态聚合表格 | articles 表 |
| `news` | `/news/<slug>` | 短文 + 时间戳 | articles 表 |
| `faq` | **不渲染单页**，聚合到 `/faq` | Q&A accordion 列表 | articles 表 |
| `comparison` | `/guides/<slug>` | 长文 + 对比表 | articles 表 |

### 为什么 tier_list 和 faq 不渲染单页

- **tier_list**：Tier 排序变化频繁（每次新角色上线），每篇 article 可能只覆盖某一类（DPS / Healer），用户期望看到**统一的整合表**，不是 10 篇分开的文章
- **faq**：单个 Q&A 太短不适合独立页面，聚合到一页用户体验更好，SEO 也更聚焦

---

## 3. 页面详细规格

### 3.1 首页 `/`

**目标**：用户进站 5 秒内看到核心价值（推荐角色 / 当前 banner / 工具入口）

**布局**（自上而下）：

```
┌────────────────────────────────────────┐
│ Header: Logo + 主导航 + Search 入口    │
├────────────────────────────────────────┤
│ Hero Section                           │
│  ├─ 当前 Banner 角色大图 + 倒计时     │
│  └─ "Reroll Now" / "Tier List" CTA    │
├────────────────────────────────────────┤
│ Tier List 摘要（S+ / S / A 三档）       │
│  → 跳转 /tier-list                     │
├────────────────────────────────────────┤
│ 工具区（Phase 2 上线）                 │
│  ├─ Pity Tracker                       │
│  ├─ Pull Simulator                     │
│  └─ Budget Calculator                  │
├────────────────────────────────────────┤
│ 最新攻略 (6 篇)                         │
│  按 published_at desc，排除 character_db│
├────────────────────────────────────────┤
│ 角色数据库快捷入口（4-6 张角色卡）     │
│  → /characters/                        │
├────────────────────────────────────────┤
│ Footer: AdSense 兜底 + 链接群           │
└────────────────────────────────────────┘
```

**数据查询**：

```sql
-- 当前 Banner: banners 表（暂存于 articles 的 news 子集）
-- 后续可建 banners 独立表，Phase 1.A 暂用 articles+article_type='news' 标记

-- Tier List 摘要: select all character_db articles, order by tier
SELECT slug, title, outline->>'tier' as tier
FROM articles
WHERE site_id=$1 AND article_type='character_db' AND status='published'
ORDER BY outline->>'tier_rank';

-- 最新攻略: 6 篇 published_at desc，排除 character_db / weapon_db
SELECT slug, title, article_type, published_at
FROM articles
WHERE site_id=$1 AND status='published'
  AND article_type NOT IN ('character_db','weapon_db','tier_list','faq')
ORDER BY published_at DESC
LIMIT 6;
```

### 3.2 Tier List `/tier-list`

**目标**：一页展示所有角色按 Tier 分组的完整排名表

**布局**：

```
┌────────────────────────────────────────┐
│ H1: NTE Tier List - Best Characters    │
│ Last Updated: 2026-05-10               │
├────────────────────────────────────────┤
│ [ All | DPS | Support | Healer ] 切换  │
├────────────────────────────────────────┤
│ S+ Tier                                │
│  [角色1卡] [角色2卡] [角色3卡]           │
├────────────────────────────────────────┤
│ S Tier                                 │
│  [...]                                 │
├────────────────────────────────────────┤
│ A Tier                                 │
│  [...]                                 │
├────────────────────────────────────────┤
│ B Tier                                 │
│  [...]                                 │
├────────────────────────────────────────┤
│ C Tier                                 │
│  [...]                                 │
├────────────────────────────────────────┤
│ "Why this ranking?" 解释段落            │
│  (从某个 article_type='tier_list' 的 article 拉)│
├────────────────────────────────────────┤
│ 相关攻略链接（每个角色对应 build 文）   │
└────────────────────────────────────────┘
```

**关键技术点**：

- 这个页面**不是从单篇 article 渲染**，而是**聚合渲染**
- Astro 用 `getStaticPaths()` 在 build 时查询所有 character_db 类 articles
- 每个角色卡显示：头像 + 名字 + Tier + 角色定位 + 链接到 `/characters/<slug>`
- Tier 排序信息存在 `articles.outline.tier` (jsonb 字段)

### 3.3 角色页 `/characters/<slug>`

**目标**：一个角色的完整信息聚合，长尾 SEO 主战场

**布局**：

```
┌────────────────────────────────────────┐
│ Breadcrumb: Home > Characters > Nanally│
├────────────────────────────────────────┤
│ 顶部信息块                              │
│  ├─ 角色立绘（左）                      │
│  └─ 基础信息（右）                      │
│      ├─ 名字 / 别名 / 稀有度             │
│      ├─ Tier 标签 + Role 标签           │
│      ├─ 元素 / 武器类型                 │
│      └─ "Best Build" 跳转链接           │
├────────────────────────────────────────┤
│ Tab 导航                                │
│  [Overview | Skills | Materials |       │
│   Best Build | Teams | Synergies | Lore]│
├────────────────────────────────────────┤
│ Overview 内容                           │
│  - 角色定位 / 强弱评价 / 适合谁玩       │
├────────────────────────────────────────┤
│ Skills 内容                             │
│  - 普攻 / 技能 / 大招 / 被动            │
│  - 每个技能含描述 + 数值 + 加点优先级   │
├────────────────────────────────────────┤
│ Materials                               │
│  - 升级材料表（图标 + 数量）            │
├────────────────────────────────────────┤
│ Best Build                              │
│  - 推荐 Arc（武器）                     │
│  - 推荐 Disk（圣遗物）                  │
│  - 主词条 / 副词条优先级                 │
├────────────────────────────────────────┤
│ Teams                                   │
│  - 推荐队伍 1 / 2 / 3（每个含其他角色卡）│
├────────────────────────────────────────┤
│ Synergies                               │
│  - 与哪些角色配合好（链接到对应角色页）  │
├────────────────────────────────────────┤
│ AdSense 广告位                          │
├────────────────────────────────────────┤
│ "Related Guides" 链接（同名角色的所有 build/comparison 文）│
└────────────────────────────────────────┘
```

**数据来源**：

- 主 article（type=character_db）的 `outline` 字段含所有结构化数据
- `articles.content_md` 存 Markdown 化的长文（Lore/Story 部分）
- 通过 article_keywords 关联到角色名关键词（确保 SEO 正确）

**outline JSON 结构示例**（character_db 类型）：

```json
{
  "character_id": "nanally",
  "rarity": 5,
  "element": "Spirit",
  "weapon_type": "Sword",
  "tier": "S+",
  "role": ["DPS"],
  "release_banner": "Spring 2026",
  "skills": {
    "basic_attack": { "name": "...", "description": "...", "scaling": [...] },
    "skill": { "name": "...", "description": "...", "cooldown_sec": 12 },
    "ultimate": { "name": "...", "description": "...", "energy_cost": 60 },
    "passives": [...]
  },
  "ascension_materials": [
    { "level": 20, "items": [...] },
    { "level": 40, "items": [...] }
  ],
  "best_build": {
    "weapons": ["Eclipse Blade", "Moonshade"],
    "disks": [
      { "set": "Stormrider 4pc", "main_stats": {...}, "sub_priority": [...] }
    ]
  },
  "teams": [
    { "name": "Hyper-Carry Comp", "members": ["nanally", "hotori", "support1", "shielder1"] }
  ]
}
```

### 3.4 Banners 页 `/banners/*`

**目标**：抽卡运营信息，**高时效**，**回访高频**

`/banners/current` — 当前限定 banner
- 主推角色立绘 + 倒计时
- 角色信息卡 + 推荐拉取理由
- 详细概率说明

`/banners/upcoming` — 已确认未来 banner
- 时间轴形式
- 每个 banner 一张卡

`/banners/history/<slug>` — 历史 banner
- SEO 长尾流量（"X 角色什么时候 up 过"）
- 一篇 banner 一个 URL

**Phase 1.A 实现**：暂用 articles 表 + article_type='news' 子集存。Phase 1.B 起建独立 `banners` 表。

### 3.5 Tools 页 `/tools/*`（Phase 2）

**Phase 1.A 不实现，先预留**。

#### Pity Tracker `/tools/pity-tracker`

抽卡保底进度追踪器：
- 用户输入"已抽次数" + "已出 5 星次数"
- 计算"距离保底还差多少抽"
- 用户数据**只存在 localStorage**，不上传服务器（隐私优先）

#### Pull Simulator `/tools/pull-simulator`

模拟抽卡：
- 用户点"Pull 1" / "Pull 10"
- 按真实概率随机出货
- 累计统计：S/A/B 出货率

#### Budget Calculator `/tools/budget-calculator`

预算计算：
- 用户输入"每月免费 Annulith 数 + 月卡 + 大月卡"
- 计算"每个月能保底几抽"

**这些工具是纯前端 React/JS，不调用 LLM**，是 Phase 2 的纯工程任务。

### 3.6 Guides 页 `/guides/*`

通用攻略文章库，对应 article_type 中的 `build` / `comparison` / 长文型内容。

`/guides/` — 全部攻略列表（卡片式，带筛选）
`/guides/reroll/<slug>` — Reroll 类专属（subdir 提高 SEO 主题集中度）
`/guides/beginners/<slug>` — 新手指南专属
`/guides/<slug>` — 其他

### 3.7 FAQ 页 `/faq`

聚合所有 article_type='faq' 的 article，渲染为 accordion（折叠面板）：

```
┌────────────────────────────────────────┐
│ H1: Frequently Asked Questions         │
├────────────────────────────────────────┤
│ Categories: [All | Reroll | Combat | ..│
├────────────────────────────────────────┤
│ ▼ How do I reroll in NTE?              │
│   答案 markdown 渲染                    │
│ ▶ What's the best beginner team?       │
│ ▶ How does pity work in this game?     │
│ ...                                    │
└────────────────────────────────────────┘
```

**SEO 优势**：FAQ schema markup（structured data），Google 会展示富摘要。

---

## 4. 路由实现技术细节（Astro）

### 4.1 文件路由结构

```
ntecodex-site/
└── src/
    ├── pages/
    │   ├── index.astro                    → /
    │   ├── tier-list.astro                → /tier-list
    │   ├── faq.astro                      → /faq
    │   ├── about.astro                    → /about
    │   ├── privacy.astro                  → /privacy
    │   ├── terms.astro                    → /terms
    │   ├── contact.astro                  → /contact
    │   ├── characters/
    │   │   ├── index.astro                → /characters/
    │   │   └── [slug].astro               → /characters/<slug>
    │   ├── weapons/
    │   │   ├── index.astro                → /weapons/
    │   │   └── [slug].astro               → /weapons/<slug>
    │   ├── banners/
    │   │   ├── current.astro              → /banners/current
    │   │   ├── upcoming.astro             → /banners/upcoming
    │   │   └── history/
    │   │       └── [slug].astro           → /banners/history/<slug>
    │   ├── guides/
    │   │   ├── index.astro                → /guides/
    │   │   ├── [slug].astro               → /guides/<slug>
    │   │   ├── reroll/
    │   │   │   └── [slug].astro           → /guides/reroll/<slug>
    │   │   └── beginners/
    │   │       └── [slug].astro           → /guides/beginners/<slug>
    │   ├── boss/
    │   │   ├── index.astro                → /boss/
    │   │   └── [slug].astro               → /boss/<slug>
    │   ├── news/
    │   │   ├── index.astro                → /news/
    │   │   └── [slug].astro               → /news/<slug>
    │   └── tools/                         (Phase 2)
    │       ├── pity-tracker.astro
    │       ├── pull-simulator.astro
    │       └── budget-calculator.astro
    ├── components/
    │   ├── Header.astro
    │   ├── Footer.astro
    │   ├── CharacterCard.astro
    │   ├── TierTable.astro
    │   ├── ArticleCard.astro
    │   ├── AdSlot.astro                   广告位组件
    │   └── ...
    ├── layouts/
    │   ├── BaseLayout.astro               基础模板（含 SEO meta）
    │   ├── ArticleLayout.astro            长文页模板
    │   └── CharacterLayout.astro          角色页模板（结构化）
    └── lib/
        ├── supabase.ts                    Supabase 客户端
        └── queries.ts                     SQL 查询封装
```

### 4.2 数据获取策略

Astro 在 **build time**（不是 runtime）从 Supabase 拉数据：

```typescript
// src/pages/characters/[slug].astro
---
import { getCharacterBySlug, getAllCharacterSlugs } from '../../lib/queries';

export async function getStaticPaths() {
  const slugs = await getAllCharacterSlugs();
  return slugs.map(slug => ({ params: { slug } }));
}

const { slug } = Astro.params;
const character = await getCharacterBySlug(slug);
---
<CharacterLayout title={character.title} character={character}>
  <!-- 渲染 ... -->
</CharacterLayout>
```

**好处**：
- 静态生成，Cloudflare Pages 直接 CDN 全球加速
- SEO 完美（HTML 完整渲染）
- AdSense 兼容（不需要 SSR）

**代价**：
- 每次发新文章要重新 build（GitHub Actions 触发）
- build 时间随文章数线性增长（100 篇文章 ≈ 1-2 分钟 build）

### 4.3 sites.config.tools_enabled 控制

```yaml
# sites.config (Phase 1.A)
tools_enabled: []   # 不渲染 /tools/* 路由

# sites.config (Phase 2)
tools_enabled:
  - pity_tracker
  - pull_simulator
```

Astro `getStaticPaths` 检查 `tools_enabled`，决定是否渲染 `/tools/*` 路由。**Phase 1.A 站点不会有这些 URL**，但代码已就位。

---

## 5. SEO 设计

### 5.1 内部链接策略

每个页面都要有合理的"内链"：

| 页面类型 | 应该链接到 |
|---------|-----------|
| 首页 | Tier List / 最新文章 / 主推角色 |
| Tier List | 每个角色的详情页 |
| 角色页 | "Best Build" 文章、Synergies 中的其他角色 |
| 攻略文章 | 文章中提到的角色页 / 武器页 |
| FAQ | 相关攻略文章 |

### 5.2 结构化数据

每个页面类型用对应的 schema.org JSON-LD：

| 页面 | Schema |
|------|--------|
| 角色页 | `VideoGame` + `Person` |
| 攻略文章 | `Article` |
| FAQ | `FAQPage` |
| Tier List | `ItemList` |
| 首页 | `WebSite` + `SearchAction` |

### 5.3 Sitemap

`/sitemap.xml` 自动生成，包含所有页面 URL，Astro 用 `@astrojs/sitemap` 集成。

---

## 6. 与 CODE-SPEC 的衔接

### 6.1 Schema 不需要改

当前 13 张表足够支撑这个站点结构。`articles.outline` (jsonb) 字段灵活承载各种结构化数据。

### 6.2 PublishAgent 需要分类发布

CODE-SPEC §3.5 PublishAgent 当前简化版：
```python
def publish(article):
    write_to_repo(f"articles/{article.slug}.md", article.content_md)
```

**需要升级为**：
```python
def publish(article):
    url_path = ARTICLE_TYPE_TO_URL[article.article_type]
    # url_path 例如 "/characters/<slug>" / "/guides/<slug>"
    write_to_repo(
        path=f"src/pages{url_path.replace('<slug>', article.slug)}.astro",
        content=render_template(article)
    )
    # tier_list / faq 类型不写文件，只更新 articles 表
```

### 6.3 OutlineAgent 需要按类型生成结构

不同 article_type 的 outline 字段结构不同：
- `character_db` 的 outline 严格按 §3.3 的 JSON 模板
- `build` 的 outline 是 markdown headings 列表
- `tier_list` 的 outline 含 `tier` + `tier_rank` 字段供聚合渲染

**OutlineAgent prompt 要按 article_type 分支**。

### 6.4 site.config 的 diversity 强制约束

```json
"diversity": {
  "required_types": ["build","tier_list","boss_guide","reroll","character_db","weapon_db","news","faq"],
  "min_types_per_week": 5
}
```

KeywordSelectorAgent 选关键词时**必须看历史 7 天的 article_type 分布**，确保多样性。

---

## 7. Phase 1.A 实施分阶段

### 7.1 当前阶段（基础设施）

❌ 不做：站点 / 路由 / 模板（先跑数据层）

### 7.2 Pipeline 开发期

✅ 在 OutlineAgent / PublishAgent 中实现 `article_type` → URL 映射逻辑（CODE-SPEC §6.2）

### 7.3 Astro 站点搭建期（Phase 1.A 末期）

✅ 按本文档 §4 实现完整路由结构  
✅ 实现 5 个核心页面：首页 / Tier List / 角色页 / 文章页 / FAQ  
✅ 暂不做：Tools / Search / Banners 独立页（Phase 2）

### 7.4 Phase 2 升级（上线后）

✅ 加 Tools（Pity Tracker / Pull Simulator）  
✅ 建独立 banners 表 + 渲染  
✅ 加 Search（Algolia / 自建）

---

## 8. 验收标准

Phase 1.A 完成时，站点应满足：

- [ ] 用户进首页能看到"这是个攻略门户"，不是"博客"
- [ ] 至少 5 类页面：首页 / Tier List / 角色页 / 攻略文章 / FAQ
- [ ] 每篇 article_type='character_db' 的 article 在 `/characters/<slug>` 渲染为结构化页面（不是长文）
- [ ] FAQ / Tier List 是聚合页，不渲染独立 article URL
- [ ] 所有页面有 Header / Footer / 内链
- [ ] sitemap.xml + robots.txt + ads.txt 正确
- [ ] AdSense 占位符已就位（待审核通过后填实际代码）
- [ ] Lighthouse Performance ≥ 85

---

**文档结束**
