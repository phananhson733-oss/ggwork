# Azure模型与云部署

已确认部署方向：Vercel网页 + 常驻云后端 + DeerFlow运行时 + Azure LLM。

## 模型协议

沿用RealShort选剧问答的Azure Responses API语义：endpoint后接`/openai/v1/responses`、api-key头、store=false、encrypted reasoning、禁并行函数调用，不传temperature。DeerFlow使用已有langchain_openai.ChatOpenAI Responses模式，不嵌套RealShort的Azure问答循环。

模型配置名azure-pick，真实deployment从AZURE_OPENAI_DEPLOYMENT读取；唯一活动模型不再包含Ollama。五个业务工具（2026-09-23 新增计数工具 pick_count_candidates）、保存确认、身份隔离及120秒执行期限保留。API密钥只配置在后端，不进入前端环境变量或Git。

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
