# 人员操作指引（HUMAN-RUNBOOK）

> 配套文档：PRD-AI-Site-Operator.md v0.9 + CODE-SPEC.md v1.3 + CREDENTIALS-SETUP.md v1.0
> 文档版本：v1.3
> 目的：你（运营者）需要亲自完成的所有一次性人工操作清单
> 阅读方式：**按顺序执行**，每步打勾 ✅。中途不要跳步，否则后面会卡住。
>
> **变更记录**：
> - v1.3 (2026-05)：移除 Anthropic Claude（改 Gemini 统一）；凭证管理拆出 CREDENTIALS-SETUP.md
> - v1.2 (2026-05)：首站从 PBZ 改为 NTE；钉钉换 Email；Supabase 区域改美国
> - v1.1 (2026-05)：图像改 Gemini API
> - v1.0 (2026-05)：初版

---

## 0. 在开始之前

### 0.1 你需要准备什么

| 项目 | 状态 | 备注 |
|------|------|------|
| 一张可在境外消费的信用卡（Visa / Master） | 必备 | FB Ads、Gemini、域名等都要 |
| 一个能稳定访问海外的网络环境 | 必备 | FB Ads、Cloudflare 后台需要 |
| 一个独立邮箱（推荐 Gmail） | 必备 | 项目专用，用于注册服务 + 接收紧急告警，便于隔离 |
| 大约 2-3 天的整段时间做账户开通 | 必备 | 有些审核需要等几小时到几天 |

### 0.2 你需要的预算

| 项目 | 金额 | 时机 |
|------|------|------|
| 域名 | $10-15 | 立即 |
| FB Ads 测试 | $200 | Phase 2（约 4 周后）|
| Gemini API（图像） | 免费/按需 | 免费 500 张/天，超出 $0.039/张 |
| LLM API（Gemini 文本+图像统一） | 免费/按需 | 免费 Tier 充裕，单站超 1500/天才收费 |
| 其他基础设施 | $0 | Cloudflare、Supabase、GitHub 都用免费额度 |

> 💡 Gemini Nano Banana 免费 Tier 每天 500 张，对于单站每天 3 篇 × 3 张 = 9 张，**免费 Tier 完全够用**。
> Phase 3 复制到多站后才可能超额。

### 0.3 整体时间预期

| 阶段 | 你的人工操作时间 | 系统/代码工作时间 |
|------|--------------|---------------|
| 账户开通 | 0.5-1 天 | — |
| 域名 + DNS | 1-2 小时 | — |
| Cloudflare 配置 | 1 小时 | — |
| 数据源（GA/GSC/AdSense/FB）开通 | 2-3 小时 | — |
| Schema + 代码部署 | 0 | 由 AI/工程师完成 1-2 周 |
| **每天** 看日报 | **30 分钟/天** | — |

---

## 1. 账户开通清单（建议一次性做完）

### 1.1 注册项目专用邮箱
- [ ] 注册一个新 Gmail 账号，专用于这个项目
- [ ] 用这个邮箱注册下面所有服务，不要用现有混用账号

> 💡 为什么独立邮箱：FB Ads 一旦封号会牵连同邮箱注册的关联账号。隔离能保护你其他业务。

### 1.2 GitHub
- [ ] 注册或登录 GitHub 账号
- [ ] 启用 2FA（双因素认证），否则后面 Cloudflare 集成有风险
- [ ] 升级到 Pro 不必要，免费版够用

### 1.3 Cloudflare
- [ ] cloudflare.com 注册账号（用项目邮箱）
- [ ] 启用 2FA

### 1.4 Supabase
- [ ] supabase.com 注册（用 GitHub OAuth 登录最方便）
- [ ] 暂不创建项目，等 schema 准备好再创建（避免免费 Tier 闲置浪费）

### 1.5 LLM 选择说明（不需要单独操作，Gemini 全包了）
- [x] 项目用 **Gemini API 一个 Key 同时承担**：文本写作 + 质检 + 图像生成
- [x] **不再需要 Anthropic Claude API**（你用 Claude Code 在本地写代码、push 到 GitHub，不在生产 Pipeline 里调）
- [x] **不再需要 OpenAI / Replicate**（MVP 阶段）
- [x] 模型版本可在前端 Settings 页随时切换（无需改代码 + 重新部署）

### 1.6 Gemini API（必做）
- [ ] aistudio.google.com 用 Google 账号登录
- [ ] 点 "Get API key" → "Create API key in new project"
- [ ] 选刚才在 Google Cloud Console 创建的 `traffic-ops` 项目（**复用，不要新开**）
- [ ] **复制 API Key**
- [ ] 免费 Tier 配额（按模型不同）：
  - Gemini 3 Flash：1500 次请求/天
  - Gemini 3.1 Pro：50 次请求/天（**重要：质检 Agent 用这个，要监控**）
  - Gemini 2.5 Flash Image：500 张/天
- [ ] 超额后会自动转为付费
- [ ] **储值预警**：进 Cloud Console → Billing → 设置预算告警 $20

> 💡 关于模型选择：当前最新 Gemini 模型清单（2026/5）见 CODE-SPEC §2.2.13 model_catalog 初始化数据。
> 模型在前端 Settings 页可切换，**不需要改代码**。当 Google 出 4.0 时，你只需在前端
> /models 页加一行配置即可。

### 1.7 Replicate（图像生成 - 备选，可不开）
- [ ] **MVP 阶段不强制**，主用 Gemini
- [ ] 如需对比测试或备份方案再注册：replicate.com → GitHub 登录 → 绑卡

### 1.8 紧急告警邮件（SMTP 配置）
项目用 Email 推送 🔴 紧急告警，**仅紧急级别**，避免轰炸。日常数据看前端 Dashboard。

**两条路径选一条**：

**路径 A：Gmail App Password（最简单，免费）**
- [ ] 用项目 Gmail 账号
- [ ] Google 账号 → Security → 启用 **2-Step Verification**
- [ ] Security → **App Passwords** → Generate（应用名填 "Traffic Ops"）
- [ ] 复制 16 位 App Password（**只显示一次**）
- [ ] SMTP 配置：
  - SMTP_HOST = `smtp.gmail.com`
  - SMTP_PORT = `587`
  - SMTP_USER = 你的 Gmail
  - SMTP_PASS = App Password（不是你的登录密码）

**路径 B：SendGrid（更稳定，免费 100 封/天）**
- [ ] sendgrid.com 注册，验证邮箱
- [ ] Settings → API Keys → Create API Key (Full Access)
- [ ] 复制 API Key
- [ ] SMTP 配置：
  - SMTP_HOST = `smtp.sendgrid.net`
  - SMTP_PORT = `587`
  - SMTP_USER = `apikey`（字面值就是 "apikey"）
  - SMTP_PASS = 你的 SendGrid API Key

**两条路径都要做**：
- [ ] 在手机邮件 App 给该收件邮箱**开启锁屏推送通知**（这是 Email 替代 IM 的关键，不开你看不到告警）
- [ ] 把 SMTP 凭据加入 GitHub Secrets（先记录到 §8 的凭据汇总）

### 1.9 Facebook Business
- [ ] business.facebook.com 创建 Business Manager
- [ ] **重要**：用一个真实的 FB 个人账号管理（年龄足够、有头像、有几个好友的"成熟"账号），新注册的小号容易被风控
- [ ] 创建一个独立的"广告账户"（不要用个人账户跑商业广告）
- [ ] 绑定信用卡

> ⚠️ FB Ads 风控注意：
> - 新建账户最好"温和起步"：第一周不要每天花超过 $20
> - 不要在多个 FB 账号之间切换 IP
> - 新 Pixel 安装后 1-3 天 FB 算法会判断是否真实

---

## 2. 域名注册（你已有 GoDaddy 账号）

### 2.1 选定域名

按推荐度排序，**避开 neverness 开头的，规避商标**：

| 候选 | 推荐度 | 理由 |
|------|------|------|
| `ntecodex.com` | ⭐⭐⭐⭐⭐ | "Codex" 是攻略站常见词，权威感 |
| `nteguide.com` | ⭐⭐⭐⭐ | 直接，但常见 |
| `nteversebuilds.com` | ⭐⭐⭐⭐ | 聚焦 Build 内容 |
| `nehub.gg` | ⭐⭐⭐ | gg 域名贵但游戏感强 |

- [ ] 在 GoDaddy 检索可用性，购买域名
- [ ] 选 1 年即可（验证模式不通就不续费）
- [ ] **不要购买 GoDaddy 推销的附加服务**（隐私保护已包含、托管、邮箱、SSL 都不需要 GoDaddy 提供）
- [ ] 完成购买，记下域名

### 2.2 把 DNS 转到 Cloudflare（必做）

**为什么**：GoDaddy DNS 慢、续费贵；Cloudflare DNS 免费、快、是后续 Pages 部署的前置。

**步骤**：

- [ ] 登录 Cloudflare → "Add a Site" → 输入域名
- [ ] 选 Free 计划
- [ ] Cloudflare 会扫描现有 DNS 记录，确认无需修改
- [ ] Cloudflare 给你两个 NS（Nameserver）地址，类似：
  ```
  brad.ns.cloudflare.com
  paige.ns.cloudflare.com
  ```
- [ ] 登录 GoDaddy → 我的域名 → 你的域名 → DNS → Nameservers → 改为自定义
- [ ] 填入 Cloudflare 给的两个 NS 地址，保存
- [ ] **DNS 生效需要 1-24 小时**，期间不影响开通其他服务
- [ ] Cloudflare 那边会自动检测，生效后状态变成 "Active"

### 2.3 Cloudflare 安全设置（生效后做）

- [ ] SSL/TLS → Overview → 模式选 **Full (strict)**
- [ ] SSL/TLS → Edge Certificates → 启用 **Always Use HTTPS**
- [ ] Security → Bots → 启用 **Bot Fight Mode**（免费版）
- [ ] Speed → Optimization → 启用 **Auto Minify** (HTML/CSS/JS)

---

## 3. GitHub 仓库准备

你需要 3 个 repo（**全部设为 Private**）：

### 3.1 创建 traffic-ops-core
- [ ] 在 GitHub 新建 repo，名字：`traffic-ops-core`
- [ ] 私有
- [ ] 不要 init README（工程师会推第一个 commit）

### 3.2 创建 traffic-ops-dashboard
- [ ] 同上，名字：`traffic-ops-dashboard`

### 3.3 创建 ntecodex-site（按你的域名命名）
- [ ] 同上，名字：`ntecodex-site`（替换为你的实际域名前缀）

### 3.4 给三个 repo 准备一个共享的 GitHub Token（用于 Pipeline 推送内容到 site repo）
- [ ] GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate
- [ ] Token 名字：`traffic-ops-bot`
- [ ] 过期时间：1 年
- [ ] Repository access：选 `ntecodex-site`
- [ ] Permissions：Contents (Read and write), Metadata (Read)
- [ ] 生成后**立即复制保存**（关闭后看不到）

---

## 4. Cloudflare Pages 配置

### 4.1 创建 site 项目（ntecodex.com）
- [ ] Cloudflare Dashboard → Workers & Pages → Create → Pages → Connect to Git
- [ ] 授权 Cloudflare 访问 GitHub（首次需要）
- [ ] 选择 `ntecodex-site` repo
- [ ] Build command：`npm run build`
- [ ] Output directory：`dist`
- [ ] Environment：Production
- [ ] 暂时不会有内容可 build，先创建项目占位

### 4.2 绑定自定义域名
- [ ] Pages 项目 → Custom domains → Set up custom domain
- [ ] 输入 `ntecodex.com`（替换为你的域名）
- [ ] Cloudflare 自动添加 DNS 记录

### 4.3 创建 dashboard 项目
- [ ] Workers & Pages → 再创建一个 Pages 项目
- [ ] 名字：`traffic-ops-dashboard`
- [ ] 连接 `traffic-ops-dashboard` repo
- [ ] Build command：`npm run build`
- [ ] Output directory：`.next`（或 `out`，看 Next.js 配置）
- [ ] 子域名：用 `admin.ntecodex.com`（不需要再买域名）

---

## 5. Google 服务接入

### 5.1 Google Analytics 4
- [ ] analytics.google.com 创建账户（用项目邮箱）
- [ ] 创建 Property：`ntecodex.com`
- [ ] Property → Data Streams → 添加 Web Stream
- [ ] 输入 `https://ntecodex.com`
- [ ] **复制 Measurement ID（G-XXXXXXXXXX）**，发给工程师
- [ ] 暂不安装跟踪代码，等 Astro 站点上线后通过环境变量注入

### 5.2 Google Search Console
- [ ] search.google.com/search-console 添加 Property
- [ ] 选 **Domain** 类型（不是 URL prefix）
- [ ] 用 **DNS 验证**：Google 给你一段 TXT 记录，添加到 Cloudflare DNS
  - Cloudflare → DNS → Records → Add → Type: TXT, Name: @, Content: `google-site-verification=...`
- [ ] 等几分钟，回到 Search Console 点 Verify
- [ ] 验证通过后，**所有子域名/HTTP/HTTPS 自动覆盖**（这是 Domain 模式的好处）

### 5.3 创建服务账号（GA4 + GSC + AdSense 共用）

这一步是给后端代码自动调 API 用的。

- [ ] 打开 console.cloud.google.com（用项目邮箱登录）
- [ ] 创建新项目：`traffic-ops`
- [ ] 启用 API：
  - Google Analytics Data API
  - Google Search Console API
  - AdSense Management API
- [ ] APIs & Services → Credentials → Create Credentials → Service Account
- [ ] 名字：`traffic-ops-collector`
- [ ] 创建后，点击该服务账号 → Keys → Add Key → Create new key → JSON
- [ ] **下载 JSON 文件，妥善保存**（这是凭据）

### 5.4 给服务账号授权
- [ ] **GA4**：analytics.google.com → Admin → Property Access Management → 添加服务账号邮箱（在 JSON 文件里有，类似 `traffic-ops-collector@traffic-ops.iam.gserviceaccount.com`）→ 角色 Viewer
- [ ] **GSC**：search.google.com/search-console → 你的 Property → Settings → Users and permissions → 添加服务账号邮箱 → 权限 Owner（注意 Owner，不是 Restricted）
- [ ] **AdSense**：等 AdSense 过审后再做（见 §6）

### 5.5 Google AdSense
- [ ] **暂不申请**，等内容铺到 30+ 篇后再申请
- [ ] 在此之前先创建 AdSense 账号占位：adsense.google.com → Sign up
- [ ] 国家选你的实际所在地（这影响打款方式）
- [ ] **不要立即提交审核**，等内容准备好

---

## 6. Facebook Business 详细配置

### 6.1 创建广告账户
- [ ] Business Settings → Accounts → Ad Accounts → Create
- [ ] 时区选 GMT+8（或你的时区）
- [ ] 货币选 USD（避免汇率麻烦）
- [ ] 绑定支付方式

### 6.2 创建 Pixel
- [ ] Business Settings → Data Sources → Pixels → Add
- [ ] 名字：`ntecodex-pixel`
- [ ] 关联到上面的广告账户
- [ ] **复制 Pixel ID**

### 6.3 创建 System User（后端 API 用）
- [ ] Business Settings → Users → System Users → Add
- [ ] 名字：`traffic-ops-bot`
- [ ] 角色：Admin
- [ ] 创建后，点击 → Generate New Token
- [ ] 应用：选你的 Business（如果没有 App，创建一个 App）
- [ ] 权限：勾选 `ads_read`, `ads_management`
- [ ] Token 有效期：60 days 或 Never expire（推荐 Never）
- [ ] **复制 Token**

> ⚠️ FB Token 容易过期或失效。Phase 1 部署时如发现 Token 失效，回到这一步重新生成。

---

## 7. Email 告警自测（部署前必做）

§1.8 已经配置了 SMTP 凭据。这一步是**手动测试一次**，确保能真的收到邮件 + 锁屏推送。

### 7.1 用本机 Python 跑一次测试发送

如果你不会 Python，让 AI/工程师在 Phase 1.A 联调时帮你测。如果会，复制下面代码：

```python
import smtplib
from email.mime.text import MIMEText

# 替换为你的实际值
SMTP_HOST = "smtp.gmail.com"          # 或 smtp.sendgrid.net
SMTP_PORT = 587
SMTP_USER = "your-project@gmail.com"  # 或 "apikey" (SendGrid)
SMTP_PASS = "xxxx-xxxx-xxxx-xxxx"     # App Password 或 API Key
RECIPIENT = "your-project@gmail.com"  # 收件邮箱（可以和 USER 相同）

msg = MIMEText("This is a test alert from Traffic Ops.")
msg["Subject"] = "[Traffic Ops Alert] TEST - email pipeline check"
msg["From"] = SMTP_USER
msg["To"] = RECIPIENT

with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
    server.starttls()
    server.login(SMTP_USER, SMTP_PASS)
    server.send_message(msg)

print("Sent!")
```

### 7.2 自检要点
- [ ] 收件箱真的收到了邮件
- [ ] 邮件没有进垃圾箱（Gmail 大概率会，把它标记为"非垃圾邮件"）
- [ ] **手机上有锁屏推送弹窗**（最重要！没有的话回 §1.8 调推送权限）
- [ ] 邮件主题前缀正确：`[Traffic Ops Alert]`

---

## 8. 凭据汇总（请按 CREDENTIALS-SETUP.md 配置 GitHub Secrets）

> **重要**：凭证管理已拆出独立文档 `CREDENTIALS-SETUP.md`，含完整的：
> - 多站凭证隔离方案（site_slug 前缀法）
> - GitHub Secrets 一项项配置清单
> - 给 AI 的安全交付方式
> - 本地 .env 配置 + 安全自检
>
> **本节只列出"哪些凭证需要"，配置步骤请打开 CREDENTIALS-SETUP.md**。

### 8.1 你应该已经收集到了以下凭证（暂时存在密码管理器/加密笔记）

```
# === 全局共享 ===
SUPABASE_URL                        ← Supabase 项目创建后获取
SUPABASE_SERVICE_ROLE_KEY           ← 同上
GEMINI_API_KEY                      ← AI Studio
SMTP_HOST / PORT / USER / PASS      ← Gmail App Password 或 SendGrid
ALERT_RECIPIENT_EMAIL               ← 你自己的邮箱
CLOUDFLARE_API_TOKEN                ← Cloudflare My Profile
CLOUDFLARE_ACCOUNT_ID               ← Cloudflare Pages 页面右下
GITHUB_TOKEN                        ← GitHub Personal Access Token

# === NTE 站专属（site_slug = ntecodex）===
NTECODEX_GA4_PROPERTY_ID            ← GA4 Property
NTECODEX_FB_AD_ACCOUNT_ID           ← FB Business
NTECODEX_FB_PIXEL_ID                ← FB Pixel
NTECODEX_FB_ACCESS_TOKEN            ← FB System User Token
NTECODEX_ADSENSE_PUBLISHER_ID       ← AdSense（过审后才有，前期填 'pending'）
NTECODEX_GOOGLE_SERVICE_ACCOUNT_JSON ← Google Cloud 服务账号 JSON 全文
```

### 8.2 下一步

打开 **CREDENTIALS-SETUP.md**，按 §2 一项项把上述凭证配置到 GitHub Secrets。配完即可让 AI 启动开发，AI 不会接触任何真实 Key。

---

## 9. Cloudflare API Token 创建

代码需要这个 Token 来：
- 部署 Pages（虽然 Pages 自动 deploy，但有时 API 触发更可靠）
- 查询 Cloudflare Analytics

- [ ] Cloudflare → My Profile → API Tokens → Create Token
- [ ] 选 "Custom Token"
- [ ] 名字：`traffic-ops`
- [ ] Permissions：
  - Account → Cloudflare Pages → Edit
  - Zone → Analytics → Read
  - Zone → DNS → Edit
- [ ] Account Resources：Include All accounts
- [ ] Zone Resources：Include All zones
- [ ] 创建后 **立即复制 Token**

---

## 10. Supabase 项目创建（等 Schema 准备好再做）

> 这一步**等工程师告诉你 schema 准备好**再做，不要提前创建。

- [ ] supabase.com → New Project
- [ ] Organization：默认
- [ ] Project Name：`traffic-ops`
- [ ] Database Password：**生成强密码并妥善保存**（之后改密码很麻烦）
- [ ] Region：**US East (N. Virginia)** — 与 GitHub Actions 同区，Pipeline 访问最快（你访问 Dashboard 跨洋，但只读查询影响小）
- [ ] 等待项目创建（约 2 分钟）
- [ ] 项目首页 → Settings → API
  - 复制 `URL`（类似 `https://xxxx.supabase.co`）
  - 复制 `anon public` key
  - 复制 `service_role` key（**这个最敏感，千万别公开**）
- [ ] 把 3 个值添加到凭据清单 §8

### 10.1 跑 Schema migration
- [ ] 工程师把 SQL 文件准备好后，进 Supabase → SQL Editor → New query
- [ ] 粘贴 SQL，运行
- [ ] 检查 Table Editor 里出现了所有表

### 10.2 配置 Authentication
- [ ] Authentication → Providers → Email → 启用
- [ ] Authentication → URL Configuration → Site URL：`https://admin.ntecodex.com`
- [ ] Authentication → URL Configuration → Redirect URLs：加入 `https://admin.ntecodex.com/**`
- [ ] Authentication → Users → Invite User → 输入你的邮箱，发邀请

### 10.3 创建第一个 site 记录
- [ ] Table Editor → sites → Insert row
- [ ] domain: `ntecodex.com`
- [ ] site_name: `NTE Codex`
- [ ] owner_id: 选你刚刚邀请的用户
- [ ] config: 粘贴 site.config.yaml 的关键字段

---

## 11. AdSense 申请（Phase 1.B 阶段）

> ⚠️ **不要太早申请**。AdSense 审核员会看你的站，如果只有 5-10 篇内容会被拒，且**重新申请有冷却期**。

> 📌 **三阶段节奏**（PRD §8 已定义）：
> - **Phase 1.A**（Week 1-3）：搭系统 + 铺内容到 30 篇 → **不投广告**
> - **Phase 1.B**（Week 4-6）：申请 AdSense + 继续铺内容 → **不投广告**
> - **Phase 1.C**（Week 6-9）：AdSense 过审 → 才开始 FB Ads
> 
> **关键**：审核期间不投广告 = 不烧钱在没变现的站上。

### 11.1 申请前自查（Phase 1.A 末期）
- [ ] 站点已上线 ≥ 30 篇内容
- [ ] 至少覆盖 5 种文章类型（build / tier_list / boss_guide / reroll / faq 等）
- [ ] 有完整的 4 个合规页面：
  - About（关于，含虚构但合理的"编辑团队"人设）
  - Privacy Policy（隐私政策）
  - Terms of Service（服务条款）
  - Contact（联系方式，至少有邮箱）
- [ ] 站点 Lighthouse SEO 评分 ≥ 90
- [ ] 每篇文章至少有 2-3 张图

### 11.2 提交申请（Phase 1.B 开始）
- [ ] adsense.google.com → 添加站点 → 输入域名
- [ ] 复制 AdSense 给的 `<script>` 代码
- [ ] **告诉工程师**把代码加到 `<head>` 里
- [ ] 在 site repo 创建 `public/ads.txt`，内容：
  ```
  google.com, pub-XXXXXXXXXXXXXXXX, DIRECT, f08c47fec0942fa0
  ```
  （pub-XXX 替换为你的 Publisher ID）
- [ ] 部署后回到 AdSense 点"我已添加代码"
- [ ] 等待审核（通常 1-14 天）

### 11.3 等待期间（Phase 1.B 持续 1-3 周）
- [ ] 内容继续以每天 3 篇的节奏产出
- [ ] 目标累计 70+ 篇
- [ ] 关注 Search Console 是否开始有 impressions（是 SEO 起势的信号）
- [ ] **不要投 FB Ads**（变现还没接通，纯亏）

### 11.4 过审后（Phase 1.C 启动）
- [ ] 在 Dashboard Settings 页把 `adsense.approved: true`
- [ ] 让工程师启用广告位渲染
- [ ] 给 AdSense 服务账号授权（在 AdSense 后台 → 用户管理 → 添加服务账号邮箱）
- [ ] **现在才开始 FB Ads 投放**（参见 PRD §12.2 预算 + Phase 1.C 描述）

---

## 12. 日常运营 SOP（每天 30 分钟）

部署完成后，你的日常工作非常轻：

### 12.1 早上（建议 30 分钟内）
- [ ] **打开 Dashboard `/`（Mission Control）**：这是每天的固定起点
- [ ] 看 Mission Control 上的：
  - 🚦 项目健康度（🟢 / 🟡 / 🔴）
  - 14 天累计 ROI（含目标比对）
  - SEO 自然流量周环比（FB Ads 真正 KPI）
  - 当日 AI 解读 + 待办
- [ ] **如收到 🔴 紧急告警 Email**（手机锁屏推送），立即处理（参见 §13）

### 12.2 当日决策动作
Mission Control 和日报里的"建议"都是系统输出，你需要做的是点确认/执行：

| 系统建议 | 你的动作 |
|---------|--------|
| SUGGEST_PAUSE 某广告组 | 登录 FB Ads Manager 暂停 |
| SUGGEST_INCREASE_BUDGET_50% | 登录 FB Ads Manager 改预算 |
| SUGGEST_REPLACE_CREATIVES | **新做素材**（这是你最大的工作）|
| SUGGEST_REVIEW_DIRECTION | 暂停所有广告，找时间复盘 |
| SUGGEST_SCALE_UP | 加预算 + 复制广告组到新地区 |

### 12.3 每周一次（周日晚 1 小时）
- [ ] 看 Dashboard 的指标趋势页（7 天对比）
- [ ] 看 Agent 日志页，挑 2-3 篇 AI 写的文章读一下
- [ ] 评估是否要调整 site.config.yaml（如调字数、调发文频率）
- [ ] 准备下周的素材方向

---

## 13. 紧急情况处理

### 🔴 AdSense 无效流量 > 10%
**含义**：可能被刷流量或 bot 攻击，再下去 AdSense 会限广告或封号。

立即动作：
- [ ] 暂停所有 FB Ads（先止血）
- [ ] 登录 Cloudflare → Security → 调高 Bot Fight 等级
- [ ] 检查 GA4 流量来源是否异常（同 IP、同 referrer 大量涌入）
- [ ] 联系工程师调研

### 🔴 AdSense CTR > 5%
**含义**：异常高，AdSense 可能怀疑作弊。

立即动作：
- [ ] **不要点击自己网站的广告**（如果你最近有点过，立即停止）
- [ ] 检查广告位是否设计成误点（误把广告当成内容）
- [ ] 如果是误点设计，立即让工程师调整广告位置

### 🔴 站点 5xx 错误持续
立即动作：
- [ ] 登录 Cloudflare 看是不是被攻击
- [ ] 临时启用 Cloudflare "I'm Under Attack" 模式
- [ ] 联系工程师

### 🟡 14 天累计亏损接近 $200
立即动作：
- [ ] **接受现实**，按预定规则执行
- [ ] 暂停所有 FB Ads
- [ ] 进入复盘：内容方向？素材？落地页？
- [ ] 决定是 Pivot 还是放弃

---

## 14. AdSense 风控自我保护清单

这部分必须**严格遵守**，违反任何一条都可能导致永久封号：

- [ ] ❌ 永远不要点自己的广告（包括家人、办公室同事电脑）
- [ ] ❌ 永远不要在群里让别人帮你点
- [ ] ❌ 永远不要刷流量或买流量到 AdSense 站（FB Ads 是合规的，但低质流量来源不行）
- [ ] ❌ 不要在邮件、社交媒体发"看我的广告 → 链接"的引导
- [ ] ✅ 测试网站时用无痕模式 + 屏蔽广告（Adblock）
- [ ] ✅ 监控无效流量比例，超 10% 立即处理
- [ ] ✅ 不同站用不同 GA Property + 不同 AdSense Publisher 关联
- [ ] ✅ 每月检查 AdSense 后台是否有警告

---

## 15. 完成度自检表

打勾你完成了的：

### Phase 1.1 - 账户与基础（预计 1 天）
- [ ] §1.1 项目专用邮箱
- [ ] §1.2 GitHub + 2FA
- [ ] §1.3 Cloudflare + 2FA
- [ ] §1.4 Supabase 注册
- [x] §1.5 LLM 说明（无需操作，Gemini 一站式）
- [ ] §1.6 Gemini API Key
- [ ] §1.7 Replicate（可选）
- [ ] §1.8 SMTP 邮件告警凭据（Gmail App Password 或 SendGrid）
- [ ] §1.9 FB Business Manager

### Phase 1.2 - 域名与 DNS（预计 半天 + 24h 等待）
- [ ] §2.1 域名注册
- [ ] §2.2 DNS 改 Cloudflare
- [ ] §2.3 Cloudflare 安全设置

### Phase 1.3 - 仓库与部署位（预计 2 小时）
- [ ] §3 三个 GitHub repo
- [ ] §3.4 GitHub Token
- [ ] §4 Cloudflare Pages 两个项目

### Phase 1.4 - 数据源开通（预计 3 小时）
- [ ] §5.1 GA4 创建
- [ ] §5.2 GSC 验证
- [ ] §5.3-5.4 服务账号 + 授权
- [ ] §6 FB Pixel + System User Token

### Phase 1.5 - 通知（预计 30 分钟）
- [ ] §7 Email 告警自测（手机能收到锁屏推送）

### Phase 1.6 - 凭据汇总（预计 30 分钟）
- [ ] §8 全部凭据汇总
- [ ] §9 Cloudflare API Token

### Phase 1.7 - 等工程师交付后
- [ ] §10 Supabase 创建 + Schema migration
- [ ] §10.3 第一个 site 记录

### Phase 2 - 内容铺到 30 篇后
- [ ] §11 AdSense 申请

---

## 附录：常见问题 FAQ

**Q：DNS 转到 Cloudflare 后，原来的邮箱（如 GoDaddy 邮箱）会受影响吗？**
A：会。如果你用了 GoDaddy 邮箱，转 DNS 前先把 MX 记录复制下来，转完后在 Cloudflare 加上。但建议这种项目用 Gmail，不要用域名邮箱。

**Q：FB Ads 一直被拒怎么办？**
A：常见原因 1）账号太新（养 1-2 周）2）支付方式问题 3）广告内容违反政策。先用低预算 $5/天测试一周让账户"活起来"。

**Q：Supabase 免费版会不会突然收费？**
A：不会自动升级。Free Tier 用满了会暂停，不会扣款。月活/存储超限会发邮件提醒。

**Q：我能不能跳过 Schema 部署，先做内容？**
A：不能。前端、Pipeline、数据采集都依赖 Supabase。Schema 是地基。

**Q：Gemini API 一个月会花多少钱？**
A：按当前规划（每天 3 篇 × Outline + Writing + QA + 配图 × 平均 5K input + 3K output），单站约 **$0-15/月**：
- Outline + Writing 用 Gemini 3 Flash（$0.30/M input + $2.50/M output）≈ $5-10/月
- QA 用 Gemini 3.1 Pro（更贵）≈ 每天 3 次免费 Tier 内
- 图像用 Gemini 2.5 Flash Image，每天 9 张 << 500 张免费 Tier
**MVP 阶段大概率全在免费 Tier 内**。多站复制后按比例增长，一直可控。

**Q：FB 广告投放期间，我能不能旅行？**
A：能，但日报必看。如果连续 2 天不看，遇到亏损扩大会错过止损点。

---

**文档结束**

完成本文档所有打勾项后，告知工程师/AI："人工准备就绪"，进入 Schema 部署 + Pipeline 开发阶段。
