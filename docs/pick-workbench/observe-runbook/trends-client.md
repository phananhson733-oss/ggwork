# Trends 客户端（TR-02）

代码：`ggwork_pick/observe/trends/{source,client,parse,egress}.py`。设计 4.1、4.3、4.4、4.10、4.11；计划 TR-02、D20。

## 请求链

一个查询单元（身份 × geo × 查询形态 × 时间范围，设计 4.5）最多发三次 HTTP，都是 GET，都经 `trends.google.com`：

| 步骤 | 路径 | 参数 |
|---|---|---|
| 预热（每个 cookie 罐每天最多一次） | `/trends/?geo=US` | 无 |
| explore | `/trends/api/explore` | `hl=en-US`、`tz=0`、`req`（comparisonItem 每个词一项：keyword、geo、time；category 0；property） |
| multiline（走势） | `/trends/api/widgetdata/multiline` | `hl`、`tz`、`req`（TIMESERIES widget 的 request 原样回传）、`token` |
| relatedsearches（相关查询） | `/trends/api/widgetdata/relatedsearches` | `hl`、`tz`、`req`（该词的 RELATED_QUERIES widget）、`token` |

- geo 用合同写法：`WW` 或两位国家码；发给 Google 时 `WW` 写成空串。颗粒度 H 对应 `now 7-d`，D 对应 `today 1-m`，其他时间范围直接拒绝。
- 一个请求最多 5 个词，各线按请求顺序返回，同一刻度。裸剧名那条线用 `bare` 标出，泛词剧 `bare` 为空，只请求意图变体（设计 4.7）。
- 不跟随任何跳转，不接受同意墙，不登录，不解验证码。

## 十种 fetch_status

| 值 | 含义 | 分组 |
|---|---|---|
| `ok` | 完整序列，至少一个非零点 | 可判定 |
| `ok_zero` | 完整序列，全为 0，按 sparse 处理 | 可判定 |
| `no_data` | HTTP 成功但没有点（或相关查询两组都空，或 explore 没给相关查询 widget） | 不参与判定，不算失败 |
| `rate_limited` | 429 | 限流信号 |
| `blocked_redirect` | API 路径上的任何跳转；跳到 sorry 或 consent 页时 `captcha_or_consent` 为真 | 限流信号 |
| `html_body` | API 路径返回 HTML | 限流信号 |
| `forbidden` | 403 | 限流信号 |
| `server_error` | 5xx | 可重试一次 |
| `timeout` | 没有拿到 HTTP 回答：超时或连接失败 | 可重试一次 |
| `parse_error` | 回答形状不符、设计没点名的 4xx、正文超过上限 | 合同变了 |

- 只有 `ok`、`ok_zero` 带值。任何失败都不带值，不会写成全零（反例 1）。
- 一个单元里第一个失败的请求结束这个单元；还没取到的部分都记这个失败状态，不带值。走势已成功、相关查询被 429 时，走势保留，单元状态记 `rate_limited`，熔断照样触发。
- `userType` 原样取自 explore 里所用 widget 的 `request.userConfig.userType`；`isPartial`、`hasData` 逐点原样保留，缺这个键就记 null，不补 false。

## 与执行器的接线（TR-14）

- 构造 `TrendsClient` 时 `gate` 与 `on_request` 都是必填关键字参数，没有默认值（同 D42 的道理）：执行器漏接任何一个，构造时就报错。
- `gate(step)` 在每次 HTTP 之前等待，预热也一样。限速器等待、预算扣减、续租检查都放在这里；它抛出的异常原样上抛，这次请求不发。
- `on_request(record)` 收到每次已发出请求的记录，对应 `ggwp_obs_requests` 的一行：阶段、标签（预算项）、开始时刻、耗时、HTTP 状态、fetch_status、跳转类型与主机、userType、字节数、错误类名、出口读数。记录里没有 cookie，也没有序列值。
- 客户端自己不重试、不睡眠。429 不重试、5xx 或超时隔 30–60 秒重试一次、熔断与熄火，都是 TR-03 的策略，由执行器按 `FetchResult.status` 与 `captcha_or_consent` 执行。
- 预热按会话的 target_date 传 `day`（D23）：同一天第二次调用不发请求。被拒绝的预热也算当天那一次。
- `capture(record, body)` 可选，只给阶段 0 存原始响应用；cron 里不开。

## cookie 罐

- `Jar` 是不可变值：UA 与 cookie 绑在一起，每次更新都换一个新对象。执行器从 `client.jar` 取出后交给 TR-04 的加密存储。
- 只有成功的回答（非失败状态）才会更新 cookie；被限流、被跳转、超时的回答即使带 Set-Cookie 也不采用，原罐保留（设计 4.4）。
- cookie 只放进按罐拼出的 Cookie 头；httpx 自带的 cookie 存储设成全部拒收。cookie 值不进 repr、日志、异常文本、请求记录。

## 出口探测（D20）

- 变量 `PICK_OBS_EGRESS_ECHO_URL`，默认不设，即关闭：不发任何回显请求，请求行的出口 IP 与测量时刻都记 null。
- 设这个变量要先经 U13 批准（对第三方的外发请求）。只收 `https://` 地址，不带账号，否则以退出码 2 拒绝。
- 开启后：会话第一个请求前测一次，之后每 20 个请求测一次，执行器每次熔断后调 `invalidate()` 再测一次。每个请求记录与结果都带最近一次读数和它的测量时刻。回显失败时沿用上一次读数（连同上一次的时刻），等下一个测量点再试，不逐请求重试。
- 回显走单独的 httpx 客户端，不带 Trends 的 cookie，也不用 Trends 的 UA。

## 夹具与阶段 0

- `tests/fixtures/trends/*.json` 目前都是按设计 4.1 与直连调研构造的（`constructed: true`），剧名、token、cookie 都是合成值。每个文件的 `pending_stage0` 列出待阶段 0 实测确认的形状。
- 阶段 0（TR-05）拿到真实响应后：脱敏（cookie 换成含 `synthetic` 的假值，token 换掉），替换对应夹具，`constructed` 改为 false，`pending_stage0` 删去已确认的条目。形状与构造的不同时，先改解析器与测试，再换夹具。
- 待确认的要点汇总：
  - explore 是否接受 GET（网页客户端用 GET，pytrends 用 POST）；
  - `userType` 所在位置与取值，会话被标成 SCRAPER 时是否连 RELATED_QUERIES 也拿掉；
  - 相关查询 widget 的 id 写法与关键词所在路径；
  - `time` 是否为字符串形式的纪元秒，`isPartial` 是否只出现在末尾，`now 7-d` 是否 169 个点；
  - `rankedList` 是否第一组 top、第二组 rising，Breakout 怎么表示；
  - 预热页路径、哪些 cookie 必需；API 被限时是跳 sorry 页还是直接 429；200 返回 HTML 的情形是否存在；
  - 空响应长什么样（全零还是空的 timelineData）。
