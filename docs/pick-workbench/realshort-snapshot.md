# RealShort 候选池快照接入（2026-09-21）

截图中 Azure 已成功调用工具，但个人 workspace 没有任何 catalog batch，工具抛出 ValueError，模型又把内部错误复述到正文。并非 Azure 密钥失败。

已从 RealShort `3e8a98a` 查询 facade 导出的 `unionRows`、`toRowWithFlags`、`loadSignalsFor` 只读提取候选。条件为 `has_signal AND off_on IS NULL`，包含剧场剧单和 ReelShort 正典候选；不调用 catalog-import、catalog-refresh，不修改源库。按 200 个行键分批读取信号以避免巨型参数查询。候选上限 10,000，超过就失败，不发布截断批次。

本次采集完成时间 2026-09-21T08:25:33.376Z（北京时间 16:25），4,416 行，其中英语 1,852 行；2 行空语种标为 und。全部通过现有导入模型校验，使用现有 Importer 发布到本地主账号、迁移后的云端同一主账号，以及独立验收账号。主账号个人 selections 数量保持不变。

这是有信号的候选池快照，不是全部剧库，不是实时连接或定时同步。源库行和信号分多次只读查询取得，不承诺数据库事务级同一时刻一致性。导入后不可变批次固定本次依据，后续同步产生新批次。原始文件仅在忽略目录和授权云卷，不进入 Git。

映射保持原始行键的 base64url 标识（保留尾随空格的区别），中文语种使用源 LANG_LOC 映射，证据保留来源、日期、评级/排名/备注。网盘链接、提取码、账号凭据不导出。ReelShort clk/bill/gsc 只保留候选条件命中事实，不虚构指标数值和周期。未标下架不等于确认在售，availability 保留 unknown。渠道禁止保留 denied，其他有附加条件的规则保留 unknown，并将各剧场规则、核对日期和来源作为知识资料导入。首版快照未映射标签筛选和团队发布/排期记录。

“排除我已经选过的”只排除该登录用户在本工作台确认保存的条目。不能把团队已发布、已排期或源站选剧池当作该用户已选。

## 重复同步

维护者在 RealShort checkout 的环境中，用该项目已安装的 tsx 执行本仓库 `customizations/pick-workbench/scripts/export-realshort.ts REALSHORT_CHECKOUT PRIVATE_OUTPUT_DIRECTORY`；Node 需 `--conditions=react-server`，cwd 为 RealShort checkout，以解析其 tsconfig 路径。脚本仅使用源 checkout 的环境文件进行 SELECT，输出 catalog、来源清单和规则 Markdown。先使用 `ggwork_pick.imports.parse_catalog` 全量验证，再通过当前工作空间的认证导入接口发布；不要写生产源库，不要复用他人的个人批次。

首版尚未把数据库管理连接串放入云端。下一阶段若需要定时同步，应配置受限只读数据源或专用读取 API，并增加来源新鲜度与同步状态展示。

## 验收

- 业务测试 37 passed，覆盖空库结构化状态与无第二次模型调用的友好回复。
- 前端 eslint / TypeScript 通过。
- 云端真实 Azure，同一句“找5部英语剧，排除我已经选过的”：success，5 条 en 候选，全部 realshort-pick 来源，7.44 秒。独立 QA 账号执行，没有替主账号新增选剧。
- 旧浏览器验收：保存/历史依据/丢响应重试通过；云端停止用例通过；刷新用例因页面未出现输入框超时，不能宣称刷新验收已通过。
- 当前真实数据请求不会再走空库错误分支。历史对话中的失败文本仍保留，需新发起一轮查询。

云端发布：Vercel production alias `https://ggwork-deerflow.vercel.app` 已更新；Railway 部署 `6382a03e-482f-443a-9c35-191939cef241` SUCCESS，运行容器确认包含 catalog_unavailable 修复，持久化库 quick_check=ok，重建后导入批次仍在。

页面复核：同一认证 QA 会话经 Vercel `/workspace/picks` 200（0.7秒）、`/workspace/chats/new` 200（2.1秒）、`/api/models` 200。Playwright 页面导航两次超时，尚未取得本次真实资料的候选卡截图；HTTP 200 与直接后端成功不能替代完整浏览器验收。临时 Railway SSH 公钥已撤销、对应本地临时私钥已删除，独立 ssh-agent 已停止；云卷迁移中间文件已清理。
