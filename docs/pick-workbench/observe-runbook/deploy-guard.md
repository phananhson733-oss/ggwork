# 部署守卫（TR-34）

脚本：`scripts/pick-deploy-guard.py`。计划 D41、第 10 节（S0–S13 与回滚规则）、D5；设计 3.8。测试：`customizations/pick-workbench/tests/test_deploy_guard.py`（假 git、假库）与 `test_deploy_guard_real.py`（真 git 仓库、真 PostgreSQL）。

## 用途

gateway、两个观测 cron、前端，**每次部署之前**都先跑守卫，两个会话都一样（U16 转告另一会话）。守卫只做核对，**自己不部署**：不执行 `railway`，也不执行 `vercel`。全部核对通过后，它打印要部署的提交、要追加进 `docs/pick-workbench/progress.md` 的一行，以及下一步命令。

它要拦住的是：从脏工作区或旧检出部署、带着 `.env*` 部署、部署一份认不出生产迁移头的代码（库已到 0007，镜像却不认识 0007：扩展加载失败，而 `/health/ready` 照样通过，故障是静默的）、从生产已经越过的提交部署（等于回滚）。

## 用法

在要部署的检出里，用后端环境的 Python 执行（DSN 读取要用它的 psycopg）：

```sh
backend/.venv/bin/python scripts/pick-deploy-guard.py gateway
backend/.venv/bin/python scripts/pick-deploy-guard.py cron trends
backend/.venv/bin/python scripts/pick-deploy-guard.py cron gsc
backend/.venv/bin/python scripts/pick-deploy-guard.py frontend [--out 新目录]
```

| 选项 | 含义 |
|---|---|
| `--remote` | 部署来源的远端，默认 `ggwork`。本仓库的 `origin` 是上游 DeerFlow，ggwork 的 main 在 `ggwork/main`；在一个 origin 就是 ggwork 的克隆里，写 `--remote origin` |
| `--repo` | 要核对的检出，默认脚本所在的检出。用子目录也行，守卫会找到检出根目录 |
| `--first-record` | 只在 progress.md 里还没有这个目标的守卫记录时用（第一次经守卫部署它）。已有记录时带它会被拒绝，所以不能用它绕过祖先检查 |
| `--out`（只有 frontend） | 导出到这个目录。它须不存在，守卫新建它，权限 700。不写时在系统临时目录里新建 `pick-frontend-<提交前 12 位>-*` |

**推荐的干净检出**：不要在日常开发的工作区里部署。在主仓库执行 `git fetch ggwork main && git worktree add --detach <部署目录> ggwork/main`，再用主目录的 Python 跑那个检出里的脚本：`<主目录>/backend/.venv/bin/python <部署目录>/scripts/pick-deploy-guard.py gateway`。新目录若还没有链接到 Railway 项目，先在里面 `railway link` 到同一个项目与服务，再执行守卫打印的下一步。

## 核对顺序

先做本地核对，再联网；数据库只在全部本地核对通过之后才读。任何一项不过，后面的都不做。

| # | 核对 | 模式 | 不过时 |
|---|---|---|---|
| 1 | 远端名合法（字母、数字、点、下划线、连字符，不以连字符开头） | 全部 | 拒绝 |
| 2 | 工作区干净：`git status --porcelain` 为空（含未跟踪文件） | 全部 | 拒绝，列出前 10 项 |
| 3 | 检出里没有未跟踪的 `.env*`，**gitignore 的也算**；`node_modules/`、`.venv/` 下依赖自带的不算 | 全部 | 拒绝，列出文件名（不读内容） |
| 4 | `git fetch <远端> main`，HEAD 必须等于刚取回的 `<远端>/main` | 全部 | 拒绝；fetch 失败算出错 |
| 5 | progress.md 里这个目标最近一条守卫记录的提交，必须是 HEAD 或 HEAD 的祖先 | 全部 | 拒绝 |
| 6 | 本检出的迁移链：托管副本与源码两条链逐项相同，且各自是一条不分叉的链 | gateway、cron | 拒绝 |
| 7 | `deploy/pick-obs/<服务>/railway.toml` 存在 | cron | 拒绝 |
| 8 | 读生产迁移头：以 observer 登录，版本表恰好一行 | gateway、cron | 拒绝；连不上算出错 |
| 9 | 生产迁移头在本检出的迁移链里 | gateway、cron | 拒绝 |
| 10 | 生产迁移头不早于 `MIN_MIGRATION_HEAD`（0007），且等于本检出的链头 | cron | 拒绝 |
| 11 | `git archive <HEAD> frontend` 导出到新目录，里面有 `frontend/package.json` | frontend | 目录已存在是拒绝；导出失败算出错 |

退出码：0 通过；1 核对没能执行（git 命令失败、连不上库、导出失败）；2 拒绝；130 被中断，重跑是安全的。0 以外一律不部署。

### `.env*` 为什么连 gitignore 的也拦

Vercel CLI 会把工作区里 gitignore 的文件一起上传：09-22 至 09-23 的四个旧前端部署源码里带了 `.env`、`frontend/.env`、`config.yaml` 等（`supabase.md`「资料页上线（P3-6）」）。`railway up` 是否上传 gitignore 的文件没有核实，按会上传处理。所以守卫用不带 `--exclude-standard` 的 `git ls-files --others` 找 `.env*`（`.envrc` 也算）。已跟踪的模板 `.env.example`、`frontend/.env.example` 不算。

### 迁移链从哪里读

- 链从迁移文件本身读：解析 `versions/*.py` 里的 `revision` 与 `down_revision`（只解析，不执行），从 `down_revision = None` 的起点连成一条线。脚本里没有写死任何修订号，以后加了 0008，守卫自动认识它。重复的修订、多个起点、指向不存在修订的 `down_revision`、分叉、合并点（`down_revision` 是元组）一律拒绝。
- 镜像装的是托管副本 `backend/extensions/sources/ggwork-pick`（`docker/Dockerfile.pick-gateway`），所以以托管副本的链为准；它与源码 `customizations/pick-workbench` 的链不一致时拒绝，先按计划 D21 刷新托管副本并合进 main。
- `MIN_MIGRATION_HEAD` 同样从托管副本的 `ggwork_pick/observe/versions.py` 解析出来。

### gateway 与 cron 对迁移头的要求不同

- **gateway**：生产迁移头只要在本检出的链里就通过。链头比生产新，说明这次部署会跑新迁移，守卫会提示「本次部署会把生产迁移头从 X 升到 Y」，这时 gateway 上线并核对之后，**两个 cron 都要从同一提交经守卫重部署**（D5）。
- **cron**：生产迁移头必须不早于 0007，而且**等于本检出的链头**。新迁移只经 gateway 上生产，所以顺序固定为「守卫 → gateway → 两个 cron」（计划「0008 预留」）。链头比生产新时，cron 部署被拒，提示先部署 gateway。cron 镜像自己的自检（D5）只要求「认识且不早于 0007」，守卫在部署前更严一步，是为了不让 cron 先于它依赖的表上线。

## observer 的 DSN 文件

- 路径放在环境变量 `PICK_OBS_DSN_FILE` 里。文件须属于当前用户、是普通文件、不是符号链接、权限 600 或更窄（属主的执行位也不行），规则与 `ggwork_pick/observe/crypto.py` 的私有文件相同。检查在打开之后的同一个文件描述符上做；位置上是 FIFO 时立即拒绝，不会卡住。
- 内容只有一行连接串：`postgresql://…`、`postgres://…`，或带 SQLAlchemy 驱动后缀的 `postgresql+asyncpg://…`（守卫把后缀去掉再交给 psycopg）。连接串里不带 ssl 参数。
- 另需环境变量 `PGSSLMODE`（生产用 `require` 或更严），守卫把它显式传给连接。
- 连上之后先核对 `current_user`：必须是 `pick_observer`（可用 `PICK_OBS_OBSERVER_ROLE` 改名，与 0007 读的是同一个变量）。换成 `deerflow_app` 或 `postgres` 的 DSN 会被拒绝：守卫只接受只读角色的凭据，文件落在本机，丢了损失也最小。
- 读法：一个只读事务（`BEGIN READ ONLY`），`SET LOCAL statement_timeout = '15s'`，读 `current_user` 与 `deerflow.ggwp_alembic_version`，然后回滚。表名写全限定，不依赖 Supavisor 背后的 `search_path`。连接名 `ggwp-deploy-guard`，连接超时 15 秒。
- 准备文件（U3，在本机，不在终端上显示口令）：

```sh
mkdir -p ~/.config/ggwork-obs && chmod 700 ~/.config/ggwork-obs
(umask 077 && pbpaste > ~/.config/ggwork-obs/observer.dsn)   # 从密码管理器复制 DSN 后执行，写完清空剪贴板
export PICK_OBS_DSN_FILE=~/.config/ggwork-obs/observer.dsn PGSSLMODE=require
```

**任何输出都不带 DSN、口令、主机名或数据库原文。** 连不上、没权限这类错误只打印错误类名与 SQLSTATE，常见的附一句提示：

| SQLSTATE | 含义 | 处理 |
|---|---|---|
| 42501 | observer 读不了版本表 | 按 TR-12 补授权：gateway 容器里跑 `python -m ggwork_pick.observe.admin regrant` |
| 42P01 | 库里没有 `deerflow.ggwp_alembic_version` | DSN 连错了库 |
| 28P01 | 口令不对 | 核对 DSN 文件 |
| （无） | 通常是网络或 TLS 没连上 | 核对主机、端口、`PGSSLMODE`；守卫不打印原文，需要时自己用 psql 连一次看原文（别把 DSN 写在命令行上） |

## progress.md 里的守卫记录

守卫通过时打印这样一行，**部署完成并核对之后**原样追加进 `docs/pick-workbench/progress.md`，随提交推到 `ggwork/main`：

```
- `pick-deploy-guard target=gateway commit=<40 位提交> prod_head=0006 chain_head=0007 at=2026-09-26T20:45:12Z`
```

- `target` 是 `gateway`、`cron:trends`、`cron:gsc` 或 `frontend`；`prod_head` 是核对时生产的迁移头，`chain_head` 是本检出的链头（frontend 没有这两项）；`at` 是核对时刻（UTC）。
- 下一次部署同一个目标时，守卫在 progress.md 里找这个目标 `at` 最晚的一条（同一时刻取靠后的一行），要求它的提交是 HEAD 或 HEAD 的祖先。不是祖先，说明生产上跑的是 HEAD 之后的提交（从这里部署等于回退），或者是一个从没进过 main 的提交（从这里部署会丢掉它）。**回滚一律用 main 上的 revert 提交**，不从旧检出部署（计划第 10 节回滚规则）。
- 只有带 `pick-deploy-guard target=` 的行算记录。提到脚本名的普通文字不算；带了这个标记却格式不对（提交不是 40 位小写十六进制、target 拼错、缺 `at`）会被拒绝，免得一条抄错的记录悄悄失效。
- 记录只有推到 `ggwork/main` 之后，另一个会话的守卫才看得到。部署之后尽快提交并推送这一行；两个会话都遵守，祖先检查才完整。
- 某个目标第一次经守卫部署时，progress.md 里还没有它的记录，带 `--first-record`（S1 的前端、S3 的 gateway、S5/S9 的两个 cron 各一次）。以前手写的部署记录（如「后端 301b0ea 于 20:45 UTC 部署」）不是守卫记录，不参与核对。
- 本地是浅克隆时，很早以前的记录提交可能不在本地历史里，守卫会按「不是祖先」拒绝；记录都是守卫上线之后的提交，正常不会碰到。

## 各模式通过之后

- **gateway**：打印 `cd <检出> && railway up --detach`。部署后按第 10 节 S4 核对（日志里没有 `service start() failed`、迁移头、授权、认证后的 `/api/pick/sync`、一次选剧对话）。提示了迁移头升级时，接着经守卫重部署两个 cron。
- **cron**：打印 `cd <检出> && railway up --detach --service pick-obs-<服务>`。部署后以 `--selfcheck-only` 手动触发一次（S6）。
- **frontend**：守卫已把 `git archive <HEAD> frontend` 导出到新目录，里面只有已跟踪的文件。下一步：从已经 `vercel link` 的检出里只拷 `.vercel/project.json` 到导出目录的 `.vercel/`，再在导出目录执行 `vercel deploy --prod`。Vercel 项目的 Root Directory 是 `frontend`，所以在导出目录的根上部署。别的 gitignore 文件一个都不要拷进去。

## 与其他任务的接缝

- **TR-12**：守卫以 `pick_observer` 登录读 `deerflow.ggwp_alembic_version`，依赖 TR-12 给 observer 的这条 SELECT（`bootstrap-observer.sql`，0007 的授权段也有）。S2 执行 `bootstrap-observer.sql` 之后，S3 的 gateway 部署前守卫才能读到头；这时生产还在 0006，所以 bootstrap 脚本本身就得授这条权限，不能只靠 0007。角色名写死为 `pick_observer`，与计划一致；改名时设 `PICK_OBS_OBSERVER_ROLE`。
- **TR-15、TR-21**：cron 模式要求 `deploy/pick-obs/trends/railway.toml`、`deploy/pick-obs/gsc/railway.toml` 已在 main 上。
- **TR-29**：本页由索引收录；「新迁移上线流程」「回滚规则」引用本页的核对顺序与记录格式。
- **CI**：`pick-workbench-tests.yml` 的触发路径目前不含 `scripts/pick-deploy-guard.py`，lint 步骤也只查 `customizations/pick-workbench`。只改脚本的提交不会触发测试，合并时要与 TR-15 的 `deploy/pick-obs/**` 一起补进触发路径。脚本按 ruff 默认配置（88 列）格式化。
