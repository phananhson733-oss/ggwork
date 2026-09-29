# Azure模型与云部署

已确认部署方向：Vercel网页 + 常驻云后端 + DeerFlow运行时 + Azure LLM。

## 模型协议

沿用RealShort选剧问答的Azure Responses API语义：endpoint后接`/openai/v1/responses`、api-key头、store=false、encrypted reasoning、禁并行函数调用，不传temperature。DeerFlow使用已有langchain_openai.ChatOpenAI Responses模式，不嵌套RealShort的Azure问答循环。

模型配置名azure-pick，真实deployment从AZURE_OPENAI_DEPLOYMENT读取；唯一活动模型不再包含Ollama。五个业务工具（2026-09-23 新增计数工具 pick_count_candidates）、保存确认、身份隔离保留；整轮执行期限原为120秒，2026-09-29 起改由`PICK_RUN_TIMEOUT_SECONDS`决定（默认600秒，见下文「模型档位与超时」）。API密钥只配置在后端，不进入前端环境变量或Git。

Vercel的RealShort生产环境确有AZURE_OPENAI_ENDPOINT/API_KEY/DEPLOYMENT，类型为sensitive不能回读。接入时依用户授权复用RealShort本地同名环境配置，两边用的是同一个Foundry资源（joyocloud05-9398）和同一把资源key。部署名称起初为gpt-5.6-luna-2，2026-09-28起改为gpt-6-sol，见下节。

本机系统DNS对该Azure域名解析异常（2026-09-28仍如此）；使用公开DNS结果和原域名TLS验证的真实请求曾返回200/completed。Railway容器内的默认DNS正常，对Azure的冒烟改在容器里做（`railway ssh -s gateway -e production`）。

## 当前部署与换模型

2026-09-28起：资源joyocloud05-9398（Foundry项目同名），部署gpt-6-sol（版本2026-09-22，全局标准）。ggwork的Railway gateway与RealShort的Vercel生产环境共用这个部署和同一把资源key。

- 只换模型时，改Railway gateway服务的`AZURE_OPENAI_DEPLOYMENT`，base URL和key不动。改变量会自动用同一镜像重新部署，不必重建。
- 切换前先在容器里用与线上同形状的请求冒烟，通过再改变量。要覆盖这几项：reasoning.effort取low与high；strict函数工具加`parallel_tool_calls=false`；`store=false`加`include: [reasoning.encrypted_content]`；把上一轮的输出连同加密推理和函数结果回传后再作答。401说明key失效，404说明部署名不存在，400 `unsupported_parameter`说明模型不收该参数。
- gpt-6-luna对`reasoning.effort`返回400 `unsupported_parameter`。本配置每次请求都带reasoning，所以不能用它，尽管Azure文档写着支持。能力以实测为准。
- 回滚就是把变量改回上一个仍然存在的部署名。换模型期间不要删旧部署：2026-09-28删掉gpt-5.6-luna-2后，回滚目标落空，RealShort的问答也跟着中断，直到它改用gpt-6-sol才恢复。
- RealShort改用同一部署的做法：`vercel env update AZURE_OPENAI_DEPLOYMENT production`（值从文件重定向），再对当前生产部署`vercel redeploy`同一提交。RealShort的请求比这里多带`web_search`与`max_tool_calls`，2026-09-28在gpt-6-sol上实测正常。
- 这把key曾于2026-09-22~23随Vercel CLI部署源码上传过（项目团队只有一人，那四个部署已于2026-09-28删除）。计划改为ggwork用KEY2、RealShort用KEY1，并轮换被上传过的那一把。轮换前先确认两边各自用的是哪一把。

## 模型档位与超时（Railway 变量）

用户决定（2026-09-28）：开思考用high，关思考保持low。档位、输出上限和超时都在Railway的gateway服务变量里调，不改代码、不重新构建。`config.pick.example.yaml`的模型段只写`$PICK_LLM_*`占位，AppConfig加载时解析（嵌套的`when_thinking_enabled.reasoning.effort`同样解析）；入口`pick_entrypoint.py`对没设的变量补默认值，并在写任何文件之前校验取值，填错时部署直接失败并指出变量名，不会拖到每次请求才报错。

| 变量 | 默认 | 作用 |
|---|---|---|
| `PICK_LLM_EFFORT_THINKING_ON` | `high` | 开思考时的`reasoning.effort` |
| `PICK_LLM_EFFORT_THINKING_OFF` | `low` | 关思考时的effort（界面关掉思考时用；选剧台的标题不调模型，`title.model_name`为空） |
| `PICK_LLM_MAX_OUTPUT_TOKENS` | `32000` | Responses的`max_output_tokens`，推理token也算在里面 |
| `PICK_LLM_REQUEST_TIMEOUT_SECONDS` | `300` | httpx超时；流式时也是两段字节之间允许的最长静默，推理期间可能一直没有字节 |
| `PICK_LLM_STREAM_CHUNK_TIMEOUT_SECONDS` | `300` | langchain两段解析后分块之间的最长间隔；SSE keepalive注释不会重置它 |
| `PICK_RUN_TIMEOUT_SECONDS` | `600` | 整轮上限：宿主watchdog与扩展自己的截止时间（`ggwork_pick/context.py`）读同一个变量 |

- effort取值限`none`、`minimal`、`low`、`medium`、`high`、`xhigh`。在gpt-6-sol上只冒烟过low和high，改成其他值之前先按上节的清单冒烟。
- GPT-6按题目难度自适应推理：简单问题上`reasoning_tokens=0`，不代表effort没生效。high档在真实多轮工具问题上的实测（2026-09-29，见[progress.md](progress.md)「模型换 gpt-6-sol」一节）：单次推理不超过600 token，约是low的4倍；最长一轮66秒，最长单次调用约22秒，所以默认值不改。high档工具调用更多，会碰到每轮8次的业务工具上限。
- 数字必须是正数，`PICK_LLM_MAX_OUTPUT_TOKENS`必须是整数。`ModelConfig`把`max_tokens`、`request_timeout`声明成数字字段，占位解析出的字符串在加载时转成数字。langchain-openai会原样保留字符串形式的超时，结果每个请求都报连接错误。
- 改法：`railway variable set PICK_LLM_MAX_OUTPUT_TOKENS=48000 --service gateway`。一次改好几个时，前面几个加`--skip-deploys`，最后一个触发部署。改变量会自动重新部署（见上节），不用改仓库，也不用重新`railway up`。卷上的yaml只含占位，不用动。要回到默认值，删掉那个变量（`railway variable delete <名字> --service gateway`）。不要为了回退在控制台Rollback：S3之后gateway只能经守卫部署，见[progress.md](progress.md)。
- 观测：每次模型调用结束，gateway日志都有一行`LLM token usage: input=… output=… total=… output_token_details={'reasoning': N}`，用`railway logs --service gateway`过滤这一行即可。耗时看相邻两行的时间戳。
- Docker Compose（`docker/docker-compose.pick.yaml`）用相同默认值传这些变量。原生Gateway如果用的是本模板的拷贝，要自己导出这些变量。

### 长回合的超时链

high档一轮最多12次模型调用，单次调用在推理期间可能几分钟没有输出。下表列出其余可能掐断长回合的环节（2026-09-29核对）：

| 环节 | 限制 | 结论 |
|---|---|---|
| 浏览器 → Vercel | 同源`/api/langgraph`，Next在构建时写入的rewrite指向Railway（外部rewrite，不经函数，`maxDuration`不适用） | Vercel代理要求120秒内收到首字节，之后每120秒至少有一次数据（[文档](https://vercel.com/docs/routing/rewrites)，[changelog](https://vercel.com/changelog/cdn-origin-timeout-increased-to-two-minutes)）。gateway空闲时每15秒发一次SSE心跳，所以这条不会触发 |
| Railway边缘 | 单个HTTP请求最长15分钟，连续5分钟没有数据就断开（[文档](https://docs.railway.com/networking/public-networking/specs-and-limits)） | 心跳能防止空闲断开；整轮上限要留在15分钟以内，默认600秒 |
| 前端 | LangGraph SDK不设超时；主发送和「重新生成/编辑」都用`buildRunStreamOptions()`显式传`onDisconnect: "continue"`，断流后服务端继续跑，可以重新加入 | 已修（2026-09-29）：两条路径共用同一组提交选项，单测钉住传给SDK的选项，e2e钉住请求体里的`on_disconnect: "continue"`。核对时发现重跑路径此前实际也发`continue`：SDK 1.6.0在`streamResumable: true`时自动补这个默认值，原先「走服务端默认的cancel」的记录不准。现在显式传入，不再依赖SDK默认值 |
| LLM重试 | `LLMErrorHandlingMiddleware`对`ReadTimeout`、`StreamChunkTimeoutError`各重试1次；每次重试都经过`PickModelGate`，计入12次调用上限 | 一次超时重试后最坏耗时约为2×`PICK_LLM_REQUEST_TIMEOUT_SECONDS`，仍受整轮上限约束 |
| OpenAI SDK重试 | `max_retries: 0` | 不重试 |

## 部署结构

- 现有Vercel pick-workbench项目有READY生产部署，入口pick-workbench.vercel.app，保留。
- 新Vercel项目ggwork-deerflow，rootDirectory=frontend。
- 新Railway项目ggwork-deerflow，独立gateway服务，/data持久化卷，单实例。
- Docker镜像由云端构建，不依赖本机Colima磁盘。模型计算发生在Azure，不打包本地模型。
- cloud entrypoint从无密钥的config.pick.example.yaml生成/data/pick-runtime.yaml（0600），扩展配置保存在/data；现有扩展配置不覆盖。Railway必须显式设置`PICK_DB_BACKEND`（`sqlite`或`postgres`），没设或取值不对时入口直接退出。`DEER_FLOW_CONFIG_PATH`、`DEER_FLOW_EXTENSIONS_CONFIG_PATH`只能不设或指向上述/data下的文件，否则同样退出，入口不覆盖别处的配置：
  - `sqlite`：SQLite放/data/data，生成的yaml与此前逐字相同；从Postgres回滚时改回它。
  - `postgres`：database段只写字面的`$PICK_DATABASE_URL`，由AppConfig在加载时解析，卷上的yaml不含密钥；`PICK_DATABASE_URL`缺失、不是`postgresql://`连接串或带`sslmode`、`ssl`等ssl开头的参数时拒绝启动，TLS改由`PGSSLMODE=require`指定。镜像构建带`--extra postgres`。
- 自助注册关闭。其他成员的账号经`railway ssh`建：`cd /app/backend && DEER_FLOW_HOME=/data python -m app.gateway.auth.create_user --email <邮箱> [--role user|admin]`。入口与gateway共用补缺省值的函数（`DEER_FLOW_CONFIG_PATH=/data/pick-runtime.yaml`等），运行时yaml不存在时不连库、非零退出；账号首次登录需重新设置，随机初始密码只写进/data/credentials/<邮箱>.txt（0600），不打印，发给本人后删掉文件。
- 迁到Supabase的bootstrap、连接、切换与回滚步骤见[supabase.md](supabase.md)。
- 后端数据库和私有文件不能放Vercel临时文件系统。前端通过配置好的后端URL请求API，Azure密钥仅在Railway。

本文是接入与部署进行中的记录，不代表新线上工作台已验收通过。部署ID、域名、数据迁移、默认DNS下的Azure工具调用及重启保持性需要在实际验证后补录。
