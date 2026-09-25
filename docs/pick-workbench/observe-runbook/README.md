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

- 每条命令是 `ggwork_pick/observe/admin/` 下的一个模块 `cmd_<名字>.py`，命令名把下划线换成连字符（`cmd_import_legacy.py` 即 `import-legacy`）。模块导出同名的 `NAME` 与 `main(argv)`，后者返回退出码。
- 入口按文件名列出命令，只导入要执行的那一个；某条命令的依赖坏了，不影响其他命令。
- 退出码（`ggwork_pick/observe/errors.py`）：0 完成或无事可做；1 中途失败；2 用法错误、缺配置或自检不过，什么都没做；3 持久状态或运行时行读不到，当天不跑；130 被中断，重跑是安全的。
- 出错时只打印错误类名与 SQLSTATE，不回显输入的参数，也不打印数据库原文（可能带 DSN、cookie 或剧名）。

| 命令 | 模块 | 任务 |
|---|---|---|
| `regrant` | `cmd_regrant.py` | TR-12 |
| `reset-disable` | `cmd_reset_disable.py` | TR-13 |
| `identity-churn` | `cmd_identity_churn.py` | TR-18 |
| `import-legacy` | `cmd_import_legacy.py` | TR-22 |
| `import-editorial` | `cmd_import_editorial.py` | TR-23b |
| `canary-report` | `cmd_canary_report.py` | TR-30 |
| `shadow-sample`、`leadtime-report` | `cmd_shadow_sample.py`、`cmd_leadtime_report.py` | TR-31 |
| `shadow-e2e` | `cmd_shadow_e2e.py` | TR-36 |
