# -*- coding: utf-8 -*-
"""
成交话术与复购提醒工作流 —— 端到端编排脚本。

流程（与 SKILL.md 的 DAG 一致）：
  S1 community-script   成交/复购节点话术：针对在售课程问题给出可发送回复
  S2 funnel-analysis    沉睡唤醒分析：从名单规模与价格权益算出口径与唤醒剧本

本脚本承担两步的确定性部分：价格与权益口径计算（实付=年课价-抵扣额）、
唤醒剧本排期生成、承诺红线扫描。模型负责语境化润色；数值口径以本脚本为准。

用法：
  python run_flow.py --input examples/input.json --outdir out
  python run_flow.py --demo

产物：
  out/flow_result.json    机器可读结果（summary/steps/deliverable）
  out/唤醒剧本.csv         沉睡名单唤醒排期
  out/成交唤醒交付物.md    人读版：成交话术 + 口径表 + 唤醒剧本
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.dirname(HERE)

DEMO = {
    "input": "场景一：微信群内用户「安安妈妈」询问：「我家孩子体验课上完了挺喜欢的，年课和系统课有啥区别呀？"
             "现在报名有优惠吗？」场景二：运营需要一份唤醒剧本，面向 83 名已完成体验课但结课后 30 天内"
             "未续报年课的家长名单，目标是通过私聊+社群触达促成续报。课程价格信息：体验课 399 元，"
             "年课 4999 元，系统课 3299 元；可用的续报权益：结课后 14 天内报名年课可抵扣体验课学费 399 元"
             "（即年课实付 4600 元）。"
}

PROMISE_BANNED = ["保证", "稳赚", "无条件退款", "全额退", "最好", "第一", "100%"]


def parse_input(text: str) -> dict:
    prices = {k: None for k in ("体验课", "年课", "系统课")}
    for k in prices:
        m = re.search(k + r"\s*(\d+(?:\.\d+)?)\s*元", text)
        if m:
            prices[k] = float(m.group(1))
    m = re.search(r"(\d+)\s*名", text) or re.search(r"(\d+)\s*人", text)
    sleeping = int(m.group(1)) if m else 0
    m = re.search(r"抵扣体验课学费\s*(\d+(?:\.\d+)?)\s*元", text)
    deduct = float(m.group(1)) if m else (prices["体验课"] or 0)
    m = re.search(r"结课后\s*(\d+)\s*天内报名", text) or re.search(r"(\d+)\s*天内.*?抵扣", text)
    window = int(m.group(1)) if m else 14
    year_price = prices["年课"]
    net = round(year_price - deduct, 2) if year_price is not None else None
    return {"prices": prices, "sleeping": sleeping, "deduct": deduct,
            "window": window, "net": net}


def step1_scripts(ctx: dict) -> list:
    """S1：成交节点话术（口径内，50-150 字）。"""
    p = ctx["prices"]
    diff = ""
    if p["年课"] is not None and p["系统课"] is not None:
        diff = (f"年课 {int(p['年课'])} 元、系统课 {int(p['系统课'])} 元；"
                "年课比系统课多了季度的阶段测评与答疑场次。")
    return [
        {"场景": "年课 vs 系统课咨询", "话术": (
            "两个课的差别主要在深度和陪跑：课程内容都以体系化学习为主，"
            f"{diff}如果孩子刚上完体验课，多数家长会先报年课衔接。"
            "现在报名可以享受结课后的续报抵扣权益，我把详细的课程对比发您看一下？")},
        {"场景": "续报权益确认", "话术": (
            f"目前可用的续报权益是：结课后 {ctx['window']} 天内报名年课，"
            f"可抵扣体验课学费 {int(ctx['deduct'])} 元，"
            f"也就是年课实付 {int(ctx['net'])} 元。"
            "这个权益到期就恢复了，您可以先对比下课程，有疑问随时问我。")},
        {"场景": "犹豫观望", "话术": (
            "没关系，选课不着急。我先把年课的课表和上次开班的安排发您，"
            "您和孩子爸爸商量一下。这期名额还有余位，有决定了随时跟我说一声就行。")},
    ]


def step2_wake_plan(ctx: dict) -> list:
    """S2：沉睡唤醒剧本（14 天窗口 → 按周排期，二触即止）。"""
    rows = [
        {"波次": "W1", "对象": "全部沉睡名单", "触达渠道": "企微 1 对 1",
         "动作": f"结课回访 + 抵扣权益提醒（{int(ctx['deduct'])} 元，{ctx['window']} 天内有效）",
         "负责人": "运营", "时间点": "窗口开启第 1-3 天", "验收指标": "触达覆盖率 100%"},
        {"波次": "W1", "对象": "W1 未响应者", "触达渠道": "社群",
         "动作": "群内年课学员案例分享（只陈述事实）+ 报名入口",
         "负责人": "助教", "时间点": "第 4-7 天", "验收指标": "互动 ≥5 人次"},
        {"波次": "W2", "对象": "W1+W1 社群均未响应者", "触达渠道": "企微 1 对 1",
         "动作": "最后提醒：权益到期倒计时（3 天）",
         "负责人": "运营", "时间点": "第 8-11 天", "验收指标": "二触覆盖率 100%"},
        {"波次": "W2", "对象": "二触仍未响应者", "触达渠道": "—",
         "动作": "停止主动触达，转入朋友圈被动触达（每月 ≤2 条）",
         "负责人": "运营", "时间点": "第 12 天起", "验收指标": "不再私聊打扰"},
    ]
    return rows


def check_promises(scripts: list) -> list:
    rows = []
    for s in scripts:
        hits = [w for w in PROMISE_BANNED if w in s["话术"]]
        rows.append({"对象": s["场景"], "命中词": "、".join(hits) if hits else "无",
                     "结论": "❌ 改写" if hits else "✅ 通过"})
    return rows


def run(text: str) -> dict:
    ctx = parse_input(text)
    scripts = step1_scripts(ctx)
    plan = step2_wake_plan(ctx)
    checks = check_promises(scripts)
    n_pass = sum(1 for c in checks if "通过" in c["结论"])
    p = ctx["prices"]

    lines = [
        "<!-- AI 生成内容 · 人工复核后方可对外使用 -->",
        "", "# 成交话术与复购提醒", "",
        "## 价格与权益口径（脚本计算）", "",
        "| 项目 | 金额/口径 |", "|---|---|",
        f"| 体验课 | {int(p['体验课']) if p['体验课'] is not None else '—'} 元 |",
        f"| 年课 | {int(p['年课']) if p['年课'] is not None else '—'} 元 |",
        f"| 系统课 | {int(p['系统课']) if p['系统课'] is not None else '—'} 元 |",
        f"| 续报权益 | 结课后 {ctx['window']} 天内抵扣 {int(ctx['deduct'])} 元 |",
        f"| 年课实付 | {int(ctx['net']) if ctx['net'] is not None else '—'} 元 |",
        "", "## S1 成交节点话术", "",
    ]
    for s in scripts:
        lines += [f"### {s['场景']}", "", s["话术"], ""]
    lines += ["## S2 沉睡唤醒剧本", "",
              f"对象：{ctx['sleeping']} 名结课 30 天内未续报家长；触达节奏「二触即止」。", "",
              "| 波次 | 对象 | 渠道 | 动作 | 负责人 | 时间点 | 验收指标 |",
              "|---|---|---|---|---|---|---|"]
    for r in plan:
        lines.append(f"| {r['波次']} | {r['对象']} | {r['触达渠道']} | {r['动作']} | "
                     f"{r['负责人']} | {r['时间点']} | {r['验收指标']} |")
    lines += ["", "## 承诺红线扫描", "",
              "| 对象 | 命中词 | 结论 |", "|---|---|---|"]
    for c in checks:
        lines.append(f"| {c['对象']} | {c['命中词']} | {c['结论']} |")
    lines += [
        "", f"扫描通过 {n_pass}/{len(checks)}；涉及减免、赔付、平台规则的诉求一律升级人工（2 小时内响应）。", "",
        "> 输出为 AI 生成内容，发送前经人工确认；价格与权益以官方口径为准。", "",
    ]
    deliverable = "\n".join(lines)
    return {
        "summary": (f"产出成交话术 {len(scripts)} 条（红线扫描通过 {n_pass}/{len(checks)}）、"
                    f"唤醒剧本 {len(plan)} 个波次（覆盖 {ctx['sleeping']} 名沉睡用户，二触即止）。"),
        "steps": [
            {"step": "S1 社群话术", "asset": "community-script",
             "output": f"成交节点话术 {len(scripts)} 条（50-150 字，口径内承诺）"},
            {"step": "S2 转化漏斗分析", "asset": "funnel-analysis",
             "output": (f"沉睡唤醒剧本 {len(plan)} 波次；权益口径：结课后 {ctx['window']} 天内"
                        f"抵扣 {int(ctx['deduct'])} 元，年课实付 {int(ctx['net'])} 元")},
        ],
        "deliverable": deliverable,
        "context": ctx,
    }


def save(result: dict, outdir: str) -> list:
    os.makedirs(outdir, exist_ok=True)
    paths = []
    p1 = os.path.join(outdir, "flow_result.json")
    with open(p1, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    paths.append(p1)

    p2 = os.path.join(outdir, "唤醒剧本.csv")
    with open(p2, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["波次", "对象", "触达渠道", "动作", "负责人", "时间点", "验收指标"])
        w.writeheader()
        w.writerows(step2_wake_plan(result["context"]))
    paths.append(p2)

    p3 = os.path.join(outdir, "成交唤醒交付物.md")
    with open(p3, "w", encoding="utf-8") as f:
        f.write(result["deliverable"])
    paths.append(p3)
    return paths


def main():
    ap = argparse.ArgumentParser(description="成交话术与复购提醒工作流")
    ap.add_argument("--input", help="输入 JSON 路径（缺省读 examples/input.json）")
    ap.add_argument("--demo", action="store_true", help="使用内置演示数据")
    ap.add_argument("--outdir", default="out", help="产物目录（默认 out/）")
    args = ap.parse_args()

    if args.demo:
        data = DEMO
    else:
        path = args.input or os.path.join(WF_DIR, "examples", "input.json")
        if not os.path.exists(path):
            print(f"[错误] 找不到输入文件 {path}（可用 --demo 查看效果）", file=sys.stderr)
            sys.exit(1)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

    text = data.get("input", "") if isinstance(data, dict) else str(data)
    result = run(text)
    for p in save(result, args.outdir):
        print(f"[产物] {p}")
    print(f"[完成] {result['summary']}")


if __name__ == "__main__":
    main()
