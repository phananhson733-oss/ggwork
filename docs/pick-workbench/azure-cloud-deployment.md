# Azure模型与云部署

已确认部署方向：Vercel网页 + 常驻云后端 + DeerFlow运行时 + Azure LLM。

## 模型协议

沿用RealShort选剧问答的Azure Responses API语义：endpoint后接`/openai/v1/responses`、api-key头、store=false、encrypted reasoning、禁并行函数调用，不传temperature。DeerFlow使用已有langchain_openai.ChatOpenAI Responses模式，不嵌套RealShort的Azure问答循环。

模型配置名azure-pick，真实deployment从AZURE_OPENAI_DEPLOYMENT读取；唯一活动模型不再包含Ollama。五个业务工具（2026-09-23 新增计数工具 pick_count_candidates）、保存确认、身份隔离及120秒执行期限保留。API密钥只配置在后端，不进入前端环境变量或Git。

Vercel的RealShort生产环境确有AZURE_OPENAI_ENDPOINT/API_KEY/DEPLOYMENT，类型为sensitive不能回读。本次依用户授权复用RealShort本地同名环境配置；没有修改RealShort环境或密钥。真实部署名称为gpt-5.6-luna-2。

本机系统DNS对该Azure域名解析异常；使用公开DNS结果和原域名TLS验证的真实请求已返回200/completed。常规本机连接尚不能据此宣称恢复，云部署仍需验证默认DNS链路。

## 部署结构

- 现有Vercel pick-workbench项目有READY生产部署，入口pick-workbench.vercel.app，保留。
- 新Vercel项目ggwork-deerflow，rootDirectory=frontend。
- 新Railway项目ggwork-deerflow，独立gateway服务，/data持久化卷，单实例。
- Docker镜像由云端构建，不依赖本机Colima磁盘。模型计算发生在Azure，不打包本地模型。
- cloud entrypoint从无密钥的config.pick.example.yaml生成/data/pick-runtime.yaml，将SQLite放/data/data，扩展配置保存在/data；现有扩展配置不覆盖。
- 后端数据库和私有文件不能放Vercel临时文件系统。前端通过配置好的后端URL请求API，Azure密钥仅在Railway。

本文是接入与部署进行中的记录，不代表新线上工作台已验收通过。部署ID、域名、数据迁移、默认DNS下的Azure工具调用及重启保持性需要在实际验证后补录。
