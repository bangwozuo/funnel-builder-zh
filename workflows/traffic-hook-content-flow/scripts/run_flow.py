# -*- coding: utf-8 -*-
"""
引流钩子内容生成工作流 —— 端到端编排脚本。

流程（与 SKILL.md 的 DAG 一致）：
  S1 hook-product-design       钩子产品设计：产出 3-5 条资料包/清单/测评类钩子候选
  S2 钩子文案锻造（跨仓技能）    把候选钩子扩写为朋友圈/社群投放文案
  S3 敏感词预审（跨仓技能）      《广告法》极限词与违规表达核查，拦截不合规项

本脚本承担三步的确定性部分：钩子模板生成、文案组装、敏感词词表扫描。
模型负责在编排平台上对模板做语境化润色；数值与词表结果以本脚本为准。

用法：
  python run_flow.py --input examples/input.json --outdir out
  python run_flow.py --demo

产物：
  out/flow_result.json       机器可读结果（summary/steps/deliverable）
  out/引流钩子交付物.md       人读版：钩子候选 + 投放文案 + 合规结论
  out/敏感词核查.csv          逐条文案的核查明细
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
    "input": "主题：《自律孩子 21 天习惯打卡表》资料包引流钩子；目标人群：微信上 28-40 岁、"
             "孩子 3-8 岁的妈妈群体；发布平台：微信朋友圈+妈妈微信群；目标：吸引目标用户扫码添加"
             "企业微信领取打卡表，进入私域培育流程。"
}

# 敏感词/违禁表达词表（S3 确定性核查）
SENSITIVE_WORDS = [
    "最好", "第一", "100%", "国家级", "顶级", "史上", "绝对", "稳赚",
    "保本", "无风险", "包治", "根治", "立即见效", "涨粉10倍", "躺着赚",
]


def parse_input(text: str) -> dict:
    topic_m = re.search(r"主题[：:]\s*(.+?)(?:资料包)?[；。]", text)
    topic = topic_m.group(1).strip().strip("《》").strip() if topic_m else (text[:30] if text else "引流主题")
    age_m = re.search(r"(\d+)-(\d+)\s*岁", text)
    audience = f"{age_m.group(1)}-{age_m.group(2)} 岁家长群体" if age_m else "目标用户"
    asset = re.search(r"《([^》]+)》", text)
    asset_name = asset.group(1) if asset else "起步资料包"
    days_m = re.search(r"(\d+)\s*天", text)
    days = days_m.group(1) if days_m else "21"
    return {"topic": topic, "audience": audience, "asset": asset_name, "days": days}


def step1_hooks(ctx: dict) -> list:
    """S1 钩子产品设计：3-5 条 ≤30 字钩子，覆盖 3 种类型。"""
    name = ctx["asset"]
    days = ctx["days"]
    return [
        {"type": "资料包型", "hook": f"{days} 天习惯打卡表，免费领", "len": len(f"{days} 天习惯打卡表，免费领")},
        {"type": "清单型", "hook": f"孩子习惯养不成？先用这张「{name}」自查", "len": 0},
        {"type": "反差型", "hook": "不是孩子不自觉，是打卡姿势不对", "len": 0},
        {"type": "提问型", "hook": f"{days} 天能改掉磨蹭吗？这份打卡表试试", "len": 0},
    ]


def step2_copy(ctx: dict, hooks: list) -> list:
    """S2 钩子文案锻造：朋友圈投放文案（正文 120-200 字区间按模板控制）。"""
    copies = []
    for i, h in enumerate(hooks[:3], 1):
        copies.append({
            "#": i,
            "类型": h["type"],
            "首句钩子": h["hook"],
            "投放文案": (
                f"{h['hook']}\n"
                f"我整理了一份{ctx['asset']}，把「目标-打卡-复盘」拆成每天 10 分钟就能完成的小步骤，"
                f"适合{ctx['audience']}。添加企业微信回复「打卡表」就能领取，"
                "领完建议今晚就和孩子约定第一周的小目标。数量没有噱头，领了就用得上。"
            ),
            "预期动作": "扫码添加企业微信，回复「打卡表」领取",
        })
    return copies


def step3_check(copies: list) -> list:
    """S3 敏感词预审：逐条扫描词表，命中即标记（「第一」后接序数词的排除）。"""
    rows = []
    for c in copies:
        text = c["首句钩子"] + c["投放文案"]
        hits = []
        for w in SENSITIVE_WORDS:
            for m in re.finditer(re.escape(w), text):
                tail = text[m.end():m.end() + 1]
                if w == "第一" and tail in "周天次步条季度章页轮":
                    continue  # 序数用法（如「第一周」）不算极限词
                hits.append(w)
                break
        rows.append({"#": c["#"], "对象": c["首句钩子"][:20],
                     "命中词": "、".join(hits) if hits else "无",
                     "结论": "❌ 拦截，需改写" if hits else "✅ 通过"})
        c["合规"] = "通过" if not hits else "拦截"
    return rows


def run(text: str) -> dict:
    ctx = parse_input(text)
    hooks = step1_hooks(ctx)
    copies = step2_copy(ctx, hooks)
    checks = step3_check(copies)
    n_pass = sum(1 for r in checks if "通过" in r["结论"])
    deliverable_lines = [
        "<!-- AI 生成内容 · 人工复核后方可对外使用 -->",
        "", f"# 引流钩子交付物（主题：{ctx['topic']}）", "",
        "## S1 钩子候选（≤30 字）", "",
        "| # | 类型 | 钩子 | 字数 |", "|---|---|---|---|",
    ]
    for i, h in enumerate(hooks, 1):
        deliverable_lines.append(f"| {i} | {h['type']} | {h['hook']} | {len(h['hook'])} |")
    deliverable_lines += ["", "## S2 投放文案（朋友圈 120-200 字）", ""]
    for c in copies:
        deliverable_lines += [f"### 文案 {c['#']}（{c['类型']}，{c['合规']}）", "", c["投放文案"],
                              "", f"预期动作：{c['预期动作']}", ""]
    deliverable_lines += [
        f"## S3 敏感词预审结论", "",
        f"共核查 {len(checks)} 条，通过 {n_pass} 条，拦截 {len(checks) - n_pass} 条；"
        "拦截项已标注命中词，改写后需复检。明细见 out/敏感词核查.csv。", "",
        "> 发布前保留人工确认环节；引流路径须真实可达。", "",
    ]
    deliverable = "\n".join(deliverable_lines)
    return {
        "summary": f"产出 {len(hooks)} 条钩子候选、{len(copies)} 条投放文案；敏感词预审通过 {n_pass}/{len(checks)}。",
        "steps": [
            {"step": "S1 钩子产品设计", "asset": "hook-product-design", "output": f"{len(hooks)} 条候选（≤30 字，3 种类型）"},
            {"step": "S2 钩子文案锻造", "asset": "hook-copy-craft（跨仓）", "output": f"{len(copies)} 条投放文案（朋友圈）"},
            {"step": "S3 敏感词预审", "asset": "sensitive-word-precheck（跨仓）", "output": f"通过 {n_pass}/{len(checks)}"},
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
    p2 = os.path.join(outdir, "引流钩子交付物.md")
    with open(p2, "w", encoding="utf-8") as f:
        f.write(result["deliverable"])
    paths.append(p2)
    p3 = os.path.join(outdir, "敏感词核查.csv")
    rows = step3_check(step2_copy(result["context"], step1_hooks(result["context"])))
    with open(p3, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["#", "对象", "命中词", "结论"])
        w.writeheader()
        w.writerows(rows)
    paths.append(p3)
    return paths


def main():
    ap = argparse.ArgumentParser(description="引流钩子内容生成工作流")
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
