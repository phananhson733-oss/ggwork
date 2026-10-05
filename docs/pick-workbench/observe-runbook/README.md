# 趋势雷达运维手册（目录）

设计：`docs/plans/2026-09-25-trends-radar-design.md`；实现计划：`docs/plans/2026-09-25-trends-radar-impl-plan.md`（下称「计划」）。

本目录一个主题一个文件，各任务只写自己的主题文件，不共同编辑同一个文件（计划 D35）。本页目前只是目录；索引正文与跨主题部分（配置与变量名、自检、回滚矩阵、两个开关、新迁移上线流程、连接账演练等）由 TR-29 补写。

## 主题文件

| 文件 | 内容 | 任务 | 状态 |
|---|---|---|---|
| `packaging.md` | trends cron 的打包路径、Railway 配置与部署验证 | TR-15 | 未写 |
| `deploy-guard.md` | 部署守卫 `scripts/pick-deploy-guard.py` 的用法 | TR-34 | 未写 |
| 其余主题 | 见计划 TR-29 的清单 | TR-29 及各任务 | 未写 |

## 运维命令

入口：`python -m ggwork_pick.observe.admin <命令> [参数…]`，`--help` 列出本镜像里已有的命令。在 Railway 容器里按 `cd /app/backend && python -m ggwork_pick.observe.admin …` 的写法执行。

- 每条命令是 `ggwork_pick/observe/admin/` 下的一个模块 `cmd_<名字>.py`，命令名把下划线换成连字符（`cmd_reset_disable.py` 即 `reset-disable`）。模块导出同名的 `NAME` 与 `main(argv)`，后者返回退出码。
- 目前已有的命令文件是 `cmd_gsc_export_urls.py`、`cmd_gsc_probe.py`、`cmd_regrant.py`、`cmd_reset_disable.py`。`cmd_import_legacy.py`（`import-legacy`）属于计划 TR-22，尚未实现；下表里其余还没有对应文件的命令同样是计划项，随表中的任务实现。
- 入口按文件名列出命令，只导入要执行的那一个；某条命令的依赖坏了，不影响其他命令。
- 退出码（`ggwork_pick/observe/errors.py`）：0 完成或无事可做；1 中途失败；2 用法错误、缺配置或自检不过，什么都没做；3 持久状态或运行时行读不到，当天不跑；130 被中断，重跑是安全的。命令自己返回的退出码只认 0–255 的整数，其余一律按 1（系统只保留低 8 位，256 会变成 0，被当成成功）。
- 出错时打印错误类名与 SQLSTATE，不打印数据库或 HTTP 的原文（可能带 DSN、cookie 或剧名）。本扩展自己抛的 `ObserveFailure` 及其子类（`Refused`、`StateUnavailable`）另外保留消息，这些消息按约定只写名字（变量名、表名、步骤），不写值。它们包着数据库错误抛出时（`raise StateUnavailable(...) from db_error`）照样带上底层的 SQLSTATE：退出码 3 带 `42501` 是缺授权，跑 `regrant`；不带 SQLSTATE 时看消息，是运行时行不存在，还是根本没连上库。
- 异常组（`asyncio.TaskGroup` 抛出的 `ExceptionGroup`）逐个成员打印，退出码取最需要人处理的那个成员：3 优先于 2，2 优先于 130，130 优先于 1。
- 各命令解析参数一律用 `ggwork_pick/observe/admin/args.py` 的 `command_parser(NAME, 说明)`，不直接用 `argparse.ArgumentParser`：argparse 原生的报错会原样带出非法的参数值，而参数可能是路径或 DSN；`command_parser` 遇到用法错误只打印 usage 和一行固定提示，以 2 退出。参数默认值会出现在 `--help` 里，所以默认值不取自环境变量或文件。

| 命令 | 模块 | 任务 |
|---|---|---|
| `regrant` | `cmd_regrant.py` | TR-12 |
| `reset-disable` | `cmd_reset_disable.py` | TR-13 |
| `identity-churn` | `cmd_identity_churn.py` | TR-18 |
| `import-legacy` | `cmd_import_legacy.py` | TR-22 |
| `import-editorial` | `cmd_import_editorial.py` | TR-23b |
| `shadow-sample`、`leadtime-report` | `cmd_shadow_sample.py`、`cmd_leadtime_report.py` | TR-31 |
| `shadow-e2e` | `cmd_shadow_e2e.py` | TR-36 |

另有独立 Trends 入口 `python -m ggwork_pick.observe.trends canary-report`，不属于上表的 admin 子命令。它是 TR-30 的中性事实切片，见 [使用说明](canary-report.md)，不判上线门槛。

## 采集合同版本

两条通道共用一个 `COLLECTOR_VERSION`（`ggwork_pick/observe/versions.py`，计划第 5 节与 D5），两个 cron 服务的 `PICK_OBS_EXPECTED_COLLECTOR` 都填它。任一通道改采集合同（Trends 换源或改请求形状，GSC 改请求形状），都要升这个常量。代价有两条：新镜像上线时两个服务的 `PICK_OBS_EXPECTED_COLLECTOR` 都要改成新值、两个 cron 一起重部署，哪个服务的变量没改，它的自检就以退出码 2 拒跑；Trends 升级前后的集合不互相确认（设计 4.11），哪怕这次改的是 GSC。是否拆成每个通道一个常量，待计划负责人拍板；要拆，得赶在 TR-11 的表结构和 TR-13 的自检绑定这个常量之前。
