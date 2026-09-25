# GSC 服务账号与客户端（TR-06）

代码：`ggwork_pick/observe/gsc/auth.py`（凭据、私钥、断言）、`query.py`（请求体与响应解析）、`client.py`（token、请求、错误分类）。设计 5.1；计划 TR-06。签名、私钥归一化、403 提示照抄 RealShort 的 `src/lib/gsc-api.ts` 与 `gsc-encoding.ts`（816ca2e）；入库、窗口、合并逻辑不照抄。

## 凭据与变量

两种来源，只设一种，同时设了就拒绝（退出码 2，不发任何请求）。

| 变量 | 用在哪 | 内容 |
|---|---|---|
| `PICK_GSC_SA_EMAIL` | gsc 服务（Railway） | 服务账号邮箱，形如 `name@project.iam.gserviceaccount.com` |
| `PICK_GSC_SA_PRIVATE_KEY` | gsc 服务（Railway） | 服务账号 JSON 里 `private_key` 那一段 PEM |
| `PICK_GSC_SA_FILE` | 仅本机 | GCP 下载的服务账号 JSON 密钥文件路径，权限必须是 600 |
| `PICK_GSC_SITE_URL` | 两处都要 | 属性标识，见下文 |

- 私钥只认 PKCS#8，即以 `-----BEGIN PRIVATE KEY-----` 开头的那种；服务账号 JSON 里给的就是它。`BEGIN RSA PRIVATE KEY`（PKCS#1）和 `BEGIN ENCRYPTED PRIVATE KEY`（带口令）都拒绝。
- 贴进变量时，两端多带的引号会去掉，字面的 `\n` 会还原成换行，所以把 JSON 里那一行原样粘进去即可。
- 私钥、签好的断言和 access token 不进日志、异常消息和 repr；配置错误只写变量名，不写值。
- 服务账号 JSON 里只用 `client_email` 与 `private_key`，`token_uri` 等其他字段一律忽略，断言只发往 `https://oauth2.googleapis.com/token`。
- 私钥只放 gsc 服务。前端和 gateway 都不设这几个变量（设计 3.4）。

## 属性名

`PICK_GSC_SITE_URL` 写 GSC 里属性的标识：网域属性写 `sc-domain:dramashortstv.com`，URL 前缀属性才写 `https://dramashortstv.com/`（以 `/` 结尾，全小写）。属性名写错时，GSC 回的是 403「User does not have sufficient permission」，看着像没授权。所以客户端在发请求之前先校验写法，403 的提示里也把这一条排在第一位。

## 请求约定

- scope 固定为 `webmasters.readonly`，签名算法 RS256，断言有效期 1 小时。
- 每轮进程内一个 token，到期前 5 分钟换新。遇到 401 就换新 token 重试一次；其余错误客户端一律不重试，由调用方（TR-21 的轮次）决定。原因是每站配额与 RealShort 共用。
- `rowLimit` 固定 25000，`dataState` 必须写明，`aggregationType` 与 `dimensionFilterGroups` 原样透传。`hour` 维度与 `hourly_all` 成对出现。不发 `startRow`，新鲜数据不翻页。
- 返回行数等于 25000 时标 `truncated`，调用方按国家拆分重发。没有 `rows` 字段表示「在 GSC 返回的数据里未观测到」，不是 0。缺计数字段的行会让整个请求失败，不会当成 0。
- `metadata` 原样保存，并解析出 `first_incomplete_hour`（带时区偏移的时刻）与 `first_incomplete_date`，保留 `responseAggregationType`。

## 错误种类

每个失败都是 `GscRequestError`，不会产出数值。`kind` 取下表之一；`detail` 存 Google 的原文（截断到 500 字），只供 TR-07 的探针打印，不进消息。

| kind | 含义 | 处理 |
|---|---|---|
| `quota_short` | 短期配额用完：429，或 403 带 `rateLimitExceeded`、`userRateLimitExceeded`，或配额错误没说明是哪种 | 本轮停发，下一轮再试 |
| `quota_long` | 长期（按天）配额用完：原因词为 `dailyLimitExceeded`，或正文、`quota_limit` 提到 per day、daily、long-term | 今天剩下的轮次都停发 |
| `forbidden` | 403，不是配额 | 先核对属性名，再核对服务账号是否已由属性 Owner 加成用户（U2） |
| `unauthorized` | 换新 token 重试一次后仍是 401 | 核对服务账号是否仍在属性里 |
| `token_rejected` | token 端点拒绝：`invalid_grant`、`unauthorized_client`、`invalid_client` 等 | `invalid_grant` 查私钥是否被删、本机时钟；`unauthorized_client` 查 GCP 项目是否启用 Search Console API |
| `bad_request` | 400 | 查维度组合、日期、过滤写法；`includingRegex` 超长或不合 RE2 也是 400 |
| `not_found` | 404 | 查属性名与接口路径 |
| `redirect` | 3xx，不跟随 | 不是 GSC API 的正常回答 |
| `server_error` | 5xx | 这次没有数据，稍后重取由轮次决定 |
| `http_error` | 其他状态码 | 看 `detail` |
| `timeout` | 90 秒内没有收完 | 同 `server_error` |
| `connection` | 连接失败 | 同 `server_error` |
| `too_large` | 响应超过 32 MB | 不该发生，看请求形状 |
| `malformed` | 200，但正文不合约定（不是 JSON、行形状不对、水位字段解析不了、token 响应缺字段） | 保存原文交 TR-07 核对 |

每个 HTTP 请求（token 与查询，失败的也算）都记一行日志，并交给 `on_response` 回调，轮次靠它计请求数与配额错误。

## 待 TR-07 实测回填

实测命令与读法见 `gsc-probe.md`：`gsc-probe` 一次跑完 P1–P7，下列各条的实测值在 `gsc-probe-<date>.json` 的 `backfill` 与 raw 文件里（实测待 U1、U2）。

- 真实的短期、长期配额错误正文：换掉测试里按文档信封构造的夹具，必要时收窄分类规则。
- `metadata` 字段的实际拼法：现在两种拼法都接受，蛇形优先，两种都在且值不同就判 `malformed`。
- 小时数据加 `byPage` 是否被接受：看 `responseAggregationType`。
- `includingRegex` 的长度上限（TR-23a 分块用）。
