# Trends cron 的打包、Railway 配置与部署验证（TR-15）

配置：`deploy/pick-obs/trends/railway.toml`（cron 本身）、`deploy/pick-obs/trends/selfcheck/railway.toml`（S6 一次性自检，同一次运行接着做 S6a 的预检），由 `scripts/pick-railway-settings.py` 写进服务设置（第 3.3 节）。测试：`customizations/pick-workbench/tests/observe/test_railway_config.py`、`test_cron_deploy_procedure.py`、`test_railway_settings.py`，共用的辅助函数在同目录的 `railway_helpers.py`。设计 3.1、6.3；计划 TR-15、D5、D6、D21，第 9 节，第 10 节 S5、S6、S6a；反例 15。

启动顺序、自检各项与退出码见 `lease-and-selfcheck.md`；会话本身（命令、模式、环境变量的含义）见 `trends-session.md`；部署前的核对见 `deploy-guard.md`。本页讲代码怎样进镜像、服务怎样配、部署后怎样验。gsc 的服务配置随入口在 TR-21 里做，写在 TR-21 自己的手册页（第 10 节）。

## 1. 打包路径：源码 → 托管副本 → 镜像

| 层 | 位置 | 谁写 |
|---|---|---|
| 源码 | `customizations/pick-workbench/`（包 `ggwork_pick`） | 各任务，唯一可以改的地方 |
| 托管副本 | `backend/extensions/sources/ggwork-pick/` | `deerflow extensions upgrade` 生成，不手改（见第 2 节） |
| 锁文件 | `backend/pyproject.toml` 以路径依赖引用托管副本（`ggwork-pick = { path = "extensions/sources/ggwork-pick" }`），`backend/uv.lock` 锁住它的版本与依赖 | 同上 |
| 镜像 | `docker/Dockerfile.pick-gateway`：构建阶段 `COPY backend ./backend`，再 `uv sync --locked --no-dev --extra postgres`，hatchling 把托管副本构建成非 editable 的 wheel，装进 `/app/backend/.venv/lib/python3.12/site-packages/ggwork_pick` | Railway 构建 |

- **镜像里没有 `customizations/`**。源码改了而托管副本没刷新，镜像里就还是旧快照（反例 15）。所以部署前 main 上的 `test_managed_copy` 必须是绿的，部署后还要在 S6 核对包摘要（第 6 节）。
- **非 Python 文件也在包里**：`observe/trends/canary_controls.json`（TR-05 的金丝雀对照清单）、`mirror/ddl.sql`、`mirror/pan_rules.json`、`migrations/versions/*.py`（自检从这里静态读迁移链）。`.dockerignore` 在别处排除了 `tests/`、`*.md`、`lib/` 等，但最后用 `!backend/extensions/sources/**` 把托管副本整个放回构建上下文；hatchling 构建 wheel 时还会按它往上找到的第一个 `.gitignore`（镜像里是 `backend/.gitignore`）排除文件。`test_packaged_files_reach_the_image` 按 Docker 的规则顺序与 git 的 ignore 规则逐个核对包里每个文件都进得了镜像。往 `.dockerignore`、任何一级的 `.gitignore` 或 `.railwayignore` 加规则时注意这一条；这些文件一改，CI 就跑这些测试（第 8 节）。
- **同一个镜像，只换启动命令**：gateway 与两个 cron 用同一个 Dockerfile。镜像的 `CMD` 仍是 gateway 的（`cd backend && python -m app.gateway.pick_entrypoint`，`backend/tests/test_pick_cloud_entrypoint.py` 钉住），没有 `ENTRYPOINT`，所以 cron 的 `startCommand` 整个替换 `CMD`，不会被当成参数拼到别的入口后面。
- 启动命令里的 `python` 是 `/app/backend/.venv/bin/python`：镜像设了 `ENV PATH="/app/backend/.venv/bin:$PATH"`。cron 进程只导入观测包（TR-01 的延迟导入），不读 gateway 的运行时配置，也不需要 `DEER_FLOW_*` 变量。

## 2. 托管副本的刷新（D21）

- **任务分支不提交托管副本**，跑测试时 `--deselect tests/test_managed_copy.py`。
- **批次合并进 `feat/trends-radar` 之后**，由集成者在集成分支的 worktree 里统一刷新一次并单独提交：

  ```sh
  # 集成分支 worktree 的根目录；配置指向 scratchpad 里的一份副本，绝不指向主目录的 config（计划第 6 节）
  cp config.pick.example.yaml <scratchpad>/config.pick.yaml
  cd backend
  DEER_FLOW_CONFIG_PATH=<scratchpad>/config.pick.yaml UV_EXTRAS=postgres \
    <主目录>/backend/.venv/bin/deerflow extensions upgrade "$PWD/../customizations/pick-workbench" --yes
  ```

  - 管理器从当前目录往上找 `backend/pyproject.toml` 定项目根，把源码快照进这个 worktree 的 `backend/extensions/sources/ggwork-pick`，再对这个 worktree 的 `backend/` 执行 `uv add` 与 `uv sync --locked`（同步的是这个 worktree 自己的 `backend/.venv`，不是主目录的共享环境）。所以一定在集成分支 worktree 里执行，不要在主目录执行。
  - PATH 上的 `uv` 必须是 CI 固定的 0.11.1（`backend/tests/test_ci_uv_version_pin.py` 钉住这个版本）。本机默认的 uv 更新，重锁出的 `uv.lock` 可能与 CI、镜像构建的 uv 不一致，`uv sync --locked` 会在构建时失败。
  - 刷新后 `git status` 里只应出现 `backend/extensions/sources/ggwork-pick/**` 与 `backend/uv.lock`；锁文件只应变 ggwork-pick 自己的版本与新增的依赖。多出别的改动先查清再提交。
  - 然后跑完整套件，**含** `test_managed_copy`（只在集成分支跑），再提交，说明写 `chore(pick): 刷新 ggwork-pick 托管副本…`，附套件结果。先例：2c38150。
- 包的版本号在 `customizations/pick-workbench/pyproject.toml`。依赖变了（例如 TR-01 加 `pyjwt[crypto]`、`cryptography`）要升版本号，锁文件随之变；只改代码时可以不升。
- 生产部署只从 main 的干净检出进行（`deploy-guard.md`），所以镜像里是哪一版，由 main 上那次刷新决定。

## 3. Railway 配置（D6）

### 3.1 cron：`deploy/pick-obs/trends/railway.toml`

| 字段 | 值 | 为什么 |
|---|---|---|
| `[build]` | 与根目录 `railway.toml` 相同：`builder = "DOCKERFILE"`，`dockerfilePath = "docker/Dockerfile.pick-gateway"` | 与 gateway 同一个镜像 |
| `startCommand` | `/bin/sh -c "cd /app/backend && exec python -m ggwork_pick.observe.trends run"` | Railway 按 exec 形式执行启动命令，不经 shell，`cd` 与 `&&` 要靠 `/bin/sh -c`；`exec` 让 python 替换 shell、成为这个进程本身（大概率是容器的 PID 1）。观测包不处理 SIGTERM：Railway 停容器时进程不做任何收尾，可能直接被 SIGTERM 按默认行为结束，也可能在停止超时后被 SIGKILL 终止，取决于进程与容器的信号行为（只有 SIGINT 会变成 KeyboardInterrupt、以 130 退出）。被强停时：已提交状态保留、预算不退；未提交的响应、最新 cookie、熔断事件可能丢失；续跑可能重做未完成单元；五分钟是租约失效上界，不是恢复时间，通常要等下一次半小时触发（第 7 节）。请求预算在发出之前就已在租约下扣掉，所以续跑不会多发超出预算的请求 |
| `restartPolicyType` | `NEVER` | 失败不立刻重跑：1 由下一次触发续跑，2、3 要人处理，重跑只会原样再失败 |
| `cronSchedule` | `*/30 17-23,0-1 * * *`（UTC） | 见下表 |
| 不写 `healthcheckPath` | | cron 不监听端口；写了就会一直等不到健康而判失败 |

Railway 已经不读服务的 `railway.toml`：Config as Code 已弃用，2026-09-28 在 S5 实测，给服务设配置路径被 API 拒绝；gateway 的部署清单也显示根目录的 `railway.toml` 从没生效（没有健康检查、重启重试 10 次，Dockerfile 来自服务变量 `RAILWAY_DOCKERFILE_PATH`）。所以这份文件不再是 Railway 读的配置，而是 cron 该怎样跑的唯一陈述：由 `scripts/pick-railway-settings.py` 把它写进服务自己的设置，并对着它核对服务设置与每次部署实际用的清单（第 3.3 节）。不在控制台里手改这几项。根目录的 `railway.toml` 与 Dockerfile 的 `CMD` 都不改（`test_root_railway_untouched`、`test_dockerfile_cmd_untouched`）。

**触发时刻**（UTC，每晚 18 次；02:00 UTC 是北京 10:00 的目标发布时刻，D23 的 target_date 是这次发布的日期）：

| 触发 | stable（17:30 起跑） | canary2（18:30 起跑） | canary1（21:00 起跑） |
|---|---|---|---|
| 17:00 | 不到起跑时刻，退出 0，什么都不做 | 同左 | 同左 |
| 17:30 | 建当晚批次，开始会话，一直跑到做完或 01:45 | 不到起跑时刻，退出 0 | 同左 |
| 18:00 | 会话还在跑，Railway 跳过这次触发；会话已崩溃，就续跑 | 不到起跑时刻，退出 0 | 同左 |
| 18:30 | 同上 | 建当晚批次，开始会话 | 不到起跑时刻，退出 0 |
| 19:00–20:30 | 同上 | 会话还在跑就跳过；会话已崩溃，就续跑 | 不到起跑时刻，退出 0 |
| 21:00 | 同上 | 同上 | 建当晚批次，开始会话 |
| 21:30–01:30 | 会话在跑就跳过；崩溃了就续跑（`window_end` 与任务清单不重算）；已做完就退出 0 | 同左 | 同左 |
| 01:45 | 硬截止：之后不再发请求，会话收尾退出 | 同左 | 同左 |

起跑时刻由 G3 从 20:30、22:00 提前到上表（计划第 9 节）：生产节奏（`PICK_OBS_TRENDS_PACE` 默认 `user`）下，原来的 stable、canary2 放不进窗口，canary1 的 22:00 刚过线、午夜后崩溃一次只剩 86%。`window_end` 随之变早（建批次那个整点减 3 小时，`trends-session.md`）。

- Railway cron 在上一轮没退出时跳过下一次触发，也不会替程序终止上一轮，所以程序自己在 01:45 停（设计 3.1）。会话中途崩溃，最多隔 30 分钟由下一次触发接着跑；01:30 是最后一次，离截止 15 分钟。
- 触发不保证准到分钟，02:00 是目标和监测指标，不是保证。
- 每次触发（包括 17:00 那次）都先校验配置（包括模式在所设节奏下放不放得进窗口，计划第 9 节）：配置错了当晚 17:00 就以 2 退出，日志里看得见。启动自检（采集合同版本、迁移头、角色）只在窗口内的触发里做，在取租约之前（`lease-and-selfcheck.md`）；窗口外的空转触发不连库。不在窗口里的触发在日志里留一行 `[pick-obs] trends <日期>: 还没到 … 的起跑时刻 …，什么都不做`，可以用来确认 cron 在按时触发。
- `test_cron_schedule_fits_the_session_modes` 把这张表与 `budget.MODES` 对着钉住：每个模式的起跑时刻都是一次触发、没有触发落在 01:45 及之后、窗口里相邻触发不超过 30 分钟、最早的起跑（stable 的 17:30）之前只有 17:00 一次空转；UTC 02:00–17:00 之间既没有触发，也没有会话（第 7 节的部署时段）。以后改模式的起跑时刻，要同时看这里与 cron 计划。

### 3.2 一次性自检与预检：`deploy/pick-obs/trends/selfcheck/railway.toml`

与 cron 配置相同的 `[build]`，`restartPolicyType = "NEVER"`，**没有** `cronSchedule`：没有 cron 计划的服务部署后立即运行一次，跑完退出，不再重启。启动命令把 `run` 换成先后两步：

```
/bin/sh -c "cd /app/backend && python -m ggwork_pick.observe.trends --selfcheck-only && exec python -m ggwork_pick.observe.trends preflight"
```

1. `--selfcheck-only`（S6）：校验会话配置，做启动自检，打一行 `[pick-obs] selfcheck ok: …`。
2. 自检以 0 退出才接着跑 `preflight`（S6a，计划第 9 节「负载闸门」）：只读、不取租约、不发 HTTP，按当晚第一次触发会读的东西展开任务清单，打一行 `{"preflight": …}`（字段见 `trends-session.md`「金丝雀的负载闸门」）。

这次部署的退出码：自检不过，就是自检的码（2 或 3），日志里没有 `preflight` 那一行；自检通过，就是 `preflight` 的码：0 表示当晚是一个合格的金丝雀夜晚，2 表示会被拒跑（原因在那一行的 `reasons`、`refused_by` 里）。第 6 节逐行讲怎么读。

为什么要单独一份：cron 配置的启动命令固定是 `run`，白天触发时它在起跑时刻之前就退出了，根本不做自检。所以 S6 要在镜像里、带着服务自己的变量跑一次 `--selfcheck-only`，就临时把这份文件写进服务设置（第 3.3 节，第 5、6 节），核对完再写回 cron 配置。预检放在同一次运行里，也是因为只有这里既是同一个镜像、又带着同一套服务变量：cron 服务平时没有在跑的容器，`railway ssh` 进不去；在本机 `railway run` 要把 `PICK_OBS_STATE_KEY` 带到本机（入口在读库之前就加载状态密钥），不这样做。`test_selfcheck_config_pinned` 钉住这条命令，`test_trends_entrypoint_argv_selfcheck_only` 与 `test_selfcheck_config_exits_2_when_tonight_is_not_a_canary` 从这份文件读出命令，经真的 `/bin/sh` 与真实的 `__main__` 在两种库上跑（第 9 节）。

这里用到的两条 Railway 行为（没有 cron 计划的部署立即运行一次；`NEVER` 退出后不重启）是按 Railway 的 cron 文档写的。S5 第一次部署时核对，与此不符就改本页与这份配置。原先的第三条（配置文件里写了的字段覆盖控制台设置）已不成立，见第 3.1 节末与第 3.3 节。

### 3.3 写进服务设置：`scripts/pick-railway-settings.py`

```sh
<主目录>/backend/.venv/bin/python scripts/pick-railway-settings.py plan  <toml>
<主目录>/backend/.venv/bin/python scripts/pick-railway-settings.py apply <toml> -p <项目> -e production -s pick-obs-trends
<主目录>/backend/.venv/bin/python scripts/pick-railway-settings.py check <toml> -p <项目> -e production -s pick-obs-trends [--deployment <部署 ID>]
```

- `<toml>` 是上面两份文件之一，可以照本页写成 `/deploy/...`（按脚本所在检出解析）。`plan` 不连 Railway，只打印会写的内容。
- `apply` 用 `serviceInstanceUpdate` 写 `startCommand`、`restartPolicyType`、`cronSchedule`，并把 `healthcheckPath` 清空；四项每次都写全，所以从 cron 配置换到自检配置时 `cronSchedule` 被写成空，不会残留。构建用的 Dockerfile 写成服务变量 `RAILWAY_DOCKERFILE_PATH`（gateway 也是这样拿到 Dockerfile 的），带 `--skip-deploys`。`apply` 本身不部署，写完接着做一次 `check`。
- `check` 比对服务设置与 `RAILWAY_DOCKERFILE_PATH`；带 `--deployment` 时再比对那次部署实际用的清单（`serviceManifest` 的 `build.builder`、`build.dockerfilePath` 与上面四项）。部署 ID 用 `railway deployment list -p <项目> -e production -s pick-obs-trends --limit 1 --json` 取。
- 退出码：0 一致；1 有差异（逐项打印 `期望/实际`）；2 用法错、文件有脚本写不了的键、服务找不到、railway 调用失败或答复不合预期（项目或部署 ID 写错、登录过期、答复不是 JSON），只打一行原因，不打调用栈。`apply` 写完服务设置而 Dockerfile 变量没写成时，报错会说明是哪一半没写，重跑 `apply` 即可（两步都是整值覆盖）。文件只能有 `[build]` 的 `builder`（必须是 `DOCKERFILE`）、`dockerfilePath` 与 `[deploy]` 的 `startCommand`、`restartPolicyType`、`cronSchedule`：以后要加字段（例如健康检查），先改脚本与测试，否则 `apply` 以 2 拒写，不会只写一半。
- 目标一律显式给 `-p/-e/-s`，不 `railway link`。`-e` 照 CLI 的习惯写环境名（`production`）即可：GraphQL 只认环境 ID（2026-09-28 S5 实测，传名字得到 `Not Authorized`），脚本先按名字查出 ID 再调 API；railway 的 GraphQL 报错（它写在 stdout）会随错误信息转述，api 的答复里没有变量值。脚本读变量时只留 `RAILWAY_DOCKERFILE_PATH` 一项，其余值（含机密）不留、不打印；railway 调用失败时只转述 stderr 的第一行，不转述 stdout，`variable list` 失败时连这一行也不转述。`RAILWAY_DOCKERFILE_PATH` 不在第 5 节的变量表里：它是构建设置，入口不读，也不触发「这个服务不读」的警告（只查 `PICK_OBS_` 前缀）。
- `test_railway_settings.py` 用假的 railway CLI 钉住两份文件怎样映射成这些调用、比对哪些项、不打印变量值、失败时的退出码（第 9 节）。

## 4. S5 之前

- S0–S4 已完成：0007 已在生产，`pick_observer` 已建（S2）且授权核对过（S4），带 0007 的 gateway 已上线并核对过。
- 要部署的提交在 main 上，main 上的 CI 绿（含 `test_managed_copy`），`deploy/pick-obs/trends/` 两份配置都在 main 上（守卫的 cron 模式会查）。
- TR-05 的 `canary_controls.json` 已随包进 main，而且每个要查的 geo 都有市场序列（`trends-session.md`「金丝雀的任务来源」）。缺了，S6 的自检会以 2 退出并点名这个文件。
- G2 已定阶段 0 的去向与粒度，决定 `PICK_OBS_TRENDS_ROUTE`、`PICK_OBS_TRENDS_GRANULARITY` 怎么设；金丝雀期间不改（计划第 9 节）。
- U13（出口回显服务）批准与否已知。

## 5. 建服务与部署（S5）

每一步都是对外操作，逐项经用户同意（计划第 10 节、U11）。

1. **建服务** `pick-obs-trends`：与 gateway 同一个 Railway 项目、同一个环境。不接 GitHub 的推送自动部署（接了源仓库也要把自动部署关掉），只从守卫推荐的干净检出 `railway up`。
2. **服务设置**：不在控制台手填。第 4 步的代码块先用 `scripts/pick-railway-settings.py apply` 写自检配置 `/deploy/pick-obs/trends/selfcheck/railway.toml`（第一次部署就是 S6 的自检），核对通过后再写 cron 配置（第 3.3 节）。
3. **变量**（下表只写名字与来历；机密由用户直接填进 Railway，不经对话、不进 progress.md 与任何日志）：

| 变量 | 值 | 谁填 | 说明 |
|---|---|---|---|
| `PICK_DATABASE_URL` | `pick_observer` 的 Supavisor session DSN（端口 5432），URL 里不带任何 ssl 参数 | 用户 | 必填。见 `observer-role.md`、supabase.md 第 3 节；这个串不配给 gateway 和 Vercel |
| `PGSSLMODE` | `require` | 代理 | 必填。asyncpg 读它决定 TLS |
| `PICK_OBS_STATE_KEY` | Fernet 密钥；轮换时多把用逗号分隔，新的放最前 | 用户 | 必填。用户按表下的命令生成，填进 Railway 并存进密码管理器。丢了它，库里的状态与 cookie 罐读不回来，每次触发以 3 退出（`trends-state.md`、`lease-and-selfcheck.md`） |
| `PICK_OBS_STATE_KEY_FILE` | 不设 | — | cron 没有卷；与 `PICK_OBS_STATE_KEY` 同时设会被拒（退出 2） |
| `PICK_OBS_EXPECTED_COLLECTOR` | 所部署提交的 `versions.COLLECTOR_VERSION`，目前是 `obs-collector-v1` | 代理 | 必填。与镜像里的常量不符，自检以 2 退出 |
| `PICK_OBS_EXPECTED_ROLE` | `pick_observer` | 代理 | 必填。与连上的 `current_user` 不符，自检以 2 退出 |
| `PICK_OBS_TRENDS_MODE` | `canary1`；金丝雀阶段 2 改 `canary2`；S10 改 `stable` | 代理 | 必填 |
| `PICK_OBS_TRENDS_GRANULARITY` | G2 定的粒度：`H`（默认）、`D` 或 `HD` | 代理 | 可选，金丝雀期间不改 |
| `PICK_OBS_TRENDS_ROUTE` | 第 8 节的去向：`both`（默认）、`a_only`、`b_only` | 代理 | 可选，金丝雀期间不改；`neither` 会被拒，Trends 不上线 |
| `PICK_OBS_TRENDS_PACE` | 不设（即 `user`：令牌桶 4、每分钟补 2） | — | 可选，金丝雀期间不改。`design`（桶 8、每分钟补 4）是设计 4.2 的原值，只在计划第 9 节按它重排之后才用；取值不认识，或模式在这个节奏下放不进窗口，以 2 拒跑 |
| `PICK_DB_SIZE_CAP_BYTES` | 与 gateway 相同 | 代理 | 计划 S5 列了它，给 TR-20 的发布前容量检查用；TR-20 之前没有代码读它，设了也不生效 |
| `PICK_OBS_PUBLISH` | 不设 | — | D11：不设就是 shadow；金丝雀无论如何不发布。S12b 才设 `1` |
| `PICK_OBS_EGRESS_ECHO_URL` | 不设 | — | U13 批准前不设。旧稿计划 S5 写的 `PICK_OBS_EGRESS_URL` 是错名，代码读的是这个；设成错名时入口会在 stderr 点名并提示正确名字，但出口测量不会开 |
| `PICK_OBS_CONTRACT_CHECK` | 不设 | — | U12 批准后设 `1`（每周一的线上合同检查） |
| `PICK_OBS_CANARY_SINCE` | 不设 | — | TR-30 修复后重跑金丝雀时设 |

   **生成 `PICK_OBS_STATE_KEY`** 只由用户在自己的终端里执行，代理不执行。密钥直接进剪贴板，不显示在终端上，也就不留在滚动记录或会话记录里：

   ```sh
   <主目录>/backend/.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode(), end='')" | pbcopy
   ```

   粘贴进 Railway 的变量与密码管理器之后，执行 `pbcopy < /dev/null` 清空剪贴板。`test_runbook_never_prints_a_generated_secret` 钉住本页生成密钥的命令都送进剪贴板。

   `test_runbook_variables_are_the_ones_the_service_reads` 核对上表：表里的名字都是 trends 服务读的，必填的一个不少。不要设 gateway 的变量（`DEER_FLOW_*`、模型密钥、`deerflow_app` 的连接串）：cron 不读，多一份机密就多一处泄露。

4. **部署，自检与预检，切回 cron（S5、S6、S6a 连着做）**：按下面的顺序执行，每一行 `railway up` 与推送都是对外操作，逐项经用户同意；中间那次核对的细节在第 6 节。

   ```sh
   # 在守卫推荐的干净检出 <检出> 的根目录；<主目录> 是主仓库，<项目> 是 Railway 项目 ID。以后每次部署（第 7 节）去掉 --first-record
   <主目录>/backend/.venv/bin/python scripts/pick-deploy-guard.py cron trends --first-record
   <主目录>/backend/.venv/bin/python scripts/pick-railway-settings.py apply /deploy/pick-obs/trends/selfcheck/railway.toml -p <项目> -e production -s pick-obs-trends
   cd <检出> && railway up --detach --service pick-obs-trends
   # 等这次运行结束，按第 6 节第 1-4 步读日志里的 selfcheck ok 与 preflight 两行、逐项核对，再对这次部署跑 check --deployment（第 6 节第 1 步）；任何一项不过就停在这里
   <主目录>/backend/.venv/bin/python scripts/pick-railway-settings.py apply /deploy/pick-obs/trends/railway.toml -p <项目> -e production -s pick-obs-trends
   cd <检出> && railway up --detach --service pick-obs-trends
   # 按第 6 节第 5 步核对这次部署；然后把守卫打印的记录行追加进 docs/pick-workbench/progress.md，提交，经用户同意推到 ggwork/main
   ```

   - **守卫只在开头跑一次**。两次 `railway up` 都从这个检出、这个提交执行，中间不提交、不 pull、不改文件（`apply` 只写 Railway 的服务设置，不动检出）。所以两次部署构建自同一个提交，第 6 节核对过的包摘要就是 cron 之后跑的那一份。
   - **`railway up` 那一行照守卫打印的写**（`test_cron_deploy_procedure.py` 钉住）。检出没有 `railway link`（不 link 是规矩）时，执行时补上 `-p <项目> -e production`：来源与提交不变，只是补齐目标；不补时未链接的目录会进交互或新建项目。
   - **预检不过就不切回 cron 配置**：自检通过而 `preflight` 退出 2（第 6 节第 3 步），服务就留在自检配置上（没有 cron 计划，当晚不会跑），按原因补齐之后在控制台对这次部署点 Redeploy（还是这次上传的代码），重读两行，两行都过了才往下走。
   - **`--first-record` 只用这一次**：progress.md 里还没有 `cron:trends` 的守卫记录时（S5 这一轮）才带，以后每次部署都不带。带错了守卫会拒（没有记录时不带，或已有记录时带），不要为了过守卫改这个参数。
   - **记录行只记一次，放在最后**：两次部署都核对完，把守卫这一轮最后一次通过时打印的那一行（`pick-deploy-guard target=cron:trends commit=…`）追加进 progress.md，提交，经用户同意推到 `ggwork/main`（`deploy-guard.md`「progress.md 里的守卫记录」）。两次部署之间不要提交它：提交之后 HEAD 就不再等于 `ggwork/main`（守卫的检查 5），只写不提交则工作区不干净（检查 2），这一轮再跑守卫都会被拒。
   - **中间拖久了**（`deploy-guard.md`「通过之后立即部署」），就在第二次 `railway up` 之前重跑守卫，命令与这一轮开头那次一字不差：记录还没推，首次仍要带 `--first-record`。重跑被拒（通常是 `ggwork/main` 前进了），就不要从这个检出继续：在最新 main 的干净检出里从头走这一套，因为提交变了，包摘要要重新核。
   - 只在 UTC 02:00–17:00 之间做这一套（第 7 节）。

   `test_cron_deploy_procedure.py` 把上面的代码块逐行交给 TR-34 的守卫重放（假 git、假库）：首次照写执行、以后去掉 `--first-record` 执行、第二次部署之前按原样重跑守卫，都必须通过，而且每一行 `railway up` 都与守卫打印的下一步一字不差；每一行 `apply` 都要能按脚本自己的参数解析成对 `pick-obs-trends` 的 `apply`，先自检配置、后 cron 配置。TR-34 的守卫不在树里时（TR-15 分支单独时）它跳过，集成之后生效。

5. **核对服务设置**：`apply` 已经核过一次服务设置；每次部署之后，再对这次部署跑 `check --deployment`（第 3.3 节），确认它实际用的清单与文件一致：自检配置那次健康检查为空、重启 `NEVER`、`cronSchedule` 为空。退出 1 就按打印的差异重新 `apply` 那份文件、再部署，不在控制台手改。

## 6. 部署验证（S6、S6a）

1. 自检配置的部署会立即运行一次并退出（第 3.2 节：先自检，通过了再预检）。先对这次部署跑 `pick-railway-settings.py check /deploy/pick-obs/trends/selfcheck/railway.toml … --deployment <部署 ID>`，退出 0 才说明这次运行用的就是自检配置；再读这次部署的 Deploy Logs。都通过时退出码 0，标准输出是这两行，先后不变（stderr 的日志会带时间戳把它们再打一次）：

   ```
   [pick-obs] selfcheck ok: collector=obs-collector-v1 head=<生产迁移头> role=pick_observer package=sha256:<摘要> at=/app/backend/.venv/lib/python3.12/site-packages/ggwork_pick
   {"preflight": {"target_date": "<今晚供给的日期>", "mode": "canary1", "pace": {"preset": "user", "bucket_capacity": 4, "refill_per_minute": 2}, …, "reasons": [], "refused_by": [], "estimates": […]}}
   ```

   第一行出现之前，入口已经校验过会话配置（模式与节奏、金丝雀对照清单与市场序列、状态密钥、出口测量地址），与每晚 `run` 发请求之前的校验相同；自检与预检都只读、不取租约、不写任何行、不发任何 HTTP 请求（`test_trends_entrypoint_argv_selfcheck_only` 从自检配置读出启动命令，经 `/bin/sh` 与真实的 `__main__` 在两种库上钉住）。没有第一行、退出码不是 0：配置校验或自检没过，预检没跑，按第 4 步的表处理。有第一行、没有第二行、退出码 3：自检通过，预检读状态失败（运行时行缺失或状态读不回），按 `lease-and-selfcheck.md` 的退出码 3 处理。两行都在、退出码 2：镜像没问题，今晚会被负载闸门或停用、终止拒跑，按第 3 步处理。
2. **逐项核对第一行（S6）**：
   - `collector` 等于所部署提交的 `versions.COLLECTOR_VERSION`；
   - `head` 等于守卫刚打印的生产迁移头；
   - `role` 是 `pick_observer`；
   - `at` 在 `/app/backend/.venv/.../site-packages/` 下，说明跑的是镜像里装的 wheel；
   - `package` 等于在所部署提交的干净检出里算出的摘要。在守卫用的那个检出的根目录执行（主目录的 Python 即可，摘要只看文件内容）：

     ```sh
     <主目录>/backend/.venv/bin/python - <<'EOF'
     import sys
     from pathlib import Path
     sys.path.insert(0, "customizations/pick-workbench")
     from ggwork_pick.observe.selfcheck import package_digest
     print("source ", package_digest(Path("customizations/pick-workbench/ggwork_pick")))
     print("managed", package_digest(Path("backend/extensions/sources/ggwork-pick/ggwork_pick")))
     EOF
     ```

     两行应当相同，并且等于日志里的 `package=`。`source` 与 `managed` 不同，是托管副本没刷新（第 2 节）；两者相同而与日志不同，是镜像不是从这个提交构建的。两种情况都不往下走。
   - stderr 里没有「环境变量 … 这个服务不读」的警告；有就是变量名写错了，按提示改名，在控制台对这次部署点 Redeploy（还是这次上传的代码，自检与预检再跑一次），重读日志。
3. **逐项核对第二行（S6a，计划第 9 节「负载闸门」）**，字段的含义见 `trends-session.md`「金丝雀的负载闸门」：
   - `target_date` 是今晚会话供给的日期（部署时段里是明天的日期），`mode`、`pace` 是服务变量设的模式与节奏（金丝雀期间 `pace` 的 `preset` 应是 `user`）；
   - `reasons` 与 `refused_by` 都是空的；`planned_requests` 不少于 `min_requests`，`controls.positive.matched` 不少于 `min_positive`；
   - `missing_first` 列的是清单里在共享剧库批次中找不到的对照（只有 identity，不带剧名），记进 progress.md 备查；
   - `estimates` 是这份任务清单在所设节奏下三种夜晚的估算，无熔断的 `coverage` 应为 1。

   `reasons` 非空（负载不够：近 14 天剧目太少、正对照匹配不到一半）时退出 2，不进 S7：服务留在自检配置上（没有 cron 计划，当晚不会跑），按原因补齐共享剧库批次或对照清单（对照清单归 TR-05，改了要走第 2 节刷新、合进 main，从新的 main 重走第 5 节第 4 步），然后对这次部署点 Redeploy 重读两行；不改门槛。`refused_by` 非空（`disabled_7d` 或 `canary_terminated`）按 `trends-session.md` 处理。即使切回了 cron 配置，当晚的 `run` 也会按同一个闸门再判一次，不合格就拒跑，那一天不算金丝雀日。
4. **不通过时**（第一行）：

| 现象 | 原因与处理 |
|---|---|
| 退出 2，点名 `PICK_OBS_TRENDS_MODE` 等会话变量 | 变量缺失或取值不认识（为免泄露不回显值）；按第 5 节的表改 |
| 退出 2，说模式的计划在这个节奏下放不进窗口 | `PICK_OBS_TRENDS_PACE` 或模式设错；按第 5 节的表与计划第 9 节改，不要为了过这一步改模式参数 |
| 退出 2，点名 `canary_controls.json` 或列出缺市场序列的 geo | TR-05 的对照清单没进包或缺短语；补齐后走第 2 节刷新、合进 main，从新的 main 重走第 5 节第 4 步 |
| 退出 2，`PICK_OBS_EXPECTED_COLLECTOR` 不符 | 变量填错，或镜像不是预期那一版（反例 15）；先核对包摘要 |
| 退出 2，迁移头不在本镜像的迁移链里 | 镜像比库旧：从最新 main 经守卫重新部署（先 gateway 再 cron，`deploy-guard.md`） |
| 退出 2，迁移头早于 0007 或库里没有迁移头 | 0007 没上生产，或 `pick_observer` 的 search_path 不是 `deerflow`（报错会带当前 schema 名，见 `lease-and-selfcheck.md`） |
| 退出 2，角色不符 | `PICK_DATABASE_URL` 用的不是 `pick_observer` |
| 退出 3，带 `42501` | 缺授权：在 gateway 容器里跑 `observe.admin regrant`（`observer-role.md`） |
| 退出 3，不带 SQLSTATE | 连不上：查 DSN、`PGSSLMODE`、Supavisor 端口是否是 5432 |
| 包摘要不符 | 见上一步；不要带着不符的镜像进 S7 |

5. **切回 cron 配置**：两行都通过后，走第 5 节第 4 步顺序的后半段：`apply /deploy/pick-obs/trends/railway.toml`，从同一个检出再 `railway up --detach --service pick-obs-trends` 一次。不重跑守卫：同一个提交，守卫刚核过（拖久了按第 5 节第 4 步的规则重跑）。再对这次部署跑 `check /deploy/pick-obs/trends/railway.toml … --deployment <部署 ID>`：健康检查为空、重启 `NEVER`、`cronSchedule` 是 `*/30 17-23,0-1 * * *`，Dockerfile 是 gateway 那一份。退出 0 了，才把守卫的记录行追加进 progress.md，提交，经用户同意推送。
6. **第一晚（S7）**：17:00 那次触发的日志应当是「还没到 canary1 的起跑时刻 21:00 UTC，什么都不做」；21:00 起会话开头的自检照样打出同一行 `selfcheck ok`，其中的 `package=` 应与 S6 相同（两次部署构建自同一个提交）。之后按 `trends-session.md` 与 TR-30 监控。

## 7. 之后的部署、回滚与停用

- **什么时候部署**：只在 UTC 02:00–17:00 之间部署 cron 服务，重做 S6 的整套也一样。17:00 起有触发，17:30 起 stable 会话开跑，会话最晚到 01:45 截止后收尾退出：窗口里重新部署会终止正在跑的会话，当晚少一截数据；切到自检配置的那段时间服务没有 cron 计划，赶上窗口就漏掉触发。`test_cron_schedule_fits_the_session_modes` 钉住这段时间里没有触发、也没有会话。
- **每次部署 cron** 都先跑守卫，不带 `--first-record`。推荐每次都按第 5 节第 4 步的顺序重做 S6（`apply` 自检配置、部署、核对两行，再 `apply` cron 配置、部署，最后记一次记录行），这样每个上线的镜像在第一次发请求之前都被人看过包摘要，也顺带看过当晚的预检行（退出 2 按第 6 节第 3 步处理）。金丝雀期间想在某一晚之前再看一次预检，也是重做这一套：在部署时段里 `apply` 自检配置、部署、读两行、`apply` 回 cron 配置并部署，不另找地方跑 `preflight`（第 3.2 节）。不重做时，服务设置保持 cron 配置：守卫通过后 `railway up` 一次，对这次部署跑 `check --deployment`，再记记录行；然后在部署后的第一晚，读窗口内第一次触发打出的那行 `selfcheck ok`，按第 6 节核对 `package=`。采集合同、迁移头、角色三项在窗口内的每次触发（会话开始或续跑）都会在取租约之前自动比对，不符就在任何请求之前以 2 退出；窗口外的空转触发（如 17:00 那次）只校验配置，不做自检。只有包摘要要靠人看。
- **新迁移上生产之后**，先 gateway、后两个 cron，都从最新 main 经守卫重部署（D5；守卫的 cron 模式要求生产迁移头等于本检出的链头）。cron 的镜像认不出生产的迁移头时，每次触发都以 2 退出，数据页会亮 `run_missed`。
- **回滚**只用 main 上的 revert 提交，而且回滚后的代码必须认识生产的迁移头（计划第 10 节回滚规则）。守卫拒绝从旧检出部署。
- **改变量**：Railway 改变量会重新部署服务；对 cron 来说，新值从下一次触发起生效。改模式（`canary1` → `canary2` → `stable`）在白天改，不要在会话进行中改；改了模式，起跑时刻与 `window_end` 跟着变（`trends-session.md`「换起跑时刻的第一天」）。
- **停用**：按计划第 10 节的顺序关 `PICK_OBS_AGENT`、关 `PICK_OBS_PUBLISH`、再停 cron。停 cron 用控制台暂停服务，或 `apply` 自检配置再部署一次（没有 cron 计划的部署只跑一次自检与预检就退出，之后不再触发）；不回退镜像。暂停前确认控制台里这个服务没有正在运行的实例。
- **会话中途被强停**（暂停服务、重新部署、容器被杀，都不做收尾）：已提交状态保留、预算不退；未提交的响应、最新 cookie、熔断事件可能丢失；续跑可能重做未完成单元；五分钟是租约失效上界，不是恢复时间，通常要等下一次半小时触发。当晚的数据因此可能少一截；续跑的 `window_end` 与任务清单不变（D23）。

## 8. CI

`.github/workflows/pick-workbench-tests.yml` 的 push 与 pull_request 触发路径加了：`deploy/pick-obs/**`、根目录的 `railway.toml`、`.dockerignore`、任何一级的 `.gitignore` 与 `.railwayignore`（`**/.gitignore`、`**/.railwayignore`：镜像里的 hatchling 按 `backend/.gitignore` 排除文件，`railway up` 读各级 `.gitignore` 与 `.railwayignore`，第 1 节的两个测试依赖它们）、`docs/pick-workbench/observe-runbook/**`（本页的测试会读这些文件），以及 TR-34 的 `scripts/pick-deploy-guard.py`、`scripts/_pick_deploy_guard_readers.py`（守卫的测试在 `customizations/pick-workbench/tests/test_deploy_guard.py`）与 `scripts/pick-railway-settings.py`（第 3.3 节，测试在 `tests/observe/test_railway_settings.py`），还有 G3 文档对齐加的本计划 `docs/plans/2026-09-25-trends-radar-impl-plan.md` 与 `frontend/tests/unit/core/pick/api.test.ts`（`tests/observe/test_rollout_plan.py` 读它们，`test_ci_runs_on_the_files_these_tests_read` 钉住）。

「Lint pick deploy scripts」步骤（原名 Lint pick deploy guard）对守卫的两个脚本与 `pick-railway-settings.py` 跑 `ruff check` 与 `ruff format --check`，用 ruff 的默认配置（仓库根没有 ruff 配置，88 列；三个脚本按这个配置写成）。脚本名写死在步骤里，改名或删掉任何一个，这一步就失败。TR-15 分支单独不含这两个脚本（它们在 TR-34 的分支里），TR-15 只经已含 TR-34 的 `feat/trends-radar` 进 main，所以步骤里不留「文件不在就跳过」的分支。`test_ci_runs_on_the_deploy_files` 钉住触发路径与这一步。

## 9. 测试

`customizations/pick-workbench/tests/observe/test_railway_config.py`：

| 测试 | 钉住什么 |
|---|---|
| `test_trends_railway_config_pinned` | cron 配置逐字段相等，多一个键也失败；`[build]` 与根目录相同 |
| `test_selfcheck_config_pinned` | 自检配置逐字段相等；启动命令与 cron 同一个目录与程序，先 `--selfcheck-only`，通过了再 `exec` 跑 `preflight` |
| `test_root_railway_untouched` | 根目录 `railway.toml` 原样 |
| `test_dockerfile_cmd_untouched` | 镜像的 `CMD` 仍是 gateway 的，没有 `ENTRYPOINT` |
| `test_start_command_runs_in_the_image` | 启动命令的目录与 `python` 是镜像里 backend 的那一份 |
| `test_cron_schedule_fits_the_session_modes` | 触发时刻与三个模式的起跑、截止对得上（第 3.1 节）；相邻触发间隔等于容量估算用的 30 分钟；UTC 02:00–17:00 没有触发也没有会话（第 7 节），本页不再出现旧的部署时段 |
| `test_railway_up_uploads_the_configs` | 两份配置不被 `.gitignore` 排除（`railway up` 不上传被排除的文件）；仓库里没有 `.railwayignore`（加了就要在这里对着它核对） |
| `test_packaged_files_reach_the_image` | 包里每个文件都进得了构建上下文与 wheel |
| `test_ci_runs_on_the_deploy_files` | 第 8 节的触发路径与 lint 步骤 |
| `test_runbook_never_prints_a_generated_secret` | 本页生成密钥的命令都送进剪贴板，不打在终端上 |
| `test_runbook_variables_are_the_ones_the_service_reads` | 第 5 节变量表的名字 |
| `test_trends_entrypoint_argv` | 启动命令里的参数解析成一次 `run`；自检配置的两步分别解析成只做自检与 `preflight`；`status` 与不带参数也能解析；写错的子命令以 2 退出且不回显 |
| `test_trends_entrypoint_argv_selfcheck_only` | 从自检配置读出启动命令，经真的 `/bin/sh`（`&&` 与 `exec` 照原样）、每一步经真实的 `__main__`（新解释器，像 `python -m` 那样运行）在两种库上：退出 0，先自检行、后预检行（今晚的 target_date、负载闸门通过），摘要等于源码的 `package_digest()`、零 HTTP、不取租约、不写批次；子进程的时钟在会话窗口里，参数若落到 `run` 就会取租约而失败 |
| `test_selfcheck_config_exits_2_when_tonight_is_not_a_canary` | S6a：镜像没问题而今晚的任务清单不够金丝雀的负载（剧目都早于 14 天、正对照一个不在批次里）：两行都打，预检行写明两条原因，部署以 2 退出，不写任何行、不取租约 |
| `test_trends_entrypoint_argv_selfcheck_only_refuses_a_bad_image` | 反例 15 在 S6 的样子：采集合同版本不符以 2 退出，只点变量名，不打自检行，预检也不跑 |

`customizations/pick-workbench/tests/observe/test_cron_deploy_procedure.py`（TR-34 的守卫不在树里时跳过）：

| 测试 | 钉住什么 |
|---|---|
| `test_block_deploys_the_selfcheck_then_the_cron` | 第 5 节第 4 步的代码块：守卫在前、首次带 `--first-record`；先 `apply` 自检配置并部署，再 `apply` cron 配置并部署，两份配置都存在，每行 `apply` 都能按脚本的参数解析；记录行只有一次，在最后 |
| `test_first_deploy_passes_the_guard` | 在没有 `cron:trends` 记录的 progress.md 上照写执行：两次部署都是守卫通过的那个提交，记录行记的也是它 |
| `test_later_deploys_pass_the_guard` | 第一条记录推到 main 之后，去掉 `--first-record` 从更新的 main 再走一遍（第 7 节） |
| `test_a_rerun_before_the_record_passes` | 第二次部署之前按原样重跑守卫，首次与以后都通过 |

`customizations/pick-workbench/tests/observe/test_railway_settings.py`：两份配置读成期望值（多出的键、不是 `DOCKERFILE` 的构建方式、缺字段都以 2 拒绝）；`apply` 写全四项（`healthcheckPath` 清空、自检配置写空的 `cronSchedule`）并以 `--skip-deploys` 设 Dockerfile 变量，不部署；`check` 逐项点出差异且不打印任何变量值；`--deployment` 比对部署清单；railway 调用都带 `-p/-e/-s`；railway 失败、服务找不到、文件不在、用法错都以 2 退出；`railway_cli` 失败时只转述 stderr、超时或找不到命令也归为 railway 失败；`variable list` 失败时不转述它说的任何话；答复不是 JSON、为空、项目或部署为 null、GraphQL 报错、`serviceInstanceUpdate` 不是 true 都以 2 退出而不打调用栈；`apply` 写了一半时报错点名没写成的 Dockerfile 变量。

通用的辅助函数（读配置、把启动命令拆成各步、解析 cron 字段、`.dockerignore` 与 git 的 ignore 规则、像 Railway 那样经 `/bin/sh` 跑启动命令而每一步在新解释器里经真实的 `__main__` 运行、解析 `selfcheck ok` 行）在 `tests/observe/railway_helpers.py`，TR-21 的 gsc 测试复用。

## 10. 与其他任务的接缝

- **TR-05**：`observe/trends/canary_controls.json` 归 TR-05，本任务不建也不改；测试用自己的夹具副本（子进程里把 `canary.DEFAULT_CONTROLS_PATH` 指向副本）。第 4 节的前提（包里的清单每个 geo 都有市场序列）由 TR-14 的 `test_trends_units.py::test_packaged_controls_file_when_present` 钉住：清单缺序列时它是红的，S6 的自检也会以 2 退出。
- **TR-21**：gsc 的 `deploy/pick-obs/gsc/railway.toml`（每 3 小时第 25 分，10 分钟硬截止）、它的部署验证与变量表写进 TR-21 自己的手册页，不写进本页：本页第 5 节的变量表由 `test_runbook_variables_are_the_ones_the_service_reads` 按 trends 服务读的变量核对，gsc 的变量写进来会让它失败。`test_gsc_railway_config_pinned`、`test_gsc_entrypoint_argv` 可以复用 `tests/observe/railway_helpers.py`（`railway_toml`、`module_argv`、`start_steps`、`cron_field`、`dockerignore_rules`、`docker_excluded`、`git_ignored`、`run_start_command`、`selfcheck_report`）。要不要同样配一份自检配置由 TR-21 定；配了的话，部署顺序照第 5 节第 4 步写。CI 的 `deploy/pick-obs/**` 已经覆盖。
- **TR-34**：守卫的 cron 模式要求 `deploy/pick-obs/<服务>/railway.toml` 在 main 上；守卫脚本的触发路径与 lint 由本任务加（第 8 节）。第 5 节第 4 步的顺序按守卫的记录规则写（`--first-record` 只在没有记录时带、记录行推送之前 HEAD 不动），`test_cron_deploy_procedure.py` 用守卫本身重放它：守卫的规则变了，那个测试先红。`deploy-guard.md`「各模式通过之后」的 cron 一条原写「部署后以 `--selfcheck-only` 手动触发一次（S6）」，与本页的做法（第 3.2 节：临时换成自检配置）不一致，已由 G3 文档对齐改成指向本页第 5 节第 4 步与第 6 节。2026-09-28 Railway 不再读配置路径之后，那一条的「配置路径」措辞随本页一起改成「用 `pick-railway-settings.py apply` 写服务设置」。
- **计划**：第 10 节 S5、S6、S6a 已由 G3 文档对齐按本页改写，S6 与 S6a 里预检随自检部署一起跑的写法由 G3 集成补齐（先部署自检配置、读自检与预检两行，再切回 cron 配置；第 3.2 节的理由），命令、运行方式与判据以本页与 `trends-session.md` 为准。
- **TR-29**：目录页 `README.md` 里本页的状态由 TR-29 更新（D35，本任务不改目录页）。
