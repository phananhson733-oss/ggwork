# 只读金丝雀事实报告

命令：

```sh
python -m ggwork_pick.observe.trends canary-report
```

使用既有状态查询的数据库配置和只读身份；在容器中先进入 `/app/backend`。它只执行一个 `status_reader` 只读事务，不启动 collector、warmup、自检、Google 请求或 task source，不获取采集租约，不改变模式或停止状态。

报告以捕获的当前 UTC 日期为七个日历日窗口末日。`generated_at` 是生成时间；`latest_recorded_date` 与年龄单独报告。没有记录的日期保持 unknown，当天没有记录为 pending，不拿很久以前的七个有记录日冒充最近七日。

报告分别列预算中的预留/计数、实际请求、HTTP 429、熔断、批次和 runtime 停止事实。同一天多个批次保持独立；只有保存的 admission 接受且同一个批次有实际请求，`load_and_request_valid` 才为 true。拒绝 admission 为 false，缺 admission 为 unknown，晚准入另列。

**`load_and_request_valid` 不是上线门槛通过。** 两个已经因限流停止的夜晚也可能有该事实。`qualification` 固定为 `not_evaluated`；简化/完整路线的阶段门槛另行核对，不能把此报告的退出码 0 或某个布尔值作为 canary PASS。

输出只含日期、状态和经过类型检查的数字/布尔聚合，不包含原始请求身份、cookie、出口、错误正文或凭据。缺配置/读权限错误沿既有安全错误输出；不改变数据库。

本命令是 TR-30 两条路线共用的中性事实切片，不代表原完整影子抽检或雷达产品已经完成。测试见 `customizations/pick-workbench/tests/observe/test_canary_report.py`；使用合成临时库验证双库、时区、日期缺口、批次边界和 SELECT-only。
