# Trends 客户端（TR-02）

代码：`ggwork_pick/observe/trends/{source,client,parse,egress}.py`。设计 4.1、4.3、4.4、4.10、4.11；计划 TR-02、D20。

## 请求链

一个查询单元（身份 × geo × 查询形态 × 时间范围，设计 4.5）最多发三次 HTTP，都经 `trends.google.com`，参数都放在查询串里：

| 步骤 | 路径 | 参数 |
|---|---|---|
| 预热（每个 cookie 罐每天最多一次） | `/trends/?geo=US` | 无 |
| explore（默认 GET，可切 POST） | `/trends/api/explore` | `hl=en-US`、`tz=0`、`req`（comparisonItem 每个词一项：keyword、geo、time；category 0；property） |
| multiline（走势） | `/trends/api/widgetdata/multiline` | `hl`、`tz`、`req`（TIMESERIES widget 的 request 原样回传）、`token` |
| relatedsearches（相关查询） | `/trends/api/widgetdata/relatedsearches` | `hl`、`tz`、`req`（该词的 RELATED_QUERIES widget）、`token` |

- geo、颗粒度、搜索属性都用合同写法：geo 是 `WW` 或两位国家码，发给 Google 时 `WW` 写成空串；颗粒度 H 对应 `now 7-d`，D 对应 `today 1-m`，其他时间范围直接拒绝（测试钉住它与合同 `TRENDS_WINDOW_KINDS` 相同）；搜索属性 `search_property` 是 `web` 或 `youtube`（合同 `DISCOVERY_PROPERTIES`），发给 Google 时 web 写成空串。
- explore 的方法由构造参数 `explore_method` 决定，默认 GET（网页客户端的做法）；pytrends 用 POST，阶段 0 两种都试，不用改代码。`fetch(..., explore_method=...)` 可以按单元覆盖，阶段 0 的 POST 重复单元就这样和其余单元共用一个客户端。只换方法，参数照样在查询串里，正文为空；multiline 与 relatedsearches 固定 GET。
- 一个请求最多 5 个词，各线按请求顺序返回，同一刻度。裸剧名那条线用 `bare` 标出，泛词剧 `bare` 为空，只请求意图变体（设计 4.7）。
- 不跟随任何跳转，不接受同意墙，不登录，不解验证码。

## 十种 fetch_status

| 值 | 含义 | 分组 |
|---|---|---|
| `ok` | 完整序列，至少一个非零点 | 可判定 |
| `ok_zero` | 完整序列，全为 0，按 sparse 处理 | 可判定 |
| `no_data` | HTTP 成功但没有点（或相关查询两组都空，或 explore 没给相关查询 widget） | 不参与判定，不算失败 |
| `rate_limited` | 429 | 限流信号 |
| `blocked_redirect` | 跳到 sorry（验证码）页或 consent（同意墙）页，`captcha_or_consent` 为真。TR-03 把它当作墙，当天熄火 | 限流信号 |
| `html_body` | API 路径返回 HTML | 限流信号 |
| `forbidden` | 403 | 限流信号 |
| `server_error` | 5xx | 可重试一次 |
| `timeout` | 没有拿到 HTTP 回答：超时、连接失败、对端中途断开（httpx 的 TimeoutException、NetworkError、RemoteProtocolError、ProxyError） | 可重试一次 |
| `parse_error` | 回答形状不符；设计没点名的 4xx；sorry、consent 以外的任何跳转（站内挪路径、跳到 accounts 等）；200 的正文超过上限或解不开；本机发不出去的请求（LocalProtocolError、UnsupportedProtocol 等） | 合同变了，不重试，不算限流 |

- 只有 `ok`、`ok_zero` 带值。任何失败都不带值，不会写成全零（反例 1）。时间线里任何一个点缺 `value` 或 `value` 为 null，整条回答记 `parse_error`，不补 0。
- 跳转一律不跟随。跳转的去向照样记进请求行（`redirect_kind`：sorry、consent、same_host、other；`redirect_host`），只有 sorry、consent 两种记 `blocked_redirect`。这是 TR-03 的前提：它把 `blocked_redirect` 映射成墙，当天熄火，还计入 7 天 3 次停用直连；站内跳转若也记成它，Google 挪一次 API 路径就会让当天熄火。预热页在 trends.google.com 站内跳转仍算预热成功。
- 状态码先于正文：只有 200 的回答才因正文超过上限（`max_body_bytes`）或解压失败记 `parse_error`；429、403、5xx、3xx 的正文读到上限就截断，状态照常判定，限流信号不会因为错误页太大而丢失。
- 一个单元里第一个失败的请求结束这个单元；还没取到的部分都记这个失败状态，不带值。走势已成功、相关查询被 429 时，走势保留，单元状态记 `rate_limited`，熔断照样触发。
- `userType` 原样取自 explore 里所用 widget 的 `request.userConfig.userType`；`isPartial`、`hasData` 逐点原样保留，缺这个键就记 null，不补 false。
- 原始行（`Line.as_raw()`，写 `ggwp_obs_raw`）的 `time` 保留回答里的原样，网页客户端给的是字符串形式的纪元秒（设计 3.5「原样的 time」）；计算用的整数秒另由 `Line.times` 给出。请求参数（`TrendsQuery.request_params()`）里的属性记合同写法 `property: web|youtube`。
- 相关查询 widget 的查找顺序：关键词原样相等；按大小写与空白规范化后相等；按位置，第 i 个词找 `RELATED_QUERIES_i`（单个词时找唯一的那个 RELATED_QUERIES）。explore 根本没给 RELATED_QUERIES 时记 `no_data` 加 `widget_missing`，这是闸门 B 要看的信号；给了却一个都对不上（或按位置找到的那个写的是另一个请求词），记 `parse_error`，不当成闸门 B 不通过。

## 与执行器的接线（TR-14）

- 构造 `TrendsClient` 时 `gate` 与 `on_request` 都是必填关键字参数，没有默认值（同 D42 的道理）：执行器漏接任何一个，构造时就报错。
- `gate(step)` 在每次 HTTP 之前等待，预热也一样。限速器等待、预算扣减、续租检查都放在这里；它抛出的异常原样上抛，这次请求不发。
- `on_request(record)` 收到每次已发出请求的记录，对应 `ggwp_obs_requests` 的一行：阶段、标签（预算项）、开始时刻、耗时、HTTP 状态、fetch_status、跳转类型与主机、userType、字节数、错误类名、出口读数。记录里没有 cookie，也没有序列值。
- 客户端自己不重试、不睡眠。429 不重试、5xx 或超时隔 30–60 秒重试一次、熔断与熄火，都是 TR-03 的策略，由执行器按 `FetchResult.status` 与 `captcha_or_consent` 执行。
- 预热按会话的 target_date 传 `day`（D23），由罐的 `can_warm(day)` 判断：`warmed_on >= day` 时不发请求，返回的 `status` 为 None。拿到回答就算用掉当天那一次：成功的把 cookie 放进罐；被拒的（限流信号或 `parse_error`）保留原罐的 cookie，只记下 `warmed_on`（设计 4.4）。超时与 5xx 不算：没拿到可用的回答，罐原样不动，执行器按 TR-03 重试一次时预热会真的再发（设计 4.2）。
- `capture(record, body)` 可选，只给阶段 0 存原始响应用；cron 里不开。

## cookie 罐

- 整个通道只有一个罐：TR-04 的 `trends/cookies.CookieJar`（计划第 5 节）。客户端只依赖 `source.TrendsJar` 这个最小协议：`user_agent`、`warmed_on`、`cookie_names`、`can_warm(day)`、`request_headers(now)`、`updated(cookies, *, now)`、`warmed(cookies, *, day, now)`。罐不可变，每次更新都换新对象；执行器从 `client.jar` 取出交给 TR-04、TR-13 加密保存。新罐用 `CookieJar.fresh(DEFAULT_USER_AGENT)`，UA 跟罐走，不轮换。
- 请求头取自 `jar.request_headers(now)`：UA 与 Cookie 总是一起出去，过期的 cookie 不发。
- Set-Cookie 由 `parse.set_cookies(lines, now=...)` 逐行解析成与 TR-04 `Cookie` 字段一一对应的对象（name、value、domain、path、expires）：Max-Age 优先于 Expires，按读到回答的时刻算成 Unix 秒；Max-Age ≤ 0 或 Expires 已过，记成 `removal`（空值、已过期），罐更新时连同被它替换的那条一起丢掉；Domain 去掉开头的点、转小写，必须是 trends.google.com 本身或它的上级域，否则整行丢弃（同浏览器）；没有 Domain 记 `""`（只属本主机）；没有 Path 或 Path 不以 `/` 开头记 `/`，因为罐不按域与路径匹配，只用它们区分同名 cookie；名字、值带请求头放不下的字符，整行丢弃。空值是合法的值，不当作删除。
- 只有成功的回答（非失败状态）才会更新 cookie；被限流、被跳转、超时的回答即使带 Set-Cookie 也不采用，原罐保留（设计 4.4）。
- cookie 只放进按罐拼出的 Cookie 头；httpx 自带的 cookie 存储设成全部拒收。cookie 值不进 repr、日志、异常文本、请求记录；`TrendsClient` 的 repr 只用罐的 UA、cookie 名与 `warmed_on` 拼出，不依赖罐自己的 repr。

## 出口探测（D20）

- 变量 `PICK_OBS_EGRESS_ECHO_URL`，默认不设，即关闭：不发任何回显请求，请求行的出口 IP 与测量时刻都记 null。
- 设这个变量要先经 U13 批准（对第三方的外发请求）。只收 `https://` 地址，不带账号，否则以退出码 2 拒绝。
- 开启后：会话第一个请求前测一次，之后每 20 个请求测一次，执行器每次熔断后调 `invalidate()` 再测一次。每个请求记录与结果都带最近一次读数和它的测量时刻。回显失败时沿用上一次读数（连同上一次的时刻），等下一个测量点再试，不逐请求重试。
- 回显走单独的 httpx 客户端，不带 Trends 的 cookie，也不用 Trends 的 UA。

## 夹具与阶段 0

- `tests/fixtures/trends/*.json` 目前都是按设计 4.1 与直连调研构造的（`constructed: true`），剧名、token、cookie 都是合成值。每个文件的 `pending_stage0` 列出待阶段 0 实测确认的形状。
- 阶段 0（TR-05）拿到真实响应后：脱敏（cookie 换成含 `synthetic` 的假值，token 换掉），替换对应夹具，`constructed` 改为 false，`pending_stage0` 删去已确认的条目。形状与构造的不同时，先改解析器与测试，再换夹具。
- 待确认的要点汇总：
  - explore 是否接受 GET（网页客户端用 GET，pytrends 用 POST）：用 `explore_method` 两种都试；
  - `userType` 所在位置与取值，会话被标成 SCRAPER 时是否连 RELATED_QUERIES 也拿掉；
  - 相关查询 widget 的 id 写法与关键词所在路径；
  - `time` 是否为字符串形式的纪元秒，`isPartial` 是否只出现在末尾，`now 7-d` 是否 169 个点；
  - `rankedList` 是否第一组 top、第二组 rising，Breakout 怎么表示；
  - 预热页路径、哪些 cookie 必需；API 被限时是跳 sorry 页还是直接 429；200 返回 HTML 的情形是否存在；
  - 空响应长什么样（全零还是空的 timelineData）。

## 集成说明（批次 1a 已合并 TR-02、TR-03、TR-04）

- **一个罐**：已完成。`source.py` 里原先的 `SetCookie` 替身已删去，换成 `from ggwork_pick.observe.trends.cookies import Cookie as SetCookie`，解析器直接造出罐自己的 cookie；`parse._set_cookie` 捕获 `ValueError`，TR-04 的校验拒收的行照样丢弃。测试里的 `FakeJar` 保留。
- **三条跨模块测试**（任务分支上是 skip，集成分支上都在跑）：`test_set_cookie_is_tr04_cookie`、`test_client_drives_tr04_cookie_jar`（客户端驱动 TR-04 真罐走预热、轮换、被拒）、`test_breaker_reads_only_walls_as_walls`（经 TR-03 的 `signal_of(status, captcha_or_consent=...)`：`captcha_or_consent` 是必填关键字参数，执行器传 `FetchResult.captcha_or_consent`；只有 sorry、consent 跳转映射成 WALL，站内跳转是 `parse_error`，即 NEUTRAL）。
- **`trends/__init__.py`**：三个分支各建了一份，合并时三份逐字相同，自动合并，没有冲突；内容只是包说明，不导入任何东西。
- **接线**（TR-05、TR-14）：`TrendsClient(jar=..., clock=..., gate=..., on_request=...)`，罐用 TR-04 的 `CookieJar`（新罐 `CookieJar.fresh(DEFAULT_USER_AGENT)`，或从状态存储读出的罐）；会话结束或每次熔断后把 `client.jar` 交回存储。
- **deselect 的写法**：pytest 的 rootdir 是 `customizations/pick-workbench`，节点 id 是 `tests/test_managed_copy.py::...`，所以在仓库根目录跑时 `--deselect customizations/pick-workbench/tests/test_managed_copy.py` 匹配不上；任务分支上要跳过它，用 `--deselect tests/test_managed_copy.py` 或 `--ignore=customizations/pick-workbench/tests/test_managed_copy.py`。
