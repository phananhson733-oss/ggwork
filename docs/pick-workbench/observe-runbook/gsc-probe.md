# GSC 实测与 URL 清单导出（TR-07）

代码：`ggwork_pick/observe/gsc/` 下的 `probe.py`（七项的框架、记录与判定词）、`probe_checks.py`（P1、P2、P3、P5、P6）、`probe_pages.py`（P4、P7）、`probe_report.py`（报告与 JSON）、`urls.py`（URL 形态与剧名键）、`export_urls.py`（URL 清单与正对照候选）、`pacer.py`、`artifacts.py`、`command.py`；命令 `admin/cmd_gsc_probe.py`、`admin/cmd_gsc_export_urls.py`。设计 5.1、5.2、5.5、4.11；计划 TR-07。

## 现状：实测待 U1

本机还没有 GSC 服务账号密钥（U1、U2 未做）。代码、解析与汇总测试已经就绪：测试全部跑在 MockTransport 上的模拟 GSC（`tests/observe/gsc_sim.py`）上，没有访问过 Google。拿到密钥后按下面两条命令各跑一次，报告、结论 JSON 与清单写到 artifacts 目录，交 G2 审。

## 准备：U1、U2

1. **U1**：在 GCP 项目里建一个专用服务账号（不复用 RealShort 的，两边的钥匙要能分别吊销），启用 Search Console API，生成 JSON 密钥。本机存成 600 权限的文件：

   ```sh
   mkdir -p ~/.config/ggwork-obs && chmod 700 ~/.config/ggwork-obs
   mv ~/Downloads/<下载的密钥文件>.json ~/.config/ggwork-obs/gsc-sa.json
   chmod 600 ~/.config/ggwork-obs/gsc-sa.json
   ```

2. **U2**：属性 Owner 在 Search Console 的「设置 → 用户和权限」里添加这个服务账号的邮箱，权限选「受限」（Restricted）。加不了，说明当前登录的账号不是 Owner，请实际的 Owner 操作。
3. 变量（凭据的读法与 gsc 服务相同，见 `gsc-client.md`）：

   ```sh
   export PICK_GSC_SA_FILE=~/.config/ggwork-obs/gsc-sa.json
   export PICK_GSC_SITE_URL=sc-domain:dramashortstv.com
   ```

   不要同时设 `PICK_GSC_SA_EMAIL`、`PICK_GSC_SA_PRIVATE_KEY`，两种来源都设会被拒绝。

## 一条命令跑完 P1–P7

在带 `backend/.venv` 的检出的仓库根目录执行（`PYTHONPATH` 让这份检出的代码优先于 venv 里装的副本）：

```sh
PYTHONPATH=customizations/pick-workbench backend/.venv/bin/python -m ggwork_pick.observe.admin gsc-probe
```

| 选项 | 默认 | 作用 |
|---|---|---|
| `--out-dir` | `~/.gstack/projects/ggwork-deerflow/artifacts` | 输出目录，在仓库之外；不存在就新建为 700 |
| `--pause` | `1.0` | 两次请求之间停几秒（每站配额与 RealShort 共用） |
| `--no-raw` | 不设 | 不保存原始回答 |

输出三份文件，都是 600 权限；同一天再跑一次，整组文件加 `-2`、`-3` 后缀，从不覆盖：

| 文件 | 内容 |
|---|---|
| `gsc-probe-<date>.md` | 报告：汇总表，七项各自的结论与依据，回填表，请求明细 |
| `gsc-probe-<date>.json` | 同样的结论，机器可读；`backfill` 是给 TR-21、TR-23a、TR-06 的参数 |
| `gsc-probe-<date>-raw.jsonl` | 每次 searchAnalytics.query 的请求体、HTTP 状态与响应正文，一行一次；不含任何请求头，也不含 token 交换 |

- `<date>` 是运行开始时的 UTC 日期。
- 退出码：0，七项都有明确结论；1，仍有「未定」或「未测」（文件照样写出，先看报告里的原因）；2，缺凭据或属性名写错，一个请求都没发。
- 请求量：约 40 次查询加 1 次 token 交换，一次一个，间隔 1 秒，一两分钟跑完。没有 page×query 组合（最贵的那种）。

## 七项测什么、怎么判

执行顺序是 P1、P7、P3、P2、P4、P5、P6：P2 要拿 P3 的 C 比较，P4 要从 C 与 P7 里挑种子页。报告按 P1 到 P7 排。

| 项 | 请求 | 判定 |
|---|---|---|
| P1 接入验收 | `[date]`/`all`，近 4 个 PT 日 | 成功即支持，同时证明 U2 由 Owner 完成；失败（403、token 被拒等）为不支持，报告里给出 Google 原文与提示，其余六项记未测 |
| P2 A′ | `[hour]`/`hourly_all`，默认聚合与 `byPage` 各一次，昨天与今天 | 回答的 `responseAggregationType` 是 `byPage` 为支持；被忽略或 400 为退路（全站层 24 小时准入按 PT 日用 A″）。支持时另出逐小时明细缺口表（A′ 减 C 的合计，只看水位之前的完整小时），作为 τ 的参照 |
| P3 C | `[hour,page,country]`/`hourly_all`，昨天、今天各一片；昨天再按曝光最多的三个国家各发一次 `country equals` | 不满额为支持；满额而拆分后都不满额也是支持（要按国家拆分发送）；拆分后仍满额，或维度组合被拒而 `[hour,page]` 可用，为退路（`[hour,page]` 加日级国家）。拆分结果与整片逐行比较 |
| P4 正则与 Vh、Vd | 种子是昨天 C 里曝光最多、能被 `page_set` 锚定正则整串匹配的新剧目页；用 `pageset.page_set` 造它的正则 | 四个语义用例：page_set 正则取到的页面是否与 `page_set.contains` 相符，未锚定的 id 能否命中（部分匹配则正则必须锚定），`^` 是否生效，前瞻是否被拒（只认 RE2）。长度上限：1024 起翻倍到 32768，遇到 400 后二分到 64 字符以内，填充用形同真实 URL、永不命中的备选项。Vh（`[hour,country]`）、Vd（`[date,country]` 的 `all` 与 `final`）带同一正则，与明细逐格按前提 3 的容差比较。Vh 或 Vd(all) 被拒为不支持；只有 Vd(final) 被拒为退路 |
| P5 水位 | 近三个 PT 日各一次 A，前天的 C，近 6 天的 `[date]`/`final`；连同 P1、P3 的回答 | 今天的 A 带 `first_incomplete_hour` 为支持，报出字段名（蛇形还是驼峰）、水位与滞后小时数，以及跨水位那天的 C 是否带该字段（不带即设计 5.3 的 `watermark_absent`）；A 不带水位为不支持 |
| P6 A″ | `[date]`/`byPage`，`final` 与 `all` 各一次，截止昨天的 16 个 PT 日 | 两次都成功为支持；逐日列出两份的曝光与差异，哪些日子还没有 final 行 |
| P7 URL 写法 | `[page]`/`all`，近 7 个 PT 日 | 新剧目页全在属性主机上、全部能被 `page_set` 锚定正则整串匹配为支持；出现别的主机（如 `www.`）、查询串、片段、非 https 为退路，逐条列出原因。另列各类页面（新剧目页、旧站 `/detail/`、`/video-play/`、`?id=`、博客、首页等）的条数与曝光，以及 slug 的百分号编码与大小写 |

## 判定词

- **支持**、**不支持**、**退路**：明确结论。退路指设计已写明的替代做法，结论里写出是哪一条。
- **未定**：本该给出结论的回答没拿到（5xx、超时、连接失败、正文不合约定、配额错误）。看报告里的错误，过一会儿重跑。
- **未测**：运行在前面停下了。P1 不通过，其余六项都是未测；任何一次配额错误都让运行停在那里，当项记未定，之后的记未测。配额错误的原文保留在 raw 文件里，用来替换 TR-06 按文档构造的配额夹具。

## 回填

`gsc-probe-<date>.json` 的 `backfill` 汇总了七项得出的参数，报告的回填表逐个写明用在哪里。G2 审过报告之后：

- TR-21：`a_prime_supported`（每轮取 A′ 还是只取 A″）、`c_shape` 与 `c_needs_country_split`（C 的形态与拆分）、`final_latest_day` 与 `final_missing_days`（E 与 A″f 的取法）。
- TR-23a：`regex_chunk_length_suggested`（`PageSet.regex_chunks` 的 `max_length`，取实测上限的 90%）、`regex_partial_match` 与 `regex_anchor_honored`（锚定）、`vh_supported`、`vd_supported`。
- D25 与 `pageset.SITE_HOST`：`site_host`、`new_page_hosts`、`new_page_shape_mismatch`。只有 P7 为支持时，`SITE_HOST` 保持现值；为退路时，由 TR-23a 决定正则怎么兼容那些写法。
- TR-06：`metadata_spelling`（确认后 `query.py` 可只收一种拼法）；raw 文件里真实的配额错误正文（若这次遇到）。

## URL 清单与正对照候选

```sh
PYTHONPATH=customizations/pick-workbench backend/.venv/bin/python -m ggwork_pick.observe.admin gsc-export-urls
```

| 选项 | 默认 | 作用 |
|---|---|---|
| `--days` | `90` | 清单窗口的 PT 日数，截止到昨天 |
| `--data-state` | `all` | 清单用的 dataState（`all` 或 `final`） |
| `--candidate-days` | `28` | 正对照候选看的查询窗口 |
| `--candidates-shown` | `60` | 候选文件里列出的条数 |
| `--no-candidates` | 不设 | 只导清单 |
| `--max-requests` | `400` | 请求上限，到了就停，什么都不写 |
| `--out-dir`、`--pause` | 同上 | 同上 |

**清单**（`gsc-urls-<date>.jsonl`，给 TR-08）

- 每行 `{"raw_url": …, "clicks": …, "impressions": …}`。`raw_url` 是 GSC 返回的原串，不解码、不去查询参数；只对完全相同的串去重，计数跨请求求和；按码点顺序排列。清单包含全部页面（新页、旧页、博客、首页），由 RealShort 的导出脚本自己判断哪些是旧页。
- 拆分：按自然月发 `[page]`；满额的月先取这个月的 `[country]` 清单，再逐国用 `country equals` 过滤；某国仍满额就把日期对半拆，直到一天。用到的每份响应都不满额；拆到一天一国仍满额的，照样收进清单，记进 manifest 的 `incomplete_leaves`，清单标 `complete: false`，命令以 1 退出，提醒先看再交出去。新鲜数据不翻页（设计 5.2）。
- `gsc-urls-<date>.manifest.json`：`file`、`sha256`（清单文件的 sha256，就是 TR-08 manifest 里的「输入 sha256」）、`site_url`、`generated_at`、`collector_version`、`window`（起止 PT 日、天数、时区）、`data_state`、`dimensions`、`search_type`、行数与点击、曝光合计、`requests`、`complete`、`incomplete_leaves`，以及每个请求切片的 `leaves`（`used` 为 false 的是满额后被拆开的那一片）。

**正对照候选**（`gsc-positive-controls-<date>.json`，给 TR-05）

- 规则：取近 28 天的 `[query,country]`，查询词按 RealShort 的剧名规则（小写、去撇号、NFC、非字母数字折成一个连字符）得到的键，与清单里新剧目页 slug 解码后的键完全相同，就算「精确剧名查询」。剧名被查询时 GSC 展示的是首页也照样算，只要这部剧的页面在 90 天里出现过。slug 截到 60 个码点的长剧名匹配不到。
- 每个候选给出：剧名键、曝光与点击、命中的查询原文、前 5 个国家（GSC 的小写 alpha-3，连同 market-map-v1 配对的 Trends geo，没有配对的记 null）、对应的新剧目页（locale、book_id、原串）。
- 阶段 0 从里面挑 15–20 部：按 `book_id` 对上共享剧库的身份，按 `top_countries` 选 geo（设计 4.11「按真实所在国查」），泛词剧与已下架剧另行剔除。
- 请求量：清单约 4 次（各月都不满额时），满额的月每月多一次国家清单与逐国请求；候选 1 次起。任何一次请求失败或到了请求上限，什么都不写，以 1 退出。

## 安全

- 凭据只从环境变量读。私钥、签好的断言、access token 不进任何文件、日志与终端输出；raw 文件只记查询的请求体与响应正文。
- 输出里是站点自己的数据（URL、计数；报告里有少量例子 URL），放在仓库之外，不要提交进 git。
- 实测命令本身不访问查询维度；导出命令取查询维度，但只把与剧名完全相同的查询写进候选文件，其余查询词不落盘。
