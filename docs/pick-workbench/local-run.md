# 个人选剧工作台：本地运行

本项目固定上游提交 `29d285731b326a728a9df33d3641f73b68bbe48b`。选剧业务正在实施，不能仅凭原版工作台打开就认为选剧闭环完成。

## 当前配置

- 当前可用原生入口：`http://localhost:3007`；Docker候选入口为 `http://localhost:2027`，尚未启动。
- 模型：宿主机 Ollama，`qwen3:8b`；当前主机已下载完成；向量模型不能替代对话模型。
- Gateway：一个 worker，SQLite 持久化，数据目录 `backend/.deer-flow/`。
- 保留用户认证，关闭自助注册；首次管理员在原版 setup 页面建立。
- 使用单独 Compose 项目 `ggwork-pick`，不使用其他工作台容器和端口。
- 业务数据、知识与环境文件不进入 Git。

## 首次准备

1. 将 `config.pick.example.yaml` 复制为未跟踪的 `config.yaml`。
2. 创建未跟踪的 `extensions_config.json`，初始内容为 `{"mcpServers":{},"skills":{}}`。
3. `.env` 中设置 `PICK_PORT=2027`、随机生成的 `BETTER_AUTH_SECRET` 和 `DEER_FLOW_INTERNAL_AUTH_TOKEN`。不要复用生产凭据。
4. `ollama pull qwen3:8b`，完成后再做实际模型验收。
5. Docker 配置中的 Ollama 地址为 `http://host.docker.internal:11434`；原生 Gateway 调试时设置 `PICK_OLLAMA_BASE_URL=http://127.0.0.1:11434`。

```bash
# 仓库根目录。当前主机提供 standalone docker-compose，未注册 docker compose 插件。
docker-compose --env-file .env -p ggwork-pick -f docker/docker-compose.pick.yaml config --quiet
docker-compose --env-file .env -p ggwork-pick -f docker/docker-compose.pick.yaml up -d --build
docker-compose --env-file .env -p ggwork-pick -f docker/docker-compose.pick.yaml ps
```

安装有 Docker Compose 插件的主机可以将 `docker-compose` 替换为 `docker compose`。前端沿用上游 Dockerfile；Gateway 使用 `docker/Dockerfile.pick-gateway` 的 Python-only 构建，没有 Redis、Docker socket、Feishu CLI 或外部写入配置。不要对其他项目执行 stop/down/prune。

上游 Dockerfile 使用 BuildKit 的缓存挂载。当前主机已经安装 `docker-buildx`；为避免修改全局 Docker 配置，使用本项目专属 CLI 配置（其中仅有 `cliPluginsExtraDirs: ["/opt/homebrew/lib/docker/cli-plugins"]`）：

```bash
task_docker_host=$(docker context inspect --format '{{.Endpoints.docker.Host}}')
DOCKER_CONFIG="$PWD/backend/.deer-flow/docker-cli" \
DOCKER_HOST="$task_docker_host" DOCKER_BUILDKIT=1 COMPOSE_DOCKER_CLI_BUILD=1 \
docker-compose --env-file .env -p ggwork-pick -f docker/docker-compose.pick.yaml build
```

必须在 extension-upgrade 完成后构建最新镜像；旧构建不会自动包含后来更新的托管副本。构建通过后再按上面的 up 命令启动，且不能与原生Gateway同时使用同一个SQLite目录。

## 验证

依次验证 Gateway 健康、个人登录、真实模型回复、调用受控工具、刷新恢复、Gateway 重启恢复。模型未下载完时，登录/页面可以单独检查，但对话验收仍是未通过。

`frontend/playwright.config.ts` 的常规 E2E 有模拟网络；既有 real-backend 套件也使用回放模型。需要另外完成本期保留认证与真实本地模型的验证。

### 扩展单元测试（SQLite 与 PostgreSQL）

用 `pick_db_url` 夹具的用例在两种方言上各跑一遍；`PICK_TEST_PG_URL` 未设时 PG 那一份跳过。CI（`.github/workflows/pick-workbench-tests.yml`）用 `postgres:17` service 跑两种方言。本机复现：

```bash
# 仓库根目录。只指向一次性的本机库：用例会在上面建删数据库和角色。backend/.venv 需装 --extra postgres 的依赖。
docker run --rm -d --name pick-pg -e POSTGRES_PASSWORD=<本机随机口令> -p 5433:5432 postgres:17
PICK_TEST_PG_URL=postgresql://postgres:<本机随机口令>@127.0.0.1:5433/postgres \
  backend/.venv/bin/python -m pytest customizations/pick-workbench/tests -q
```

URL 用 libpq 格式、不带查询参数。每个会话先建一个迁移到 head 的模板库，每个用例从模板复制一个独立的库（search_path 为 `deerflow`，与生产一致），并建一个带随机后缀的只读角色、写进 `PICK_MIRROR_READER_ROLE`，用例结束时都删掉。

## 当前原生调试入口

Docker基础镜像下载期间，已启动相同配置的原生Gateway（127.0.0.1:8007）和生产前端（localhost:3007）。浏览器可打开 `http://localhost:3007/setup` 设置个人管理员。首次凭据由用户在浏览器亲自设置。

前端Next代理超时设为180秒，以覆盖本地模型较慢的首轮推理；刷新断线显式继续后台运行，停止按钮仍发起取消请求。

原生Gateway使用根目录config.yaml，环境中明确指定 `DEER_FLOW_PROJECT_ROOT`、`DEER_FLOW_HOME`、`DEER_FLOW_CONFIG_PATH` 和 `PICK_OLLAMA_BASE_URL=http://127.0.0.1:11434`。前端 `.env` 设置 `DEER_FLOW_INTERNAL_GATEWAY_BASE_URL=http://127.0.0.1:8007`。**该值要在 pnpm build 时存在**，因为Next的API rewrites编译进构建产物；只在pnpm start时更改会连接旧端口。

此调试入口不代表Docker验收已经完成。两种运行方式不得同时写入同一SQLite目录；切换到容器前停止此项目的原生Gateway，并备份数据。

## Python 依赖下载故障

默认命令为 `cd backend && uv sync --locked --extra ollama`。本次网络中 `files.pythonhosted.org` 出现连接/下载超时，先采用更长超时及更低并发；若仍失败，可从锁文件导出含哈希的完整依赖清单，用可访问镜像下载同版本分发包：

```bash
# backend/，不改锁文件版本；导出所有依赖并保留平台 markers/hash。
uv export --locked --format requirements-txt --extra ollama \
  --no-emit-project --no-emit-workspace --output-file /tmp/ggwork-locked-requirements.txt
UV_HTTP_TIMEOUT=180 UV_CONCURRENT_DOWNLOADS=4 uv pip install \
  --no-config --no-deps --python .venv/bin/python \
  --index-url https://mirrors.cloud.tencent.com/pypi/simple \
  --require-hashes -r /tmp/ggwork-locked-requirements.txt
```

`--no-deps` 仅因清单已完整展开并固定版本，避免上游 websockets override 被重新解析；不允许在缺少完整导出清单时这样安装。该步骤不安装工作区包，随后需要将本仓库的 extension-api/harness 安装到同一环境，并核对与锁文件一致。容器构建有独立依赖环境，不受宿主安装成功自动保证。

## 业务插件的正式安装与更新

当前已使用官方管理器完成安装。源目录是 `customizations/pick-workbench`，`backend/extensions/sources/ggwork-pick` 为管理器生成的快照；运行环境使用已安装wheel。

```bash
# backend/；首次使用install，后续使用upgrade，不手改托管副本。
DEER_FLOW_CONFIG_PATH="$PWD/../config.yaml" UV_EXTRAS=ollama \
.venv/bin/deerflow extensions upgrade "$PWD/../customizations/pick-workbench" --yes
```

更新后重启Gateway，并核对源码、托管副本与site-packages三处业务Python文件一致。独立迁移当前到0002，升级后的旧数据库历史查询/个人选择保留。


## 策略中间件与候选引用

`plugins:` 负责service、router、task lifecycle；真正的策略必须在 `extensions.middlewares` 显式配置 `ggwork_pick.middleware:PickModelGate` 与 `ggwork_pick.middleware:PickToolGate`。不能用 `registry.middlewares` 替代：该入口按上游观察插件语义忽略请求改写，并隔离普通异常。

宿主 `app/gateway/services.py` 有一处小型接入修改：将用户提交的 `pick_reference` 深拷贝到本轮runtime context，既不赋予权限，也不写入checkpoint configurable。扩展仍逐次检查owner/thread/item归属。保存序号由服务端映射，避免小模型抄错长ID。

LangGraph步数设为1000，因为宿主每轮有多层图节点；实际模型调用≤12、业务工具执行≤8由配置中间件控制。Gateway启动时设置 `PICK_RUN_TIMEOUT_SECONDS=120`。运行层watchdog从worker入口开始覆盖preflight、模型并发等待、重试退避和Agent执行，截止时记录timeout。数据库和检查点的终态收尾仍完成后才结束响应，不强杀正在提交的写入。模型/工具中间件预算继续作为内层限制。

## 独立验收和备份

`frontend/playwright.pick.config.ts` 使用真实本地模型、保留身份认证，默认目标为独立QA前端3008。提供仅用于QA的 `PICK_E2E_EMAIL` / `PICK_E2E_PASSWORD` 后执行 `pnpm exec playwright test -c playwright.pick.config.ts`。它会导入合成剧库，不能指向个人业务实例。未配置账号时明确skip；不能把skip计为通过。

停止对应Gateway后，使用下面的命令复制完整home并核验SQLite与原始导入资料：

```bash
python scripts/pick-backup.py /absolute/path/to/home /absolute/path/to/new-backup --gateway-stopped
```

备份含账号数据与本机签名密钥，必须保持私有。根config.yaml、extensions_config.json和环境配置在home之外，需要另行私有备份。恢复时停止Gateway，把旧home保留在另一位置，将核验过的备份恢复到**原绝对路径**，再启动并核对资料、候选、备注及回执。不要把备份目录和在线目录同时作为可写实例运行。

当前Docker磁盘为59GB且剩余0，构建未完成；不得通过关闭签名校验绕过apt报错，也不要为本任务prune其他业务镜像/卷。


原生前端用 `cd frontend && PORT=3007 pnpm start --hostname 127.0.0.1` 启动，明确限制为本机地址。生产构建已验证Next代理超时180000毫秒。QA开发前端已在本轮自动验收完成后停止，复测时请在独立端口启动并同样指定loopback绑定。


启动原生Gateway时必须同时提供 `PICK_RUN_TIMEOUT_SECONDS=120`，例如：

```bash
# backend/；DEER_FLOW_*路径仍按上文指定。
PICK_RUN_TIMEOUT_SECONDS=120 PICK_OLLAMA_BASE_URL=http://127.0.0.1:11434 \
.venv/bin/python -m uvicorn app.gateway.app:app --host 127.0.0.1 --port 8007
```

期限只取服务端启动环境；请求context/config里的同名值不会放宽预算。该变量未设置时保留上游行为，因此复现个人工作台时不要省略。Docker pick Compose已固定设为120。独立真实Gateway以2秒期限实测，客户端尝试9999秒仍在2.19秒进入timeout，额外时间为必要收尾。
