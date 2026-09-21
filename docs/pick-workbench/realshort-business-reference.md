# RealShort业务接入依据与Docker定位

本次为只读代码核对。实际参考工作树：`/Users/wzb/Code/realshort-alt-release-20260917`，HEAD `3e8a98a`。原 `/Users/wzb/Code/realshort` main 位于较旧提交且有未提交改动，不以它缺少路由推断选剧台不存在。线上URL本次无法由抓取工具读取；下述结论来自本地代码，不代表核实了当前线上数据或部署版本。

## 已有业务资产

- `src/app/admin/(protected)/pick/page.tsx`：选剧、全部剧库、榜单、发布记录、规则、证据页。
- `src/lib/pick/queries.ts`：统一只读查询入口；剧场剧单与ReelShort正典行合并查询。筛选、分页计数共用条件。
- `src/lib/pick/request.ts`：剧场/语言/依据/发布日期与发布状态等参数白名单；榜期缺失、跨年周标签有明确解析状态。
- `src/lib/pick/platforms.ts`：剧场规则与来源/更新时间，包括禁YouTube、仅指定YouTube剧单和附加发布条件。不是泛化allowed/denied即可完整表示。
- `src/db/schema.ts`：catalog_rows、catalog_signals、catalog_posted、catalog_accounts；另外结合ReelShort片库、本站出站、分成与搜索信号。
- `src/lib/ask/tools.ts`：现成search_catalog、count_catalog、get_catalog_row、list_rank、list_posted五个只读工具；沿用pick查询而非另写SQL。
- `src/lib/ask/{projection,answer-check,negative-claims}.ts`：工具结果裁剪、来源/数字/总数核对、未取全提示与负向断言校验。
- `src/app/admin/(protected)/pick/ask-actions.ts`：现有问答为登录后的Server Action。它不是已建好的跨应用HTTP/MCP业务接口。

## 接入方向

DeerFlow继续提供账号、会话、运行、个人候选快照和保存。真实业务查询优先复用RealShort的查询与工具语义，经一层受认证、只读、白名单参数的数据适配器提供给工作台。Node/TypeScript查询代码不直接复制为Python SQL，也不把已有Azure模型循环嵌套成第二个对话Agent。

第一步接search_catalog/get_catalog_row，再接规则、榜单、发布记录。业务结果保存原rowKey、平台、证据日期/来源、有效查询条件、partial/total、数据版本；个人清单保存到工作台自己的数据库。CSV/JSON保留为离线快照和测试入口，不作为真实业务接入的必经人工整理步骤。

特别保留：剧场声明不等于本站需求信号；搜索曝光不是点击/订单；滚动销售指标变化不是当日销量；同名不是同剧；无发布匹配不等于任何账号从未发布。按某个账号排除已发，还需要明确账号范围、发布记录完整性和结构化匹配契约，不能仅靠accounts字段存在作全称判断。

实际生产数据和当前数据库访问本次未验证。代码参考已确定，后续缺口应称为只读数据连接与业务样本验收，不能再笼统称为缺业务需求或必须让用户先制作CSV。

## Docker

当前compose只有nginx、frontend、gateway三个服务，单Gateway与持久化目录，Ollama在宿主机。它负责一致部署、启动和依赖环境，不是每成员一个容器，也没有挂载Docker socket让Agent自行创建成员沙箱。

成员个人清单/候选/资料隔离靠服务端身份与owner作用域，属于应用逻辑隔离。MVP一人一空间或小团队共享入口不要求每人独立Docker。后续若开放任意代码执行，再专门设计执行沙箱；组织/客户要求更强隔离时才考虑独立实例和数据库。

业务接入可继续使用当前原生服务。建议把Docker移到部署打包验收，不作为读取RealShort代码、设计适配层或本地业务联调的前置条件。此为对原计划优先级的调整建议，不等于Docker构建已通过。
