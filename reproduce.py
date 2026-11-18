#!/usr/bin/env python3
"""可复现的卫星控制状态测试系统 - 核心功能集成脚本

本脚本整合了阶段1-4、6的所有核心功能，提供完整的可复现测试流程：
1. 生成测试序列（基于模板和种子）
2. 执行测试并记录完整事件
3. 判定策略违反
4. 复现和缩减异常场景
5. 导入历史日志作为测试种子

使用方式：
    python reproduce.py --mode generate --count 20 --seed 42
    python reproduce.py --mode batch --scenarios generated/
    python reproduce.py --mode replay --finding findings/F1_revocation_queued_command/
    python reproduce.py --mode import-logs --start-date 2026-11-15 --end-date 2026-11-18

环境要求：
    - HoneySat Docker 容器运行中（Yamcs, HoneySat API, MongoDB）
    - 环境变量：YAMCS_URL, YAMCS_USERNAME, YAMCS_PASSWORD
"""
import argparse
import json
import sys
from pathlib import Path

# 导入核心模块
from generator import generate, validate, TEMPLATES, SCEN_DIR
from runner import Runner
from oracle import judge
from replay import replay
from minimize import minimize
from import_logs import import_logs_from_mongo

ROOT = Path(__file__).resolve().parent


def mode_generate(args):
    """生成测试序列"""
    print(f"🔧 生成 {args.count} 个测试序列（种子：{args.seed}）...")

    templates = list(TEMPLATES.glob("*.json"))
    if not templates:
        print("❌ 错误：scenarios/ 目录下没有找到模板文件")
        return 1

    generated = generate(templates, args.count, args.seed)
    print(f"✅ 成功生成 {len(generated)} 个有效序列")
    print(f"📁 输出目录：{SCEN_DIR}")

    # 显示生成的场景列表
    for sc in generated[:5]:
        print(f"  - {sc['scenario_id']}: {sc.get('description', 'No description')}")
    if len(generated) > 5:
        print(f"  ... 还有 {len(generated) - 5} 个场景")

    return 0


def mode_batch(args):
    """批量执行测试序列"""
    scenario_dir = Path(args.scenarios)
    if not scenario_dir.exists():
        print(f"❌ 错误：目录不存在 {scenario_dir}")
        return 1

    files = sorted(scenario_dir.glob("*.json"))
    if args.limit:
        files = files[:args.limit]

    print(f"🚀 批量执行 {len(files)} 个测试场景...")

    summary = []
    for i, f in enumerate(files, 1):
        if f.name.startswith(("report", "replay", "batch")):
            continue

        scenario = json.loads(f.read_text())
        scenario_id = scenario["scenario_id"]

        print(f"\n[{i}/{len(files)}] 执行 {scenario_id}...")

        try:
            # 执行场景
            manifest = Runner(scenario).run()
            run_id = manifest["run_id"]

            # 判定结果
            verdict = judge(ROOT / "runs" / run_id)

            # 保存判定结果
            (ROOT / "runs" / run_id / "verdict.json").write_text(
                json.dumps(verdict, indent=2, ensure_ascii=False))

            summary.append({
                "scenario": scenario_id,
                "run_id": run_id,
                "verdict": verdict["verdict"],
                "policy_id": verdict.get("policy_id"),
                "rationale": verdict["rationale"][:120]
            })

            print(f"  结果: {verdict['verdict']}")
            if verdict["verdict"] == "policy_violation":
                print(f"  ⚠️  策略违反: {verdict.get('policy_id', 'Unknown')}")
                print(f"  理由: {verdict['rationale']}")

        except Exception as exc:
            print(f"  ❌ 执行失败: {exc}")
            summary.append({
                "scenario": scenario_id,
                "verdict": "environment_error",
                "rationale": repr(exc)
            })

    # 保存汇总
    from collections import Counter
    tally = Counter(s["verdict"] for s in summary)
    result = {"total": len(summary), "tally": dict(tally), "runs": summary}

    outpath = ROOT / "batch_summary.json"
    outpath.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    print(f"\n{'='*60}")
    print(f"✅ 批量执行完成")
    print(f"📊 统计: {json.dumps(tally, ensure_ascii=False)}")
    print(f"📁 详细报告: {outpath}")

    return 0


def mode_replay(args):
    """复现发现的异常"""
    finding_dir = Path(args.finding)
    if not finding_dir.exists():
        print(f"❌ 错误：发现目录不存在 {finding_dir}")
        return 1

    report_file = finding_dir / "report.json"
    if not report_file.exists():
        print(f"❌ 错误：找不到 report.json")
        return 1

    report = json.loads(report_file.read_text())
    original_scenario = report["original_scenario"]

    print(f"🔁 复现发现: {report['finding_id']}")
    print(f"描述: {report['description']}")
    print(f"复现次数: {args.replays}")

    results = replay(original_scenario, args.replays)

    success_count = sum(1 for r in results if r["reproduced"])
    print(f"\n{'='*60}")
    print(f"✅ 复现结果: {success_count}/{args.replays}")

    if success_count == args.replays:
        print("🎯 100% 复现成功")
    elif success_count > 0:
        print(f"⚠️  部分复现 ({success_count}/{args.replays})")
    else:
        print("❌ 无法复现")

    return 0


def mode_minimize(args):
    """缩减异常场景"""
    finding_dir = Path(args.finding)
    if not finding_dir.exists():
        print(f"❌ 错误：发现目录不存在 {finding_dir}")
        return 1

    report_file = finding_dir / "report.json"
    if not report_file.exists():
        print(f"❌ 错误：找不到 report.json")
        return 1

    report = json.loads(report_file.read_text())
    original_scenario = report["original_scenario"]

    print(f"✂️  缩减场景: {report['finding_id']}")
    print(f"原始步骤数: {len(original_scenario['steps'])}")

    minimized = minimize(original_scenario)

    if minimized:
        print(f"✅ 缩减成功")
        print(f"最小步骤数: {len(minimized['steps'])}")
        print(f"缩减率: {(1 - len(minimized['steps'])/len(original_scenario['steps']))*100:.1f}%")

        # 保存缩减结果
        outfile = finding_dir / "minimized.json"
        outfile.write_text(json.dumps(minimized, indent=2, ensure_ascii=False))
        print(f"📁 保存至: {outfile}")
    else:
        print("⚠️  无法进一步缩减")

    return 0


def mode_import_logs(args):
    """从 MongoDB 导入历史日志作为测试种子"""
    print(f"📥 导入日志: {args.start_date} 至 {args.end_date}")

    try:
        stats = import_logs_from_mongo(
            start_date=args.start_date,
            end_date=args.end_date,
            limit=args.limit
        )

        print(f"\n{'='*60}")
        print(f"✅ 导入完成")
        print(f"总记录数: {stats['total_records']}")
        print(f"有效种子: {stats['valid_seeds']}")
        print(f"SetHeater 命令: {stats.get('setheater_count', 0)}")
        print(f"📁 种子目录: {ROOT / 'seeds'}")

        return 0

    except Exception as e:
        print(f"❌ 导入失败: {e}")
        return 1


def mode_verify(args):
    """验证环境配置"""
    print("🔍 验证环境配置...\n")

    # 1. 检查必要文件
    required_files = [
        "target_manifest.json",
        "policies.json",
        "runner.py",
        "yamcs_adapter.py",
        "generator.py",
        "oracle.py",
    ]

    print("📄 检查核心文件:")
    all_files_exist = True
    for f in required_files:
        path = ROOT / f
        if path.exists():
            print(f"  ✅ {f}")
        else:
            print(f"  ❌ {f} (缺失)")
            all_files_exist = False

    # 2. 检查环境变量
    import os
    print("\n🔧 检查环境变量:")
    env_vars = ["YAMCS_URL", "YAMCS_USERNAME", "YAMCS_PASSWORD"]
    all_env_set = True
    for var in env_vars:
        if os.environ.get(var):
            print(f"  ✅ {var}")
        else:
            print(f"  ❌ {var} (未设置)")
            all_env_set = False

    # 3. 检查 Yamcs 连接
    print("\n🌐 检查 Yamcs 连接:")
    try:
        from yamcs.client import Credentials, YamcsClient
        yamcs_url = os.environ.get("YAMCS_URL", "http://localhost:8090")
        username = os.environ.get("YAMCS_USERNAME", "administrator")
        password = os.environ.get("YAMCS_PASSWORD", "")

        creds = Credentials(username=username, password=password)
        client = YamcsClient(yamcs_url.replace("http://", ""), credentials=creds)
        instances = client.list_instances()
        print(f"  ✅ 连接成功，发现 {len(instances)} 个实例")
        for inst in instances:
            print(f"     - {inst.name}")
    except Exception as e:
        print(f"  ❌ 连接失败: {e}")
        all_env_set = False

    # 4. 检查已有数据
    print("\n📊 检查已有数据:")
    runs_dir = ROOT / "runs"
    if runs_dir.exists():
        run_count = len([d for d in runs_dir.iterdir() if d.is_dir()])
        print(f"  ✅ 执行记录: {run_count} 个")
    else:
        print(f"  ⚠️  无执行记录")

    findings_dir = ROOT / "findings"
    if findings_dir.exists():
        finding_count = len([d for d in findings_dir.iterdir() if d.is_dir()])
        print(f"  ✅ 发现记录: {finding_count} 个")
    else:
        print(f"  ⚠️  无发现记录")

    print(f"\n{'='*60}")
    if all_files_exist and all_env_set:
        print("✅ 环境验证通过，可以开始实验")
        return 0
    else:
        print("❌ 环境验证失败，请检查上述问题")
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="HoneySat 控制状态测试系统 - 可复现核心功能",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  # 验证环境
  python reproduce.py --mode verify

  # 生成 20 个测试序列
  python reproduce.py --mode generate --count 20 --seed 42

  # 批量执行所有生成的序列
  python reproduce.py --mode batch --scenarios generated/

  # 复现发现 F1
  python reproduce.py --mode replay --finding findings/F1_revocation_queued_command/ --replays 5

  # 缩减异常场景
  python reproduce.py --mode minimize --finding findings/F1_revocation_queued_command/

  # 导入历史日志
  python reproduce.py --mode import-logs --start-date 2026-11-15 --end-date 2026-11-18
        """
    )

    parser.add_argument("--mode", required=True,
                        choices=["verify", "generate", "batch", "replay", "minimize", "import-logs"],
                        help="执行模式")

    # generate 参数
    parser.add_argument("--count", type=int, default=20,
                        help="生成序列数量（默认：20）")
    parser.add_argument("--seed", type=int, default=42,
                        help="随机种子（默认：42）")

    # batch 参数
    parser.add_argument("--scenarios", type=str,
                        help="测试场景目录")
    parser.add_argument("--limit", type=int,
                        help="限制执行数量")

    # replay 参数
    parser.add_argument("--finding", type=str,
                        help="发现目录路径")
    parser.add_argument("--replays", type=int, default=5,
                        help="复现次数（默认：5）")

    # import-logs 参数
    parser.add_argument("--start-date", type=str,
                        help="起始日期（YYYY-MM-DD）")
    parser.add_argument("--end-date", type=str,
                        help="结束日期（YYYY-MM-DD）")

    args = parser.parse_args()

    # 路由到对应的处理函数
    mode_handlers = {
        "verify": mode_verify,
        "generate": mode_generate,
        "batch": mode_batch,
        "replay": mode_replay,
        "minimize": mode_minimize,
        "import-logs": mode_import_logs,
    }

    handler = mode_handlers[args.mode]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
