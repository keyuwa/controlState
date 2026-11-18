# HoneySat 控制状态安全测试系统

基于 HoneySat 卫星仿真环境的自动化安全测试框架，支持测试序列生成、执行、判定和异常复现。

## 快速开始

### 1. 启动 HoneySat 环境

```bash
cd /path/to/HoneySat/evaluation/experiment-1
docker-compose -f docker-compose-ccsds.yaml up -d yamcs honeysat_api logs_mongodb sat_proxy
```

访问 Yamcs 控制台：http://localhost:8090（用户名：`administrator`，密码：`Admin123`）

### 2. 配置环境

```bash
# 激活 Python 环境
source /path/to/HoneySat/.venv/bin/activate

# 设置 Yamcs 凭据
export YAMCS_URL="http://localhost:8090"
export YAMCS_USERNAME="administrator"
export YAMCS_PASSWORD="Admin123"

# 进入实验目录
cd control_state
```

### 3. 验证环境

```bash
python reproduce.py --mode verify
```

## 完整复现流程

### 步骤1：生成测试序列

```bash
python reproduce.py --mode generate --count 20 --seed 42
```

输出：20 个测试场景 → `generated/gen_42_*.json`

### 步骤2：批量执行测试

```bash
python reproduce.py --mode batch --scenarios generated/
```

输出：
- 执行记录 → `runs/<run_id>/`
- 判定结果 → `runs/<run_id>/verdict.json`
- 汇总报告 → `batch_summary.json`

### 步骤3：复现发现的异常

```bash
python reproduce.py --mode replay \
  --finding findings/F1_revocation_queued_command/ \
  --replays 5
```

### 步骤4：缩减异常场景

```bash
python reproduce.py --mode minimize \
  --finding findings/F1_revocation_queued_command/
```

## 发现的漏洞

### 🔴 F1: 撤权不取消排队命令（已确认）

**漏洞描述**：操作员提交命令到队列后被撤销权限，队列放行时命令仍然执行，违反最小权限原则。

**复现步骤**：
1. 操作员以有效权限提交 `SetHeater` 命令
2. 队列管理员阻塞队列（`hold_queue`）
3. 系统管理员禁用该操作员账户
4. 队列管理员放行队列（`release_queue`）
5. **命令被执行**（预期应被拒绝）

**复现率**：5/5（100%）

**影响范围**：
- 违反 RBAC 最小权限原则
- 已撤权用户的命令仍能影响卫星状态
- 适用于所有排队的命令类型

**技术细节**：
```json
{
  "steps": [
    {"action": "issue_command", "command": "/SERVICE-EPS/SetHeater", "args": {"Mode": 1}},
    {"action": "hold_queue"},
    {"action": "change_authorization", "user": "operator", "active": false},
    {"action": "release_queue"},
    {"action": "query_eps"}
  ],
  "expected": "command should be rejected",
  "actual": "command executed, heater turned on"
}
```

**根本原因**：
- Yamcs 只在命令提交时检查权限（`AUTH-1`）
- 队列操作不重新验证命令权限或提交者（`AUTH-2`）
- 队列放行时直接执行，无二次鉴权

**证据位置**：`findings/F1_revocation_queued_command/`

**缩减结果**：原始 9 步 → 最小 4 步（缩减率 55.6%）

## 核心组件

### 执行引擎
- `runner.py` - 测试场景执行器，支持命令发送、队列控制、权限变更
- `yamcs_adapter.py` - Yamcs API 适配器，处理遥测订阅和命令历史

### 测试生成
- `generator.py` - 基于模板的测试序列生成器，支持参数变异和步骤重组
- `scenarios/` - 手工模板（正常执行、取消、权限变更等）

### 判定与复现
- `oracle.py` - 策略违反判定器，支持 pass/violation/insufficient_evidence
- `replay.py` - 异常复现验证器
- `minimize.py` - 测试用例缩减器

### 数据导入
- `import_logs.py` - 从 MongoDB 导入历史交互日志作为测试种子

### 统一接口
- `reproduce.py` - 统一执行脚本，支持 6 种模式：
  - `verify` - 环境验证
  - `generate` - 生成测试
  - `batch` - 批量执行
  - `replay` - 复现异常
  - `minimize` - 缩减场景
  - `import-logs` - 导入日志

## 数据产物

```
control_state/
├── target_manifest.json      # 目标接口清单（8条命令）
├── policies.json             # 安全策略定义（8项原生 + 2项实验）
├── scenarios/                # 测试模板
├── generated/                # 生成的测试序列
├── runs/                     # 执行记录
│   └── <run_id>/
│       ├── events.jsonl      # 事件时序（单调时钟 + UTC）
│       ├── manifest.json     # 执行元数据
│       └── verdict.json      # 判定结果
├── findings/                 # 发现的异常
│   └── F1_revocation_queued_command/
│       ├── report.json       # 发现报告
│       ├── scenario.json     # 原始场景
│       └── minimized.json    # 缩减场景
└── batch_summary.json        # 批量执行汇总
```

## 依赖要求

- Python 3.8+
- HoneySat Docker 环境（Yamcs 5.10.8-SNAPSHOT）
- Python 包：yamcs-client, requests

## 进阶功能

### Web 界面（模型辅助探索）

```bash
# 设置模型凭据（可选）
export MODEL_BASE_URL="https://your-endpoint/v1"
export MODEL_API_KEY="your-key"
export MODEL_NAME="your-model"

# 启动 Web UI
python web_ui.py
```

访问：http://localhost:5001

### 从历史日志生成测试

```bash
python reproduce.py --mode import-logs \
  --start-date 2026-11-15 \
  --end-date 2026-11-18 \
  --limit 200
```

## 参考资料

- 详细文档：[REPRODUCE.md](REPRODUCE.md)
- 执行计划：[执行计划.md](执行计划.md)
- HoneySat 项目：https://github.com/ssloxford/HoneySat

## 许可证

本项目用于安全研究和教育目的。

---

**最后更新**：2026-11-18  
**实验环境**：HoneySat CCSDS (Yamcs 5.10.8-SNAPSHOT)  
**发现状态**：1 个已确认漏洞（F1），100% 可复现

---

# 早期演示：合成策略验证

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
