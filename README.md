# 卫星控制状态安全：实验系统

## 当前状态（2026-11-18）

阶段 1–4 已闭环，阶段 6 日志导入已可用；阶段 5（模型接入）与阶段 7（基线评估）待配置模型凭据后验收。详细计划见 `执行计划.md`，接口与策略依据见 `target_manifest.json` / `policies.json`。

### 阶段 1：接口与策略核实（完成）

- 目标：HoneySat experiment-1 CCSDS 栈（Yamcs 5.10.8-SNAPSHOT + RACCOON sat_proxy + honeysat_api 仿真器 + MongoDB），镜像与基线已固定（`target_manifest.json`）。
- 原生链路只暴露 5 条查询命令；经用户批准扩展了 `SetHeater`（TC(132,3) → ZMQ heater，全链路真实状态变化，EPS 功耗/温度可读回）。补丁在 `patches/`，基线镜像 `*:baseline-20261117`，MDB 经 `yamcs-mdb-override.yaml` 挂载，未改目标 compose。
- 关键原生语义（源码级依据见 `policies.json`）：授权仅在提交时检查（AUTH-1）；队列控制不重查命令权限/提交者（AUTH-2）；队列内 reject 是放行前唯一取消（QUEUE-2）；DB 新建用户无法认证，账号集静态（AUTH-4，实测）。

### 阶段 2–4：执行、生成、判定、复现（完成）

运行环境变量：`YAMCS_PASSWORD`（operator，默认 administrator）、`YAMCS_CONTROL_PASSWORD`（root，队列/IAM 控制面）。

```sh
PY=/Users/keyu/code/honeySat/HoneySat/.venv/bin/python
$PY runner.py scenarios/normal_execution.json     # 单场景执行 -> runs/<run_id>/
$PY oracle.py runs/<run_id>                       # 独立判定 -> verdict.json
$PY replay.py scenarios/auth_change_revoke.json --times 5   # 复现统计
$PY minimize.py scenarios/auth_change_revoke.json --finding-id F1  # 缩减
$PY generator.py --count 20 --seed 42             # 模板+变异 -> generated/
$PY batch.py generated                            # 批量执行 -> batch_summary.json
```

事件证据：每个 run 有 `events.jsonl`（单调时钟+UTC+组件标识）、`manifest.json`。遥测订阅先于发令建立（无缓存），命令-遥测因果靠 command_id + 订阅顺序 + 时间窗。恢复在 finally 中执行（队列放行、heater 关闭、账号恢复），错误记入 `restore_errors`。

### 已确认发现

**F1：撤权不取消排队命令（策略违反 EXP-1）**：操作员提交 SetHeater→队列阻塞→禁用该账号→放行队列→命令仍执行。5/5 复现。依据 AUTH-1/2（授权仅在提交时检查一次，放行不重查）。证据在 `findings/F1_revocation_queued_command/`。这是 Yamcs 原生语义与 EXP-1 实验策略的冲突，不是 HoneySat 实现缺陷；评估原生行为时用基线镜像对照。

### 阶段 5：模型接入（待验收）

`model_client.py`（OpenAI 兼容端点，凭据走环境变量 `MODEL_BASE_URL`/`MODEL_API_KEY`/`MODEL_NAME`，不落盘）+ `explore.py`（反馈闭环 + 有限特征新颖性）。未配置凭据时保持待验收状态。

### 阶段 6：日志种子（可用）

`import_logs.py` 从本机 MongoDB（`honeysat_log.ABCMeta`）按时间段导出交互、解析为种子（`seeds/`），带 `source_doc_id` 可追溯；无法识别的请求保留原始记录并计入 limitations。

### 与早期演示的关系

下文为最初的合成策略演示与 HoneySat 只读接入版，保留作对照。

---

# 早期：卫星控制状态安全：最小可执行演示

## 演示问题

地面操作者获准提交加热器开启指令，指令等待链路恢复。在等待期间权限撤销。实验策略明确规定：撤销必须取消该操作者尚未执行的指令。

三个场景使用相同的指令处理模型：

| 场景 | 执行前检查 | 最终加热器状态 | 是否违反策略 |
|---|---|---|---|
| 正常授权 | 检查 | 开启 | 否 |
| 排队后撤权，缺陷版本 | 仅依赖提交时授权 | 开启 | 是 |
| 排队后撤权，保护版本 | 检查当前权限与授权版本 | 关闭 | 否 |

## 运行

在此目录运行 `python3 demo.py`，输出完整步骤并保存 `results.json`。

或运行 `sh run-docker.sh`，复用本机已有的 `python:3.12` 镜像，无网络、无额外依赖。Docker 方式的结果文件位于临时容器内，容器退出即删除；终端保留完整步骤，需保存 JSON 时使用本地方式。

## 模型与真实系统的边界

这是自行构造的安全机制实验，不是 HoneySat、Yamcs 或真实卫星漏洞复现。加热器是布尔状态，不包含物理仿真。队列、操作者和执行器均在单进程中模拟，没有真实 CSP/CCSDS 传输。模型明确假设执行器已经同步撤销信息；它不能解决卫星断连期间不知道地面撤权的问题。检查与执行采用单线程一步完成，未验证并发、崩溃和持久化。

## 接入已有 HoneySat 的下一步

已发现本机 `/Users/keyu/code/honeySat/HoneySat/evaluation/experiment-1` 的 CCSDS 实验，包含 Yamcs、sat_proxy、honeysat_api 和 MongoDB。现有调用脚本支持 EPS、ADCS 和 OBC 查询。接入时先用 `/SERVICE-EPS/FetchEPSData` 核实 TC/TM 往返，再核实实际配置指令及其执行效果；不能将查询成功当成加热器控制已实现。

这个 demo 的下一阶段应把执行器替换为已验证的真实接口，将授权检查设在能够阻止最终操作的位置，并记录地面接受、链路转发、星载执行和遥测变化。需要先确定目标系统自己的撤销策略，再判断是否存在真实漏洞。

## 运行 HoneySat 接入版

使用已有的 experiment-1 CCSDS 容器。确保 MongoDB、HoneySat API、RACCOON 代理和 Yamcs 均已启动，Yamcs 本地端口为 8090。将实验凭据放入 `YAMCS_PASSWORD` 环境变量，用户名默认 `administrator`，可通过 `YAMCS_USERNAME` 覆盖。在本目录执行：

```sh
/Users/keyu/code/honeySat/HoneySat/.venv/bin/python honeysat_demo.py
```

正常组经 Yamcs 发出 `FetchEPSData`，等待命令完成和实时 EPS 电压遥测。撤权组在实验脚本中将授权版本从 1 改为 2，在调用 Yamcs 前拒绝旧指令。结果写入 `honeysat-results.json`。

接入版使用 HoneySat 的只读指令验证链路。排队、授权版本和撤销事件由实验脚本管理，检查位于地面提交入口。实验范围为地面侧放行策略，不覆盖 Yamcs 原生权限撤销、星载授权或真实断连。

## 使用外部模型安排授权回归测试

可将下列提示交给模型，并将它输出的 JSON 保存到 `plan.json`。

> 将卫星控制授权回归测试中优先验证的场景排在前面。authorized 表示授权仍有效，revoked 表示排队后撤权。只返回含这两个 ID 的 JSON 数组，各出现一次。

使用前面相同的 Yamcs 环境和凭据运行：

```sh
/Users/keyu/code/honeySat/HoneySat/.venv/bin/python honeysat_demo.py --model-plan plan.json
```

脚本先验证计划，再按顺序执行两个固定场景。模型输入只能改变顺序，预期结果由程序定义。报告 `honeysat-model-results.json` 包含每组预期、实际结果、是否通过，以及允许组的指令 ID 和遥测。此接口接受外部模型输出文件，应用内未配置模型服务。
