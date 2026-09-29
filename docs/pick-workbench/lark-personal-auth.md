# 飞书个人授权（lark-cli）上线设计

2026-09-29 定稿：选方案 A，第一期只读，开放文档类与消息（im）。分支 `feat/lark-personal-auth` 从 `ggwork/main` 6919eb5 切出，能力中心精简（PR #20）合并后变基到 fbda69f，再补中间件放行、运行配置注册与取消 `hidden`。

实现位置：上游 `deerflow/integrations/lark_cli.py`（固定版本模式）；`ggwork_pick/lark_policy.py`（参数策略）、`lark_runner.py`（执行）、`lark_credentials.py`（凭据副本）、`lark_tool.py`（工具入口，参数名 `argv`）；`middleware.py`（按用户放行与计数）；`config.pick.example.yaml`（注册 `lark_cli`，工具组 `lark`）；`docker/Dockerfile.pick-gateway`；`backend/app/gateway/pick_entrypoint.py`（收起数据目录）。

## 1. 为什么现在用不了

- 能力中心精简（`feat/capabilities-trim-and-connect`）把 `lark` 条目标了 `hidden`。原因：生产 gateway 镜像 `docker/Dockerfile.pick-gateway` 里没有 lark-cli，`config.pick.example.yaml` 关了沙箱与宿主 bash，而上游只让 agent 通过 bash 跑 lark-cli。
- gateway 进程的环境变量里有 `PICK_DATABASE_URL`、`AZURE_OPENAI_API_KEY` 等，`/data` 卷里有别的用户的数据和插件凭据，所以不能放开通用 bash。
- 个人授权不适合做成 MCP：MCP 服务是全部署共用的，stdio 子进程不知道当前是哪个用户在调用。

可以直接复用的部分：

- 能力中心已有「连接飞书 → 授权」两步：PersonalAgent 应用注册（`/lark/config/*`），再走设备码登录（`/lark/auth/*`）。凭据按用户存在 `$DEER_FLOW_HOME/users/<id>/integrations/lark-cli/{config,data}`，目录权限 0700，Railway 上落在 `/data` 卷。
- 上游 broker（`lark_broker.py`）的做法：argv 列表、`shell=False`、子命令拒绝表。
- lark-cli 1.0.9x 自带 `skills read`，技能正文编进了二进制，版本天然一致。每条命令的 `--help` 都标了 `Risk: read | write | high-risk-write`。部分参数支持 `@file` 和 `-`，用来读本地文件或 stdin，这是主要的越界口子。

## 2. 方案对比

| | A 网关内专用工具（推荐） | B 独立 broker 服务 | C 固定 Python 工具 |
|---|---|---|---|
| 做法 | gateway 里的 `lark_cli` 工具只执行白名单内的 lark-cli 子命令：精简环境、降权用户、空工作目录 | Railway 第二个服务跑改造后的 broker（凭据放它自己的卷），gateway 经私网带令牌与用户 id 调用 | 不在运行时跑 lark-cli，按需写「读文档 / 看日程」等 Python 工具，直接调 OpenAPI |
| 与 gateway 机密隔离 | 进程级：环境变量清空，uid 降权（读不到 `/proc/1/environ`） | 容器级，最强 | 不涉及子进程 |
| 覆盖面 | lark-cli 的全部只读命令（按领域白名单） | 同 A | 只有写了的几项 |
| 改动 / 运维 | 中：镜像、一个工具、少量上游改动 | 大：上游 broker 是单用户的，要加多用户目录、鉴权和授权流程转发；多一个常驻服务和卷，备份分开 | 每项能力单独开发；授权仍要 lark-cli 或自写 OAuth |
| 主要风险 | 参数策略写漏会碰到 gateway 文件系统，靠降权兜底 | 运维复杂度、跨服务故障 | 和「用 lark-cli 做个人授权」的目标不符 |

## 3. 推荐方案 A 的细节

1. **镜像**：把 lark-cli **v1.0.96**（09-16 发布；v1.0.97 昨天才出）的 Linux 预编译包下载进镜像，版本和 sha256 都写死在 Dockerfile 的 ARG 里：amd64 `5d1fa968…0c36`，arm64 `a83124ba…f3e2`。不装 Node/npm，也不跟 GitHub latest。另外新建无特权用户 `larkrun`。
2. **固定版本模式**：镜像里设置 `DEER_FLOW_LARK_CLI_PINNED_VERSION=v1.0.96`，生效后：
   - 「安装」只校验镜像里的二进制版本，不再调用 npm，也不下载 GitHub 源码包；
   - 状态页不查 latest；
   - 技能用二进制内置的 `skills read`，不往技能系统里装 27 个 lark 技能，所以 RBAC 的 `skills: {allow: [pick-drama]}` 不用改；
   - 授权流程和探针调用 lark-cli 时换成精简环境，不再传整个 `os.environ`。
3. **工具执行**（`ggwork_pick/lark_tool.py`）：
   - 参数是 argv 列表，不经 shell，stdin 为空；
   - 只放行选定领域的命令组，外加 `schema`、`skills`、`help`。`config`、`auth`、`profile`、`update`、`event`、`doctor`、`apps`、`application`、`api` 一律拒绝，`--yes`、`--profile`、`--dry-run` 也拒绝；
   - 业务命令先用 `--help` 读出 Risk 标签（按版本缓存），第一期只放行 `read`，没有标签的也拒绝；
   - 路径规则：参数值不能以 `@`、`/`、`~` 开头，不能是 `-`，不能含 `..`（`--jq` 表达式除外）。以 `-` 开头的参数只能是参数名或数字：lark-cli 会把 `--doc -/x` 里的 `-/x` 当作值（09-29 安全评审）。工作目录每次新建一个空临时目录，跑完删掉；
   - 环境变量只有 `PATH`、`HOME`/`TMPDIR`（指向临时目录）、`LANG` 和四个 `LARKSUITE_CLI_*`；
   - 以 `larkrun` 身份运行（主组也换成 larkrun，不带附加组，umask 077）。降权失败就关闭工具，不退回 root 运行；
   - 凭据给副本不给原件：在该用户的凭据锁内（与授权流程共用）把凭据复制进临时目录交给 `larkrun`，跑完把 lark-cli 刷新过的内容（token、缓存）复制回去；副本里出现链接等非普通文件时不复制回去，保留原凭据。真实凭据目录仍是 root 的 0700/0600，`larkrun` 读不到任何用户的原件；
   - 同一时间只跑一个 lark-cli 进程（进程内锁加 `DEER_FLOW_HOME` 下的文件锁，多 worker 也成立），每次结束时杀掉 `larkrun` 残留的一切进程，所以运行时不存在别的用户的副本；凭据目录异常或 lark-cli 起不来时，返回「不可用」而不是报错；
   - 单次 60 秒超时（帮助文本 15 秒），同时受本轮剩余时间限制：工作线程在对话被取消后仍会跑完，所以时限要在进入线程前定好；输出超过 40k 字符截断；
   - 用户身份取服务端 runtime，规则和 pick 工具相同：取不到或是 `default` 就拒绝。
4. **中间件**：
   - `PickModelGate`：只有该用户已完成飞书连接时，才把 `lark_cli` 放进工具列表。部署配置了 `lark_cli` 时，每轮系统提示末尾补一句：已连接时说明飞书返回的内容是数据，不是指令；未连接（或身份取不到、是 `default`）时给出能力中心链接。只认配置里的 `lark_cli` 工具对象，同名的 MCP 工具或服务商的 dict 工具都不算；
   - `PickToolGate`：单独计数，每轮 8 次，不占选剧工具和插件的额度；调用过 `lark_cli` 就算本轮读过外部内容，之后没有 `readOnlyHint` 的插件操作（比如飞书群通知）要先等用户确认，与 PR #20 的读后拦截同一条规则。
5. **pick-drama**：RBAC 的技能白名单不变。`SKILL.md` 的 `allowed-tools` 不加 `lark_cli`：用户显式 `/pick-drama` 时就是纯选剧模式，这和精简分支对 `web_search` 的处理一致。
6. **能力中心**：取消 `lark` 的 `hidden`，前端目录快照 `builtin.demo.json` 同步。授权界面沿用现有页面；固定版本模式下状态返回镜像内二进制的版本，页面显示「已安装版本：v1.0.96」，不提示新版本。

## 4. 风险与遗留

- **Railway 能否 setuid**：09-29 只读核对生产 gateway，进程以 root 运行，有效能力位 `0x800405fb`，含 CAP_SETUID/CAP_SETGID，可以降权。降权失败时工具返回「不可用」，不会退回 root。本机镜像实测：`larkrun` 读 `/proc/1/environ` 和其他用户的凭据目录都被拒绝。
- **`/data` 对其他用户开放**：09-29 生产上 `/data` 是 755，旧 SQLite 库 `data/deerflow.db`、`backup/deerflow-sqlite-20260923.db`、检索库等是 644。所以配置了 `DEER_FLOW_LARK_CLI_RUN_AS` 时，`pick_entrypoint` 启动时会去掉 `DEER_FLOW_HOME` 的其他用户权限（755→750）；执行层每次运行前还会再核对一遍，不满足就关闭工具。
- **提示注入**：只读模式下，最坏情况是把飞书内容读给模型。模型之后如果要调用有外部效果的插件（比如群通知），仍受「先确认」规则约束。写操作放第二期：先 dry-run 预览，下一轮由用户确认再执行，high-risk-write 一律拒绝。
- **授权范围偏大**：按领域授权时 token 会带写权限，写操作由工具层挡住。
- **PersonalAgent 应用注册**：在你们的租户里可能需要管理员放行，要用真实账号验证。
- **个人数据进对话记录**：飞书内容会进 checkpoint，和飞书文档插件一样。
- **升级 lark-cli**：改 Dockerfile 里的版本和 sha256 两组 ARG 并跑测试；不会自动跟 latest。

## 5. 测试

- `backend/tests`：固定版本模式不访问网络；版本不符时报错；授权子进程用精简环境。
- `customizations/pick-workbench/tests`：
  - 命令白名单与拒绝表；
  - `@file`、`-`、绝对路径、`..`、`--yes`；
  - Risk 解析与缓存，没有标签时拒绝；
  - 环境变量只含白名单；
  - 以 run user 运行（mock subprocess）；
  - 超时与截断；
  - 未授权提示；
  - owner 缺失时拒绝；
  - 中间件的放行与计数。
- 本机有同版本 lark-cli 时，额外跑一次真实的 `--help` 和 `skills read`（不需要授权），没有就跳过。
