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

`test_bootstrap_sql.py` 用 PATH 上的 `psql` 执行 `docs/pick-workbench/supabase/` 下的脚本（任意较新的 psql 客户端即可；没有时该文件的 PG 用例直接失败，不会跳过）。它以一个 `NOSUPERUSER CREATEROLE`、持有独立测试库的替身角色登录，模拟 Supabase 的 `postgres`，脚本里的角色名和库名都换成带随机后缀的名字，结束时删掉。

### 选剧资料页 e2e（本机 QA 实例）

`frontend/tests/e2e-pick/pick-data-board.spec.ts` 用真的 gateway、镜像库和生产构建打开 `/workspace/pick-data` 的每个 tab，并发 10 个交替 `v=` 的请求，确认每个请求读到自己的版本（单测替身抓不到的：版本作用域漏到某个嵌套组件、两个请求共用一个作用域）。同一个实例再跑 `personal-selection.spec.ts`（真模型：本机 Ollama 的 `qwen3:8b`）。只对 localhost；数据全是 `board_fixture.py` 造的合成数据和用例自己导入的合成剧。缺账号或缺 `PICK_BOARD_FIXTURE_JSON` 时整套 skip，**skip 不算通过**。

要点：

- **gateway 和镜像同库**（和生产一样）：`board_fixture.py up` 建库、跑 gateway 的启动迁移、发布版本；gateway 的 `PICK_DATABASE_URL` 指向同一个库，所以 `/sync` 的 `mirror` 版本号就是页面上的版本号。
- **读连接走 TLS 并校验 CA**：前端生产路径不接受明文（`NODE_ENV=test` 以外），所以 PG 要开 SSL，用自签 CA。不要去改别人共用的集群（例如后端测试用的 55461 那个 `ssl=off`），另起一个一次性的。
- 以下 `$d` 是仓库外的一次性目录，证书、PG 数据、URL 文件、账号文件都在里面，全程 `umask 077`，跑完整个删掉。`$W` 是要测的检出（worktree 根），`$PY` 是装了 `--extra postgres --extra ollama` 的 backend 虚拟环境的 python。

```bash
d=<scratch>/pick-e2e W=<检出根> PY=<backend venv>/bin/python
mkdir -p "$d/tls" && chmod 700 "$d" && umask 077

# 1. 自签 CA 与服务端证书（IP SAN 要和读连接的主机一致）
cd "$d/tls"
openssl req -x509 -newkey rsa:2048 -nodes -days 7 -subj "/CN=pick-e2e-ca" -keyout ca.key -out ca.pem
openssl req -newkey rsa:2048 -nodes -subj "/CN=127.0.0.1" -keyout server.key -out server.csr
printf 'subjectAltName=IP:127.0.0.1,DNS:localhost\n' > san.ext
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial -days 7 -extfile san.ext -out server.crt

# 2. 一次性的 PG 17 集群，开 SSL：superuser 只在回环上免密，其余角色（读角色）只能 hostssl + SCRAM
B=$(brew --prefix postgresql@17)/bin
"$B/initdb" -D "$d/pgdata" -U postgres --auth-local=trust --auth-host=scram-sha-256 -E UTF8 --locale=C
printf 'host all postgres 127.0.0.1/32 trust\nhostssl all all 127.0.0.1/32 scram-sha-256\n' > "$d/pgdata/pg_hba.conf"
cat >> "$d/pgdata/postgresql.conf" <<EOF
port = 55481
listen_addresses = '127.0.0.1'
unix_socket_directories = ''
ssl = on
ssl_cert_file = '$d/tls/server.crt'
ssl_key_file = '$d/tls/server.key'
log_connections = on
EOF
"$B/pg_ctl" -D "$d/pgdata" -l "$d/pg.log" -w start
PG=postgresql://postgres@127.0.0.1:55481/postgres

# 3. 建库和镜像版本；--data-dir 就是 gateway 的 pick 数据目录（DEER_FLOW_HOME/pick）
cd "$W/customizations/pick-workbench"
PICK_TEST_PG_URL=$PG "$PY" tests/mirror/board_fixture.py up \
  --url-file "$d/reader.url" --data-dir "$d/home/pick" > "$d/board.json"
DB=$(jq -r .database "$d/board.json") ROLE=$(jq -r .role "$d/board.json")
```

没有 Homebrew 的 PG 时，第 2 步可以换成 docker（端口和口令相应改进 `PG`）：`docker run --rm -d --name pick-e2e-pg -e POSTGRES_PASSWORD=<本机随机口令> -p 5434:5432 -v "$d/tls":/tls:ro --entrypoint sh postgres:17 -c 'install -o postgres -m 600 /tls/server.key /tmp/server.key && exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tls/server.crt -c ssl_key_file=/tmp/server.key'`（私钥在容器里拷一份改属主，postgres 不接受别人的私钥）。

`board.json` 只有库名、角色名、数据目录和版本号（dropped / v1 / v2 / building / failed），没有口令；读连接只在 `reader.url`（0600）里。

**4. gateway。** 不用 `pick_entrypoint`：它每次都从 `config.pick.example.yaml` 重写运行时 yaml，模型段是 Azure（本机不发外网请求），`personal-selection` 要真模型。所以自己生成一份：和入口写的一样（同一个 `POSTGRES_DATABASE` 段），只把模型换成 Ollama 的 `local-qwen`，RBAC 的模型白名单跟着换；然后照入口的做法起 `app.gateway.pick_asgi:app`。

```bash
cd "$W/backend"
"$PY" - "$d/home" <<'EOF'
import json, sys
from pathlib import Path
import yaml
from app.gateway.pick_entrypoint import POSTGRES_DATABASE, PROJECT_ROOT
home = Path(sys.argv[1])
config = yaml.safe_load((PROJECT_ROOT / "config.pick.example.yaml").read_text())
model = {"name": "local-qwen", "display_name": "本地 Qwen3 8B", "use": "langchain_ollama:ChatOllama", "model": "qwen3:8b",
         "base_url": "$PICK_OLLAMA_BASE_URL", "reasoning": False, "temperature": 0.1, "num_ctx": 16384, "num_predict": 2048,
         "context_window": 16384, "supports_thinking": False, "supports_vision": False}
provider = config["authorization"]["provider"]
roles = {name: {**role, "models": {"allow": ["local-qwen"]}} for name, role in provider["config"]["roles"].items()}
config = {**config, "models": [model], "database": dict(POSTGRES_DATABASE),
          "authorization": {**config["authorization"], "provider": {**provider, "config": {"roles": roles}}}}
(home / "pick-runtime.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
(home / "extensions_config.json").write_text(json.dumps({"mcpServers": {}, "skills": {}}))
EOF
export PYTHONPATH=$W/backend/packages/harness:$W/backend/packages/extension-api:$W/customizations/pick-workbench:$W/backend
export DEER_FLOW_HOME=$d/home DEER_FLOW_PROJECT_ROOT=$W
export DEER_FLOW_CONFIG_PATH=$d/home/pick-runtime.yaml DEER_FLOW_EXTENSIONS_CONFIG_PATH=$d/home/extensions_config.json
export PICK_DB_BACKEND=postgres PICK_DATABASE_URL=postgresql://postgres@127.0.0.1:55481/$DB PGSSLMODE=require
export PICK_MIRROR_READER_ROLE=$ROLE PICK_RUN_TIMEOUT_SECONDS=120 PICK_OLLAMA_BASE_URL=http://127.0.0.1:11434
"$PY" -m uvicorn app.gateway.pick_asgi:app --host 127.0.0.1 --port 8011 --workers 1 > "$d/gateway.log" 2>&1 &
curl -s http://127.0.0.1:8011/health/ready    # {"status":"ready",...}
grep -c '/api/pick/replay' "$d/gateway.log"   # 1：挂上的是本检出的 ggwork_pick
```

- `PYTHONPATH` 必须有：backend 虚拟环境是几个检出共用的，里面的 `ggwork_pick` 是装进去的 wheel（可能早于镜像代码），harness 是指向主检出的 editable 安装；不设就跑的不是要测的代码，也可能读到主检出的 `.env`。日志里「Extension routers mounted」那一行要有 `/api/pick/replay`。
- 端口用 8011，避开别的 gateway；它要和第 6 步 build 时的 `DEER_FLOW_INTERNAL_GATEWAY_BASE_URL` 一致。不设 feed token，不会触发同步。

**5. 账号。** 管理员走 `POST /api/v1/auth/initialize`（或前端的 `/setup` 页）；QA 普通用户用 `create_user` 建（沿用第 4 步 export 的变量，它读同一份运行时 yaml 和库），初始口令在 `$d/home/credentials/<邮箱>.txt`；再登录一次、用 `change-password` 带上同一个 `new_email` 完成首次设置（清掉 `needs_setup`）。邮箱用 `example.com`：`.test` 这类保留域名过不了 EmailStr。口令只写进 0600 文件，不打印：

```bash
"$PY" - "$d" <<'EOF'
import os, re, secrets, subprocess, sys
from pathlib import Path
import httpx
d = Path(sys.argv[1]); gw = "http://127.0.0.1:8011"; admin, qa = "admin-pick-e2e@example.com", "qa-pick-e2e@example.com"
def private(name, text):
    with os.fdopen(os.open(d / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f: f.write(text)
def strong(): return "Qa-" + secrets.token_urlsafe(24) + "-9z"
pw = strong(); assert httpx.post(f"{gw}/api/v1/auth/initialize", json={"email": admin, "password": pw}).status_code == 201
private("admin.env", f"ADMIN_EMAIL={admin}\nADMIN_PASSWORD={pw}\n")
subprocess.run([sys.executable, "-m", "app.gateway.auth.create_user", "--email", qa], check=True, capture_output=True)
cred = d / "home" / "credentials" / f"{qa}.txt"; first = re.search(r"^password: (.+)$", cred.read_text(), re.M).group(1)
with httpx.Client(base_url=gw) as c:
    assert c.post("/api/v1/auth/login/local", data={"username": qa, "password": first}).status_code == 200
    pw = strong(); body = {"current_password": first, "new_password": pw, "new_email": qa}
    assert c.post("/api/v1/auth/change-password", json=body, headers={"X-CSRF-Token": c.cookies["csrf_token"]}).status_code == 200
private("qa.env", f"PICK_E2E_EMAIL={qa}\nPICK_E2E_PASSWORD={pw}\n"); cred.unlink()
EOF
```

**6. 前端**（`$W/frontend`）。`DEER_FLOW_INTERNAL_GATEWAY_BASE_URL` 要在 build 时就在（rewrites 编进产物）；镜像读连接和 CA 只在 start 时给，从文件读进环境、不上命令行。build 在本机约 1 分钟，慢的环境放后台跑。

```bash
cd "$W/frontend"
export DEER_FLOW_INTERNAL_GATEWAY_BASE_URL=http://127.0.0.1:8011
export NEXT_TELEMETRY_DISABLED=1    # 本机默认开着 Next 的匿名遥测，会往外发请求
pnpm build
PICK_MIRROR_READER_URL="$(cat "$d/reader.url")" PICK_MIRROR_CA_PEM="$(cat "$d/tls/ca.pem")" \
  pnpm start -p 3008 --hostname 127.0.0.1 > "$d/frontend.log" 2>&1 &
```

**7. 跑。** 先资料页、再个人选剧：后者会导入一批个人剧库，之后 `/sync` 的 `current.shared` 变成 false、资料页多一条「手动导入的剧库」横幅（资料页用例在两种状态下都要过）。本机实测资料页 6 条 5–7 秒；个人选剧 3 条 40 秒到 2 分钟（qwen3:8b，模型冷启动时慢），超过 2 分钟的环境放后台跑。

```bash
set -a; . "$d/qa.env"; set +a
PICK_BOARD_FIXTURE_JSON="$d/board.json" \
  pnpm exec playwright test -c playwright.pick.config.ts tests/e2e-pick/pick-data-board.spec.ts
pnpm exec playwright test -c playwright.pick.config.ts tests/e2e-pick/personal-selection.spec.ts
# 读角色确实走了 TLS：每条读连接在 pg.log 里都记着 SSL（连接池 5 秒就收，查 pg_stat_ssl 常常已经空了）
grep "user=$ROLE" "$d/pg.log" | grep -c "SSL enabled"    # 大于 0
grep "user=$ROLE" "$d/pg.log" | grep -vc "SSL enabled"   # 0
```

输出里每个用例都要是 passed；出现 skipped 就是环境没给全。`frontend.log` 里不应有 `[pick-board] query failed` 或 `outside a version scope`。

**8. 收尾**（顺序不能反：库上还有 gateway 的连接时删不掉库）：停前端和 gateway（上面两个后台进程），然后

```bash
cd "$W/customizations/pick-workbench"
PICK_TEST_PG_URL=$PG "$PY" tests/mirror/board_fixture.py down --database "$DB" --role "$ROLE"
"$B/pg_ctl" -D "$d/pgdata" -m fast stop    # docker 版：docker stop pick-e2e-pg
rm -rf "$d"
```

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

更新后重启Gateway，并核对源码、托管副本与site-packages三处业务Python文件一致。独立迁移链（`ggwork_pick/migrations/versions/`，版本记在 `ggwp_alembic_version`）在启动时升级到最新版本，当前是0006：0005给两种方言的ggwp表加可空列，0006只在PostgreSQL上建 `pick_mirror` 的四张表，SQLite上什么也不做。升级后的旧数据库历史查询/个人选择保留。


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
