# 凭证安装指引（CREDENTIALS-SETUP）

> 配套文档：HUMAN-RUNBOOK.md（人工注册）+ CODE-SPEC.md §9（代码读取）
> 文档版本：v1.0
> 目的：把你已经收集到的凭证安全地配置到 GitHub Secrets，让 AI/代码能用而不接触原文
> 适用对象：你（运营者）按本文档操作 1-2 次即可

---

## 0. 重要安全原则

### ❌ 永远不要做的事
- 把凭证粘贴到 AI 聊天框（Claude / Cursor / GPT 都不行 — 会被记录）
- 把凭证 commit 到 GitHub（即使 private repo 也不行）
- 把凭证写到代码注释、README、文档
- 把凭证发到聊天工具 / 邮件正文

### ✅ 凭证只放两个地方
1. **GitHub Secrets**（运行时使用）
2. **本地密码管理器**（1Password / Bitwarden / iCloud Keychain，作为备份）

### ✅ AI 看到的内容
- 只看变量**名称**（如 `GEMINI_API_KEY`）
- 不看变量**值**
- 写代码时用 `os.getenv("GEMINI_API_KEY")` 读取
- GitHub Actions 跑时自动注入

---

## 1. 多站凭证隔离方案

### 1.1 核心规则

凭证分两类，命名规则不同：

```
全局共享 Secrets：
  ├── 所有站共用（API Key 通用资源）
  └── 直接命名：GEMINI_API_KEY, SUPABASE_URL, ...

每站专属 Secrets：
  ├── 每个站有独立的一组（FB 广告、GA4、AdSense）
  └── 命名格式：<SITE_SLUG_UPPER>_<RESOURCE>
      例如 NTECODEX_FB_ACCESS_TOKEN
```

### 1.2 站点 slug 命名规则

`site_slug` 是连接数据库 site 和 GitHub Secrets 前缀的纽带，定下后**永不修改**。

规则：
- 全小写 + 连字符（kebab-case）：`ntecodex` / `genshin-builds` / `wuwa-guide`
- 取自域名前缀（去 `.com` 等后缀）
- 在 GitHub Secrets 中变成大写 + 下划线：`NTECODEX` / `GENSHIN_BUILDS` / `WUWA_GUIDE`

首站：`ntecodex`（域名 ntecodex.com）

---

## 2. 配置 GitHub Secrets（按部就班）

### 2.1 准备工作

打开浏览器：
1. 进入 GitHub `traffic-ops-core` repo
2. **Settings** → **Secrets and variables** → **Actions**
3. 这是你接下来加 Secret 的唯一界面

每加一个 Secret：
- 点 **New repository secret**
- **Name** 填变量名（精确大小写）
- **Value** 填实际凭证（粘贴）
- 点 **Add secret**

### 2.2 全局共享 Secrets 清单（13 个）

按下表逐个添加。**Name 必须精确匹配**（拼写、大小写）。

| # | Name | Value 来自 | 备注 |
|---|------|-----------|------|
| 1 | `SUPABASE_URL` | Supabase Settings → API | 形如 `https://xxx.supabase.co` |
| 2 | `SUPABASE_SERVICE_ROLE_KEY` | Supabase Settings → API | service_role 那个，不是 anon |
| 3 | `GEMINI_API_KEY` | aistudio.google.com | 形如 `AIza...` |
| 4 | `SMTP_HOST` | 自填 | `smtp.gmail.com` 或 `smtp.sendgrid.net` |
| 5 | `SMTP_PORT` | 自填 | `587` |
| 6 | `SMTP_USER` | Gmail 邮箱 / SendGrid 用 `apikey` | |
| 7 | `SMTP_PASS` | Gmail App Password 或 SendGrid API Key | |
| 8 | `ALERT_RECIPIENT_EMAIL` | 你的告警接收邮箱 | |
| 9 | `CLOUDFLARE_API_TOKEN` | Cloudflare My Profile → API Tokens | |
| 10 | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Workers & Pages 页面右下 | 32 位 hex |
| 11 | `GITHUB_TOKEN` | GitHub Personal Access Token | 用于推 site repo |
| 12 | `GIT_USER_NAME` | 自填 | 推荐 `traffic-ops-bot` |
| 13 | `GIT_USER_EMAIL` | 自填 | 推荐 `bot@yourdomain.com` |

### 2.3 NTE 站专属 Secrets（6 个）

| # | Name | Value 来自 |
|---|------|-----------|
| 1 | `NTECODEX_GA4_PROPERTY_ID` | GA4 Property ID（纯数字）|
| 2 | `NTECODEX_FB_AD_ACCOUNT_ID` | FB 广告账户 ID，形如 `act_123456789` |
| 3 | `NTECODEX_FB_PIXEL_ID` | FB Pixel ID（纯数字）|
| 4 | `NTECODEX_FB_ACCESS_TOKEN` | FB System User Token，形如 `EAAxxx...` |
| 5 | `NTECODEX_ADSENSE_PUBLISHER_ID` | **过审后再填**，前期留空或填占位 `pending` |
| 6 | `NTECODEX_GOOGLE_SERVICE_ACCOUNT_JSON` | 整个 JSON 文件内容（粘贴时含 `{}`）|

> 💡 **关于 JSON 类 Secret**：把 JSON 文件用记事本打开，**Ctrl+A 全选 → 复制 → 粘贴到 Value 框**。GitHub 会原样保存。代码读取时再 `json.loads(os.getenv(...))`。GitHub Secrets 接受多行字符串。

### 2.4 验证清单

加完后，回到 Secrets 列表页，确认看到以下名字：

**全局**：
```
ALERT_RECIPIENT_EMAIL
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN
GEMINI_API_KEY
GITHUB_TOKEN
GIT_USER_EMAIL
GIT_USER_NAME
SMTP_HOST
SMTP_PASS
SMTP_PORT
SMTP_USER
SUPABASE_SERVICE_ROLE_KEY
SUPABASE_URL
```

**NTE 专属**：
```
NTECODEX_ADSENSE_PUBLISHER_ID    （可暂时填 'pending'）
NTECODEX_FB_ACCESS_TOKEN
NTECODEX_FB_AD_ACCOUNT_ID
NTECODEX_FB_PIXEL_ID
NTECODEX_GA4_PROPERTY_ID
NTECODEX_GOOGLE_SERVICE_ACCOUNT_JSON
```

总共 **19 个 Secrets**。GitHub Secrets 数量上限 100 个，足够 10+ 个站使用。

---

## 3. 给 AI（Claude Code 等）的安全交付方式

### 3.1 你需要给 AI 的内容

```
1. 三份核心文档：
   - PRD-AI-Site-Operator.md
   - CODE-SPEC.md  
   - HUMAN-RUNBOOK.md
   - CREDENTIALS-SETUP.md（本文件）

2. 完整的 Secrets 名单（只有变量名，没有值）：
   - 见 §2.2 和 §2.3

3. 当前任务说明：
   "按 CODE-SPEC.md 实施 Phase 1.A，从 §2 Schema 部署开始。
    凭证已配置在 GitHub Secrets，按 §9.3 的代码模式读取 site_slug 拼接环境变量。
    首站 site_slug = ntecodex。"
```

### 3.2 你不需要给 AI 的内容

❌ 任何 API Key 的真实值
❌ Supabase service_role_key
❌ Service Account JSON 内容
❌ FB Access Token

### 3.3 AI 写出的代码长这样

```python
# ✅ 正确：从环境变量读
import os
api_key = os.getenv("GEMINI_API_KEY")

# ✅ 正确：按 site_slug 拼接
site_slug = "ntecodex"  # 从数据库读
prefix = site_slug.upper()
fb_token = os.getenv(f"{prefix}_FB_ACCESS_TOKEN")

# ❌ 错误：硬编码
api_key = "AIzaSyB..."  # 绝对不能这样
```

如果你看到 AI 写的代码里有任何**真实的 Key 字符串**，立即让 AI 改正，否则 push 到 GitHub 会泄露。

### 3.4 .gitignore 必须包含

确保站点 repo 和 traffic-ops-core repo 的 `.gitignore` 含：

```gitignore
# Environment
.env
.env.local
.env.*.local

# Credentials
*-credentials.json
*-service-account.json
secrets/

# Common mistakes
**/credentials.json
**/api_keys.txt
```

---

## 4. 本地开发的凭证管理

如果你用 Claude Code 本地写代码 + 调试，需要在本地能跑代码（连 Supabase / 调 Gemini）。

### 4.1 创建本地 .env 文件

在 `traffic-ops-core` repo 根目录创建 `.env` 文件（**已在 .gitignore 中**）：

```
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
GEMINI_API_KEY=...
NTECODEX_FB_ACCESS_TOKEN=...
# ...所有 Secrets 同名变量
```

代码用 `python-dotenv` 加载：

```python
from dotenv import load_dotenv
load_dotenv()  # 自动读取根目录的 .env
```

### 4.2 验证 .env 不会被推上 GitHub

每次 commit 前：

```bash
git status              # 确认 .env 不在 changes 列表
git diff --cached       # 确认没有 Key 字符串
```

如果 .env 出现在 git status 里，说明 .gitignore 没生效，**立即检查**。

### 4.3 万一不小心 push 了凭证

**立即做（按顺序）**：

1. 第一时间到对应平台**revoke 所有泄露的 Key**：
   - Gemini → AI Studio → 删除并重建 Key
   - FB Token → Business Settings → System Users → 重新生成
   - Supabase → Settings → API → Reset service_role
   - 等等
2. 用新 Key 更新 GitHub Secrets 和本地 .env
3. 历史 commit 中的 Key 即使删了也能在 git history 找到，但 Key 已 revoke 就无害了

**别试图 force push 改写历史** — 容易出错，新 Key 才是关键。

---

## 5. 加新站时的操作

未来你的第二个站（假设 site_slug = `wuwa-guide`）：

```
1. 进 GitHub Secrets → New
2. 添加 6 个新 Secret，前缀 WUWA_GUIDE_：
   - WUWA_GUIDE_GA4_PROPERTY_ID
   - WUWA_GUIDE_FB_AD_ACCOUNT_ID
   - WUWA_GUIDE_FB_PIXEL_ID
   - WUWA_GUIDE_FB_ACCESS_TOKEN
   - WUWA_GUIDE_ADSENSE_PUBLISHER_ID
   - WUWA_GUIDE_GOOGLE_SERVICE_ACCOUNT_JSON
3. 在 Supabase sites 表 insert 一行，config.site_slug = 'wuwa-guide'
4. 全局共享 Secrets 不需要新增
5. 代码不需要改

完工。
```

---

## 6. 快速自检表

完成 GitHub Secrets 配置后，对照以下打勾：

### 全局共享
- [ ] SUPABASE_URL
- [ ] SUPABASE_SERVICE_ROLE_KEY
- [ ] GEMINI_API_KEY
- [ ] SMTP_HOST / PORT / USER / PASS / ALERT_RECIPIENT_EMAIL
- [ ] CLOUDFLARE_API_TOKEN / ACCOUNT_ID
- [ ] GITHUB_TOKEN / GIT_USER_NAME / GIT_USER_EMAIL

### NTE 站专属
- [ ] NTECODEX_GA4_PROPERTY_ID
- [ ] NTECODEX_FB_AD_ACCOUNT_ID
- [ ] NTECODEX_FB_PIXEL_ID
- [ ] NTECODEX_FB_ACCESS_TOKEN
- [ ] NTECODEX_ADSENSE_PUBLISHER_ID（可填 `pending`）
- [ ] NTECODEX_GOOGLE_SERVICE_ACCOUNT_JSON

### 本地（如果用 Claude Code）
- [ ] `.env` 文件在 traffic-ops-core 根目录
- [ ] `.gitignore` 含 `.env`
- [ ] 测试：`git status` 不显示 `.env`

### 安全
- [ ] 所有原始凭证已备份到密码管理器（1Password / Bitwarden）
- [ ] 临时记录凭证的笔记/文档**已删除**
- [ ] 没有把凭证发到任何聊天工具

完成上述全部 → 告诉 AI："凭证已就绪，按 CODE-SPEC §2 启动 Schema 部署"。

---

**文档结束**
