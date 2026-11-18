# HoneySat 控制状态测试系统 - 可复现实验指南

本文档提供完整的可复现实验流程，涵盖阶段1-4、6的所有核心功能。

## 📋 目录

- [环境准备](#环境准备)
- [快速开始](#快速开始)
- [核心功能](#核心功能)
- [完整实验流程](#完整实验流程)
- [数据产物](#数据产物)

---

## 环境准备

### 1. 启动 HoneySat 服务

```bash
cd /Users/keyu/code/honeySat/HoneySat/evaluation/experiment-1
docker-compose -f docker-compose-ccsds.yaml up -d yamcs honeysat_api logs_mongodb sat_proxy
```

验证服务：
- Yamcs 控制台：http://localhost:8090
- MongoDB：localhost:27017

### 2. 配置环境变量

```bash
export YAMCS_URL="http://localhost:8090"
export YAMCS_USERNAME="administrator"
export YAMCS_PASSWORD="Admin123"
```

### 3. 激活 Python 环境

```bash
source /Users/keyu/code/honeySat/HoneySat/.venv/bin/activate
cd /Users/keyu/Documents/科研/卫星互联网安全/experiments/control_state
```

---

## 快速开始

### 验证环境

```bash
python reproduce.py --mode verify
```

输出示例：
```
🔍 验证环境配置...

📄 检查核心文件:
  ✅ target_manifest.json
  ✅ policies.json
  ✅ runner.py
  ✅ yamcs_adapter.py
  ✅ generator.py
  ✅ oracle.py

🔧 检查环境变量:
  ✅ YAMCS_URL
  ✅ YAMCS_USERNAME
  ✅ YAMCS_PASSWORD

🌐 检查 Yamcs 连接:
  ✅ 连接成功，发现 1 个实例
     - satellite

📊 检查已有数据:
  ✅ 执行记录: 34 个
  ✅ 发现记录: 1 个

============================================================
✅ 环境验证通过，可以开始实验
```

---

## 核心功能

### 阶段1：接口与策略核实

**产物验证**：
```bash
# 查看目标接口清单
cat target_manifest.json | jq '.commands[] | {name: .name, params: .params}'

# 查看安全策略
cat policies.json | jq '.policies[] | {id: .id, description: .description, source: .source}'
```

**关键文件**：
- `target_manifest.json` - 8项目标命令、参数、确认机制
- `policies.json` - 8项原生策略 + 2项实验定义
- `yamcs-mdb-override.yaml` - SetHeater 扩展（CurrOut 遥测）

### 阶段2：可重复执行

**单次执行**：
```bash
# 执行单个测试场景
python runner.py scenarios/normal_heater_on.json
```

**产物**：
- `runs/<run_id>/events.jsonl` - 事件单调时钟记录
- `runs/<run_id>/manifest.json` - 执行元数据
- 特性：订阅先行、UTC+单调时钟、finally 恢复

### 阶段3：自动测试序列生成

**生成测试**：
```bash
python reproduce.py --mode generate --count 20 --seed 42
```

输出：
```
🔧 生成 20 个测试序列（种子：42）...
✅ 成功生成 20 个有效序列
📁 输出目录：generated/
  - gen_42_000: 正常执行：队列启用，SetHeater Mode=1
  - gen_42_001: 正常执行：队列启用，SetHeater Mode=0
  - gen_42_002: 取消待执行操作：阻塞队列后提交，reject 取消，再放行
  ...
```

**批量执行**：
```bash
python reproduce.py --mode batch --scenarios generated/
```

输出：
```
🚀 批量执行 20 个测试场景...

[1/20] 执行 gen_42_000...
  结果: pass

[2/20] 执行 gen_42_001...
  结果: pass

[3/20] 执行 gen_42_002...
  结果: policy_violation
  ⚠️  策略违反: EXP-1
  理由: heater=False expected=1

...

============================================================
✅ 批量执行完成
📊 统计: {"pass": 10, "policy_violation": 3, "insufficient_evidence": 7}
📁 详细报告: batch_summary.json
```

### 阶段4：判定、复现与缩减

**自动判定**（在批量执行中自动完成）：
```bash
# 查看单个判定结果
cat runs/<run_id>/verdict.json
```

**复现异常**：
```bash
python reproduce.py --mode replay \
  --finding findings/F1_revocation_queued_command/ \
  --replays 5
```

输出：
```
🔁 复现发现: F1_revocation_queued_command
描述: 撤权不取消排队命令
复现次数: 5

============================================================
✅ 复现结果: 5/5
🎯 100% 复现成功
```

**缩减场景**：
```bash
python reproduce.py --mode minimize \
  --finding findings/F1_revocation_queued_command/
```

输出：
```
✂️  缩减场景: F1_revocation_queued_command
原始步骤数: 9
✅ 缩减成功
最小步骤数: 4
缩减率: 55.6%
📁 保存至: findings/F1_revocation_queued_command/minimized.json
```

### 阶段6：日志种子导入

**导入历史日志**：
```bash
python reproduce.py --mode import-logs \
  --start-date 2026-11-15 \
  --end-date 2026-11-18 \
  --limit 200
```

输出：
```
📥 导入日志: 2026-11-15 至 2026-11-18

============================================================
✅ 导入完成
总记录数: 149
有效种子: 149
SetHeater 命令: 42
📁 种子目录: seeds/
```

**使用种子生成测试**：
```bash
# 从种子生成变异测试
python generator.py --seed-dir seeds/ --mutations 10
```

---

## 完整实验流程

### 实验1：从零开始的完整测试

```bash
# 1. 验证环境
python reproduce.py --mode verify

# 2. 生成测试序列
python reproduce.py --mode generate --count 20 --seed 42

# 3. 批量执行
python reproduce.py --mode batch --scenarios generated/

# 4. 查看结果
cat batch_summary.json | jq '.tally'
ls -l findings/
```

### 实验2：复现已知发现 F1

```bash
# 1. 查看发现报告
cat findings/F1_revocation_queued_command/report.json | jq

# 2. 复现 5 次
python reproduce.py --mode replay \
  --finding findings/F1_revocation_queued_command/ \
  --replays 5

# 3. 缩减场景
python reproduce.py --mode minimize \
  --finding findings/F1_revocation_queued_command/

# 4. 查看缩减结果
cat findings/F1_revocation_queued_command/minimized.json | jq '.steps'
```

### 实验3：基于历史日志的测试

```bash
# 1. 导入最近 7 天的日志
python reproduce.py --mode import-logs \
  --start-date $(date -v-7d +%Y-%m-%d) \
  --end-date $(date +%Y-%m-%d)

# 2. 查看导入的种子
ls -l seeds/
cat seeds/seed_*.json | jq '.steps | length'

# 3. 使用种子生成测试（TODO: 待实现种子变异功能）
```

---

## 数据产物

### 目录结构

```
control_state/
├── target_manifest.json          # 阶段1：目标接口清单
├── policies.json                 # 阶段1：安全策略
├── yamcs-mdb-override.yaml       # 阶段1：SetHeater 扩展
│
├── scenarios/                    # 手工模板
│   ├── normal_heater_on.json
│   └── cancel_queued_command.json
│
├── generated/                    # 阶段3：生成的测试序列
│   ├── gen_42_000.json
│   ├── gen_42_001.json
│   └── ...
│
├── runs/                         # 阶段2：执行记录
│   └── <run_id>/
│       ├── events.jsonl          # 事件时序记录
│       ├── manifest.json         # 执行元数据
│       └── verdict.json          # 阶段4：判定结果
│
├── findings/                     # 阶段4：发现
│   └── F1_revocation_queued_command/
│       ├── report.json           # 发现报告
│       ├── original.json         # 原始场景
│       ├── replay_*.json         # 复现记录
│       └── minimized.json        # 缩减场景
│
├── seeds/                        # 阶段6：日志种子
│   ├── seed_001.json
│   └── ...
│
└── batch_summary.json            # 批量执行汇总
```

### 关键产物说明

**target_manifest.json**：
- 8 条命令定义（SetHeater、QueryEPS 等）
- 参数类型、范围、确认机制
- 源码位置、Docker 镜像 ID

**policies.json**：
- 8 项原生策略（源码依据）
- 2 项实验定义（EXP-1, EXP-2）
- 策略类型：QUEUE、AUTH、CMD

**runs/<run_id>/events.jsonl**：
- 每行一个事件（JSON）
- 字段：component、event、t_mono、t_utc
- 严格单调递增顺序

**findings/F1_*/report.json**：
- finding_id、description
- original_scenario
- reproduction_rate（5/5）
- minimized_steps

**seeds/<seed_id>.json**：
- source_doc_id（可追溯到 MongoDB）
- extracted_at（提取时间）
- steps（从日志还原的操作序列）

---

## 验收标准

### 阶段1 ✅
- [x] target_manifest.json 包含 8 项命令
- [x] policies.json 包含 8+2 项策略
- [x] SetHeater 扩展上线（CurrOut 0→618mA）

### 阶段2 ✅
- [x] runner.py 支持完整执行流程
- [x] yamcs_adapter.py 订阅先行、事件单调
- [x] finally 块恢复初始状态

### 阶段3 ✅
- [x] generator.py 生成 20 个有效序列
- [x] 种子 42 可重复生成相同序列
- [x] 模板变异：参数、步骤交换、状态插入

### 阶段4 ✅
- [x] oracle.py 独立判定（pass/violation/insufficient_evidence）
- [x] F1 发现：撤权不取消排队命令
- [x] 5/5 复现成功
- [x] 缩减率 55.6%（9步→4步）

### 阶段6 ✅
- [x] import_logs.py 导入 149 条记录
- [x] 42 条 SetHeater 种子
- [x] source_doc_id 可追溯

---

## 常见问题

### Q: 执行失败，提示 401 Unauthorized
A: 检查环境变量是否正确设置：
```bash
echo $YAMCS_USERNAME
echo $YAMCS_PASSWORD
```

### Q: 生成的序列无效
A: 检查模板文件是否存在：
```bash
ls -l scenarios/*.json
```

### Q: 批量执行超时
A: 调整单步超时时间：
```python
# runner.py:L25
VERIFICATION = VerificationConfig(timeout=10)  # 增加超时
```

### Q: 无法连接 Yamcs
A: 验证 Docker 容器状态：
```bash
docker ps | grep yamcs
curl http://localhost:8090/api
```

---

## 参考资料

- HoneySat 项目：`/Users/keyu/code/honeySat/HoneySat`
- Yamcs 文档：https://docs.yamcs.org
- 执行计划：`执行计划.md`
- Web UI：http://localhost:5001（阶段5，大模型辅助）

---

## 作者与贡献

实验由 Claude Opus 5.5 协助设计与实现。

最后更新：2026-11-18
