# Spec：选剧工作台内嵌剪辑与对话 Skill 双入口

规格日期：2026-10-08。状态：已综合现有工程、设计决策；实现与发布验收尚未完成。本文不把旧 mock 的模拟行为当作已实现能力。

## Problem Statement

选剧运营已经在工作台中发现、比较并保存剧集，但从“选中一部剧”到“获得可用短视频”仍需转到本机脚本、不同 Skill 或其他编辑工具中操作。用户需要重复说明剧目、素材、剪辑要求，并自行追踪转录、方案、渲染和成片位置。对话中的请求与功能页面没有稳定的共同任务身份，也缺少对断线、部分失败和历史版本的统一说明。

用户希望将现有短剧剪辑能力内嵌到选剧工作台：既能独立使用，也能在对话中通过自然语言或 Skill 命令直接完成。用户只需选择一部剧并提供素材；系统应清楚表达什么时候可开剪、工作在哪里运行、结果在哪里、失败后如何继续。功能必须保留现有选剧流程，并避免覆盖原素材或已交付成片。

## Solution

在现有工作台增加一个固定的“剪辑”入口，首页为任务列表和“新建剪辑”。剧目行的“剪辑”可直接带入该剧；对话中的剪辑请求调用同一任务服务，任务卡打开同一详情。独立任务不要求先创建对话或先把剧集保存进个人清单。

首个执行平台为用户自己的 Apple Silicon Mac。用户连接设备后，可提供这台 Mac 上已授权的素材目录，或将文件提交到在线的 Mac。转录与视频渲染在本地完成；云端模型只使用必要转录文本、时间戳和媒体标识生成结构化剪辑方案。原片和最终 MP4 不持久存放在云端；在线预览和下载通过授权的生成设备提供。

页面填写和素材检查属于草稿，点击“开始剪辑”才提交执行意图。对话中已明确要求执行的请求，在缺少的前提补齐后继续，且只受理一次。条件齐全时不增加例行方案确认；只有用户明确选择“先看方案”时才等待其确认。

一次请求只针对一部剧，但可包含多集素材和多条成片。选中的全部素材收到并校验后才进入正式处理；部分成片成功时先交付已验证的结果，失败项可单独重试。任务结果、执行阶段、设备连接状态和文件可访问性分别表达。改变剪辑要求产生关联的新版本，已交付结果保留。

## User Stories

1. As a selection operator, I want to open an editing workspace inside the existing workbench, so that I can move from selection to editing without switching products.
2. As a selection operator, I want to start editing from a drama row, so that the selected drama is carried into the request automatically.
3. As a workbench user, I want to create an editing task independently, so that I do not have to save a selection or create a conversation first.
4. As a workbench user, I want one stable editing home, so that I always know where to find ongoing tasks and past results.
5. As a selection operator, I want to return to my original filtered list, so that editing does not lose the selection context I was reviewing.
6. As an editor, I want each request to identify one drama, so that episodes from different dramas are not silently mixed.
7. As a conversational user, I want to describe the edit in natural language, so that I can work without completing a separate form.
8. As a conversational user, I want to invoke an admitted editing Skill with slash syntax, so that I can select the intended workflow explicitly.
9. As a conversational user, I want registered plugin-and-Skill aliases to resolve consistently, so that the same command has the same meaning across entry points.
10. As a conversational user, I want the assistant to ask only for missing or ambiguous information, so that I do not repeat details it already knows.
11. As a conversational user, I want a fully specified execution request to proceed directly, so that routine editing does not require manual approval clicks.
12. As an editor, I want to request a plan before rendering when I choose, so that I can adjust the narrative before generating video.
13. As a page user, I want selecting files to remain a draft action, so that rendering does not start before I submit the request.
14. As a user waiting for prerequisites, I want my submitted intent to continue once those prerequisites are satisfied, so that I do not have to submit the same request again.
15. As a first-time user, I want device preparation guidance inside my current page or conversation, so that I can connect my Mac without losing my editing request.
16. As a device owner, I want to authorize a specific Mac, so that source paths and outputs are associated with the correct computer.
17. As a device owner, I want directory access to be explicit, so that the worker reads only the material I have authorized.
18. As an editor, I want to distinguish an online device from a ready editing environment, so that I understand why a connected Mac may still need preparation.
19. As a user on an unsupported execution platform, I want a clear support message, so that I do not mistake browser access for native rendering support.
20. As a returning user, I want an already-prepared Mac to avoid repeating setup, so that subsequent editing requests remain quick to start.
21. As an editor, I want to use a local material folder, so that source video already on my Mac does not need a persistent cloud upload.
22. As an editor, I want to submit selected files to my connected Mac, so that I can provide material through the workbench when a folder path is inconvenient.
23. As an editor, I want to see the target device during transfer, so that I know where my files are being received.
24. As an editor, I want receipt and verification to be separate states, so that a selected or transmitted file is not mistaken for usable material.
25. As an editor, I want all files in my selected source set verified before editing begins, so that missing episodes are not silently excluded.
26. As an editor, I want verified files retained after a transfer failure, so that I do not need to resend material that is already usable.
27. As an editor, I want to explicitly revise the selected source set, so that I can choose to omit a file while keeping that decision visible.
28. As an editor, I want changed source bytes to trigger revalidation, so that an old transcript or plan is not applied to different video.
29. As an editor, I want duplicate or ambiguous episode identification surfaced, so that the system does not guess an incorrect sequence.
30. As a privacy-conscious user, I want to know which text is sent for cloud planning, so that local video processing is not misrepresented as a fully offline workflow.
31. As an editor, I want controls to show only capabilities the installed system can execute, so that sample options do not become false product promises.
32. As an editor, I want truthful phases and measured progress, so that I can distinguish waiting, transcription, planning, rendering and output checks.
33. As an editor, I want accepted work to continue after I leave the page, so that the browser is not required to keep an already-submitted job running.
34. As an editor uploading material, I want to know when the browser must remain open, so that I do not interrupt an unfinished transfer unknowingly.
35. As a workbench user, I want refreshes and repeated submissions to preserve one task identity, so that I do not create duplicate rendering work.
36. As a workbench user, I want page details and conversation cards to show the same task state, so that I can switch entry points confidently.
37. As a user reading an old conversation, I want historical narration distinguished from current task state, so that an old message is not mistaken for live progress.
38. As an editor, I want successful outputs from a partially failed batch delivered with an accurate count, so that I can use completed work without mistaking the whole request for finished.
39. As an editor, I want to retry only the failed output when possible, so that successful videos are not regenerated or overwritten.
40. As an editor, I want failures before individual output plans exist to have a stage-level retry, so that recovery does not depend on a nonexistent output item.
41. As an editor, I want a stop request to show that it is awaiting confirmation, so that I do not assume a local process has stopped prematurely.
42. As an editor, I want already-verified outputs retained when I stop remaining work, so that cancellation does not destroy completed results.
43. As an editor, I want late reports from stopped or replaced attempts ignored, so that old work cannot change the final task outcome.
44. As an editor, I want failures to identify the blocked stage and a viable next action, so that I can recover without restarting everything blindly.
45. As an editor, I want completed tasks to remain visible when their Mac is offline, so that I know my historical results have not become failed jobs.
46. As an editor, I want preview and download limitations explained before I click, so that an offline device is not mistaken for a broken player.
47. As an editor, I want a missing local file distinguished from an offline device, so that I receive the correct recovery guidance.
48. As an editor, I want interrupted playback or download to retry access rather than rendering, so that a network problem does not create another edit.
49. As an editor, I want adjustment requests to create an associated new version, so that the original successful cut remains available.
50. As an editor, I want version history to retain the requested change and source version, so that I can understand how a later cut was produced.
51. As a standalone user, I want source-conversation links shown only when one exists, so that the interface does not invent conversation history.
52. As a task owner, I want other users unable to read or modify my material and results, so that ownership is enforced regardless of the entry point.
53. As a user with expired authorization, I want login recovery distinguished from device reauthorization, so that I can restore the correct access without duplicating work.
54. As a mobile browser user, I want task status and next actions prioritized, so that I can operate my paired Mac without requiring native processing on my phone.
55. As a keyboard or screen-reader user, I want accessible controls and useful state announcements, so that I can complete the same editing workflow without pointer-only interactions.
56. As a workbench user, I want readable text and consistent light and dark themes, so that editing feels like part of the existing workbench.
57. As a user with no tasks or a failed history request, I want those states distinguished, so that a loading error does not imply my work has disappeared.
58. As a selection operator, I want existing candidate ordering, historical replay and save receipts preserved, so that adding editing does not break selection work.
59. As an operator, I want editing history independent of catalog-mirror availability, so that an unrelated catalog outage does not hide existing clip tasks.
60. As a maintainer, I want one shared editing contract across UI and conversational callers, so that permissions and task behavior do not drift between implementations.
61. As a maintainer, I want a native acceptance run with real media, so that passing simulations is not mistaken for a working rendering pipeline.
62. As a maintainer, I want unresolved installation, resource and quality constraints recorded explicitly, so that agent implementation does not invent unsupported limits or release guarantees.

## Implementation Decisions

1. **业务扩展优先。** 剪辑作为一个业务扩展包，包含 Gateway 接口入口和独立 Mac 执行器入口。复用宿主扩展注册、鉴权、数据库生命周期与 UI 贡献点。原生转录和渲染依赖不进入 Gateway 的重计算路径。不为该功能另造 Agent runtime，也不改造核心 MCP 任务系统为通用剪辑队列。

2. **统一业务模型。** 明确区分待准备的执行意图、已受理剪辑任务、冻结素材集合、结构化剪辑方案、请求输出项、执行尝试、已验证成片和成片访问状态。任务归属于服务端认证的用户，来源对话可选。业务表与迁移归扩展所有，不加入上游核心迁移历史。

3. **唯一业务测试及调用边界。** 页面和对话适配器调用同一组受鉴权约束的剪辑操作：准备/提交请求、查询任务及输出、停止、重试阶段或失败项、创建关联版本、查询设备和素材准备状态、请求成片访问。具体 API 命名由实现与现有接口约定对齐；不能为页面和 Skill 各自维护任务规则。

4. **固定信息架构。** 一个“剪辑”入口固定到任务列表，提供新建入口和每项任务的稳定详情。剧目行带入该剧，对话卡打开同一任务。保留既有导航、筛选、版本与回放上下文，不强迫独立用户先创建对话。任务详情按剧目、要求、当前状态、下一步操作组织。

5. **提交边界。** 页面选文件、检查素材和编辑要求只更新草稿；点击“开始剪辑”提交执行意图。明确的对话执行请求无需补点按钮。缺少前提的意图显示待准备；前提齐全后通过同一稳定请求标识受理。重复消息、刷新、重试网络请求和重复就绪报告不得创建第二项任务。同标识承载不同要求应明确报告冲突，不静默替换。

6. **Skill 准入。** 复用现有 slash 名称、保留命令、可用目录及前后端共同解析约定。插件别名必须解析为真实注册且已准入的 Skill 身份。路径不当作命令，任意命令文本不获得脚本执行权限。压缩包中的说明作为待适配来源；其中例行人工确认步骤按已确认的产品规则调整，不能覆盖用户要求的直接执行方式。

7. **能力覆盖与真实支持范围。** 源包包含原声高光、批量高光、Hook 混剪、旁白和视觉蒙太奇相关工作流。UI 的可选模式、数量、时长、语言、画幅及音轨要求必须来自实际准入配置，不照抄 mock 示例为正式上限。当前已展开的基线是原声高光与文本规划的 Hook 剪辑；视觉蒙太奇、旁白最终交付和编辑器草稿仍需各自的能力验收，不能被文本转录成功冒充已实现。

8. **Mac 准备流程。** 首期执行器支持 Apple Silicon macOS。首次连接、目录授权及环境检查在当前请求上下文中引导，并区分设备在线与环境就绪。保留剧目及要求，原生安装/授权仍发生在 Mac。已就绪设备不重复引导；浏览器的操作系统不是执行器的平台判断依据。

9. **设备控制与权限。** 执行器由用户绑定，以出站 HTTPS 周期领取工作、报告状态和接收停止指令。浏览器不是持续控制中继。设备身份、素材和任务所有者必须一致；Gateway 管理凭据及模型凭据不得下发到执行器。轮询、租约、重连和并发数值需实际配置与验证，本文不预设未经测量的数值。

10. **两种素材入口。** 本地目录由绑定设备在授权范围内解析、扫描和读取；服务端不按用户字符串读取云端目录。提交文件通过受控暂态传输交给在线 Mac，只有本地收到并验证才可用。显示目标设备、传输与校验阶段；不持久保存完整云端原片，不承诺未实现的断点续传。

11. **素材集合与身份冻结。** 等待准备时保留用户选定清单。选定文件全部本地收到并校验，执行任务受理时冻结集合版本、文件身份和剪辑要求，再进入正式转录、规划和渲染。不是要求整部剧全部集数。未就绪文件不能自动剔除；受理前用户可明确修改清单，受理后改变清单形成关联的新请求。符号链接越界、文件换字节、重复集号及未知顺序必须被识别或明确补问。

12. **转录与云端规划。** 转录在 Mac；缓存与验证后的媒体身份、语言及相关转录配置绑定，不只看文件名。云端模型只接收必要文本、时间戳和媒体标识，输出结构化计划。转录内容是数据，不是工具授权。计划的媒体引用、有限时间值、范围、对白衔接及请求输出配置必须校验；不能静默截断越界时间或跳过未知集数。具体模型、长剧处理策略与内容质量阈值仍是实施门槛。

13. **本地渲染和成片发布。** 各执行尝试使用隔离输出区域，原片不变。只有完整、可解码且通过约定输出检查的成片可被登记为已交付结果。编码回退和重试不得覆盖已交付引用。逐条交付完整 MP4，与禁止发布未完成的视频文件并不冲突。

14. **状态分离。** 分别表达执行阶段、任务结果、设备可达性及文件可访问性。等待领取不写成正在渲染；设备离线且结果未知不写成已经暂停或停止。使用真实事件、计数或实测进度；不能沿用 mock 定时百分比或编造剩余时间。已经受理的任务可在关闭页面后继续；仍在提交文件时浏览器与 Mac 需保持在线。

15. **部分成功。** 按请求数量显示“部分完成”和逐项结果，先交付已验证的成功项。失败项在前提满足后单独重试，成功引用不变。请求数量不能自动从三条缩成两条并宣称全部成功；正式能力不把 mock 的单任务状态作为限制。

16. **停止和并发裁决。** 提交停止后显示正在停止，直到受影响进程真实退出且执行器确认。停止受理后禁止受影响尝试新发布输出；此前已确认交付的成片保留。若完成先被服务端提交并校验，后到的停止返回已完成。已停止、已替换或因停止而失效的尝试迟到报告不能改终态或发布新结果。停止待确认期间拒绝同一执行范围的重试。

17. **重试与新版本。** 保持要求不变的失败恢复属于原请求的新执行尝试；已形成输出项时仅重试指定失败项，尚未形成逐条计划时可重试相应任务阶段。改变剪辑要求属于关联的新任务/版本，保留原版、修改要求、来源版本与时间。不默认无限重试，也不许重新生成所有成功项来掩盖单项失败。

18. **本地成片访问。** 成片只持久保存在生成设备。云端保存任务和文件索引，预览/下载使用当前获授权设备提供的字节。设备离线时保留完成数量，提前将访问操作标为不可用，并显示连接指引；指引不假装远程开机。重新上线后仍须验证授权及文件身份，不能仅凭心跳就宣布可播放。

19. **访问恢复。** 设备在线但文件缺失、输出校验失败、字节传输中断、登录过期和设备授权撤销分别提示。取片中断重试访问，不自动重新剪辑，不把部分字节标为完整交付。权限失效后停止受保护读写；只保留可安全恢复的上下文，恢复后按原身份查询，不跨账号复用缓存结果。

20. **双入口一致性。** 页面与对话展示同一服务端状态及恢复动作。对话只补问缺少或歧义信息，多个任务目标不明确时不猜最近任务。历史叙述保留当时事实，任务卡单独表达当前状态或读取失败。有真实来源对话才显示来源链接，不自动创建隐藏对话。

21. **视觉与无障碍。** 复用工作台外壳、组件和语义主题，主操作色与成功/在线状态分开；剪辑业务区使用明确的本地字体及回退，不全站换肤。正文、关键说明和标签至少 16px；触控目标至少 44×44px；正文对比度目标至少 4.5:1，控件/焦点至少 3:1。信息不只靠颜色，禁用原因正常可读，支持键盘、焦点定位、节制的状态播报和 reduced-motion。

22. **响应式与历史。** 桌面使用紧凑任务表格/列表和并排预览；平板将素材与要求堆叠；手机优先呈现剧目、状态、下一步动作，再呈现细节。复用宿主移动导航。任务列表包含剧目、时间、请求/交付数量、状态、设备和访问情况；加载失败不显示为没有任务，旧数据显示时明确刷新失败。

## Testing Decisions

### 主测试边界

主边界选用**受鉴权的统一剪辑任务接口**。通过现有 Gateway 集成测试客户端，以真实业务服务和临时数据库驱动请求，验证返回值、可查询任务状态、公开输出清单及授权行为。测试不依赖内部函数拆分、私有队列、具体 SQL 字符串、CSS 类名或组件树形状。

页面和对话只保留薄的适配与端到端一致性测试，不各自复制整套任务状态单元测试。对于无法从 Gateway 证明的原生文件权限、进程停止、编码与字节交付，增加真实 Apple Silicon 端到端验收；这是必要的部署边界证明，不为每个内部 helper 新造测试缝。

此选择沿用此前已接受的“共同服务 + 双入口 + 原生验收”方向；用户已授权后续按推荐选择，无需重新访谈确认。

### 现有测试先例与复用方式

1. **选剧接口的 owner 隔离及保存回执测试**：现有测试覆盖未认证拒绝、伪造 owner 被拒绝、跨 owner 不可见、相同请求回执重复读取和不同载荷冲突。复用其测试客户端及合成用户方式，验证剪辑的身份、幂等和结果隔离。
2. **选剧快照与重启持久化测试**：现有测试覆盖历史结果不被新数据重写、重试不改变既有身份和服务重启后可读。采用相同外部行为断言，验证冻结素材集合、关联版本、进程重连和任务恢复。
3. **前后端 slash 共享契约测试**：已有名称模式、保留命令、启用白名单、重复激活和不可用 Skill 测试。扩展真实注册的剪辑能力案例，不复制 mock 的正则解析器。
4. **现有个人选剧浏览器验收**：已有真实后端、隔离 QA owner、历史回放、重复保存回执、活动任务刷新及停止流程的先例。沿用隔离原则并扩展两种剪辑入口的可见行为；不能用只拦截全部 API 的成功演示代替真实后端验收。
5. **现有宿主持久化与任务测试**：可借鉴租约、重启、旧消息及取消竞争案例，但不因此复用其强制绑定对话的任务业务模型。

### 核心行为验收矩阵

| 范围 | 输入或触发 | 必须观察到的行为 |
|---|---|---|
| 执行意图 | 页面只选文件；对话明确要求开剪但缺素材 | 页面草稿不启动；对话保留待准备意图，补齐后只受理一次 |
| 幂等 | 双击、重复消息、网络超时后查询、就绪重复报告 | 同一请求一项任务；相同标识不同要求明确冲突 |
| 入口一致性 | 页面创建后在对话查看；对话创建后刷新页面 | 相同身份、状态、要求、输出和有效动作 |
| 身份权限 | 未登录、伪造 owner、跨用户任务/设备/文件 | 拒绝且不泄露剧名、路径或结果；模型文本不提供授权 |
| 素材完整性 | 所选 24 个仅 23 个验证；用户明确缩减范围 | 不自动开剪或跳过文件；明确修改后按新范围判断 |
| 素材版本 | 受理前后改变集合、同名换字节、扫描后变化 | 冻结身份不变；必要时新请求/重验，旧缓存和计划不误用 |
| 原生权限 | 目录未授权、符号链接越界、设备身份不匹配 | 无越界读取；显示实际阻碍并保留请求 |
| 传输 | 浏览器中断、Mac 断线、收到但未验证 | 不误报素材可用；已验证文件保留，未实现的续传不被承诺 |
| 方案安全 | 未知媒体、非有限/越界时间、转录内指令、模型截断 | 无效方案不可进入渲染；不静默改范围或采纳数据中的权限指令 |
| 部分交付 | 三条中两条通过，一条失败 | 部分完成 2/3；两条完整文件可交付，失败项单独恢复 |
| 重试 | 失败项重试、两入口并发重试、停止中重试 | 目标范围明确；成功引用保持；同范围冲突受服务端约束 |
| 停止竞态 | 完成先提交；停止先受理；旧尝试迟到 | 服务端顺序一致，停止确认真实，无迟到输出撤销终态 |
| 成片身份 | 调整原片时原片正在播放或下载 | 新关联版本产生，原引用及原素材不被覆盖 |
| 文件访问 | 离线、文件移动、校验失败、Range 传输中断 | 各自说明；不改任务完成事实，不发布坏文件，不自动重剪 |
| 权限恢复 | 登录过期、设备令牌撤销、重新登录另一个用户 | 不继续读受保护数据，不混账号缓存，不复制任务 |
| 页面状态 | 空历史、读失败、刷新失败、多个任务 | 空与错误分开；任务链接稳定；旧数据不是假实时状态 |
| 可访问性 | 375/768/1440 视口、200% 缩放、键盘、读屏 | 无页面级横向溢出，焦点合理，状态可知，操作可达 |
| 原有选剧 | 筛选、候选排序、来源证据、旧回放、保存回执 | 既有语义和权限不变，剪辑历史不依赖镜像正常读取 |

### 原生与质量验收

- 至少跑通真实 Apple Silicon 上的设备准备、授权素材、转录、最小必要文本规划、本地渲染、输出校验和浏览器取片闭环。
- 使用合成或明确可用于测试的短视频与隔离账号，不触碰生产用户素材；临时 SQLite/一次性 PostgreSQL 按现有测试约定使用。
- 断言解码、音轨/尺寸/时长与请求相符，原素材和已交付版本不变；播放成功不代替剧情、对白边界和时间引用的质量检查。
- 记录实际模型配置及长剧上下文策略，对叙事完整、对白衔接、重复片段、语言和来源支持性建立固定案例；阈值与成本/耗时上限在实测后确定，不能靠模拟数据填满。
- 运行仓库要求的离线检查，并完成受影响链路的真实后端与浏览器回归。测试完成的声明必须区分单元/接口、浏览器、真实原生媒体及模型质量各层证据。

## Out of Scope

- 云端重计算转录/渲染，以及完整原片或最终 MP4 的持久云端副本。
- 首期 Windows、Intel Mac、Linux 或手机原生执行器；浏览器查看和控制能力不据此被一并排除。
- 自动发布到短视频平台、投放、付费订阅、协作者共享或团队公共素材库。
- 自动安装任意第三方 Skill、执行未经准入的命令、把字幕/文档内容当作工具权限。
- 覆盖或删除用户原素材、已交付成片，或自动清理用户文件夹。
- 改造核心 Agent runtime、通用 MCP 任务系统、全站品牌或既有选剧业务语义。
- 把视觉蒙太奇、实际配音 MP4、编辑器草稿导出当作已验证基线。它们继续保留为该套 Skill 的能力范围问题，需单独定义并验证交付，不在此处默默删除最终需求。
- 本规格发布本身不代表授权部署、访问生产素材、购买服务、配置凭据或宣称可以上线。

## Further Notes

### 术语

- **选剧工作台**：既有候选浏览、证据、历史和个人选剧能力所在的产品上下文。
- **执行意图**：用户明确要求执行、但可能尚缺设备或素材前提的请求；不同于尚未提交的页面草稿。
- **剪辑任务**：通过准入后持久化、可查询、可控制的单剧请求；来源对话不是必填条件。
- **素材集合**：本次用户选择的文件及在执行受理时冻结的验证身份。
- **输出项**：本次请求中的某一条目标成片；可独立成功、失败及恢复。
- **执行尝试**：执行同一要求的一次尝试；旧尝试无权覆盖当前结果。
- **关联版本**：修改要求形成的新请求及结果，与原版关联且不覆盖原版。
- **可访问性状态**：当前身份能否从生成设备读取成片，不等于剪辑任务是否完成。

### 实施前仍需闭合的门槛

| 项目 | 必须取得的结果 |
|---|---|
| 目标版本与挂载位置 | 核对实际线上选剧界面、部署对应源码和扩展挂载点；保留既有导航，不能把上游或旧本地快照当作部署事实 |
| 安装/更新/签名 | 选择并验证 Apple Silicon 执行器分发机制、权限和更新方式；取得实际需要的签名条件 |
| 运行限额 | 分项确定并验证轮询、离线判定、恢复、停止、并发、文件传输、缓存及准入边界；不能把普通附件限制直接当作视频策略 |
| 模型及质量 | 确定实际模型/配置、长剧规划策略和可复现的质量验收标准 |
| 视觉蒙太奇 | 明确需要的帧级理解、输入许可及首发交付范围，不能假装纯字幕足够 |
| 旁白 | 区分旁白文本/字幕/时间线与实际带配音的视频；没有默认获准的新 TTS 服务 |
| 编辑器草稿 | 明确编辑器/模板版本、打包交付和真实编辑器验收范围 |

推荐推进顺序：先核对目标与贡献点，再建立统一任务契约；其后接入真实 Mac 和素材状态，完成页面/对话映射，最后验证真实出片、结果访问、异常恢复和选剧回归。上述门槛不是要求重新访谈已确认的交互，也不能通过自动选择一个选项来伪造实测结果。

### 证据与发布状态

- 本文来自已完成的工程和设计讨论，继承用户逐项选择及后续推荐项授权；本次只重新组织为 Spec，没有改写已确认的本地/云端边界。
- 现有 V2 是模拟原型，未同步全部后续决策；不能以它证明真实上传、模型规划、转录、渲染或权限验收。
- 会话内独立设计与工程增量核对已完成；外部模型复核没有完成。本文不将缺失覆盖记作通过。
- 本次检查了现有接口、slash 契约、历史/回执及浏览器测试先例，未重新运行应用测试，也未处理真实视频。
- 用户已指定业务仓库 GitHub Issues 为跟踪位置，使用 ready-for-agent 标签。实现经一个集成分支与 PR 提交；合并前不得把未完成的原生、质量或部署验收标记为通过。
