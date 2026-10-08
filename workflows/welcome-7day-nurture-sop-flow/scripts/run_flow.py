# -*- coding: utf-8 -*-
"""
欢迎语与 7 天培育 SOP 工作流 —— 端到端编排脚本。

流程（与 SKILL.md 的 DAG 一致）：
  S1 conversion-sop-generate   生成 D1-D7 培育 SOP（每步含渠道/动作/话术/负责人/验收指标）
  S2 fan-segmentation-tag      新粉分层规则：按 7 天内互动/付费信号给出动态分层与触达策略
  S3 回复话术生成（跨仓技能）    按人设产出欢迎语与关键节点（D1/D3/D7）话术

本脚本承担三步的确定性部分：SOP 表生成、分层规则计算、话术模板组装。
模型负责在编排平台上对话术做语境化润色；排期与分层口径以本脚本为准。

用法：
  python run_flow.py --input examples/input.json --outdir out
  python run_flow.py --demo

产物：
  out/flow_result.json      机器可读结果（summary/steps/deliverable）
  out/7天培育SOP.csv         D1-D7 逐步排期表
  out/培育交付物.md          人读版：SOP + 分层规则 + 节点话术
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
    "input": "新粉情况：过去 7 天通过《幼儿园手册模板包》引流品添加企业微信的新粉丝共 156 人，"
             "均为 25-38 岁、关注幼儿教育的妈妈群体。业务目标：7 天内完成新粉分层并按层执行培育剧本，"
             "推动体验课（399 元）付款。可用资源：运营 1 人、社群助教 1 人。"
}

# D1-D7 培育 SOP 骨架（确定性排期）
SOP_ROWS = [
    ("D1", "企微 1 对 1", "发欢迎语+模板包领取引导", "运营", "通过后 10 分钟内", "送达率 100%"),
    ("D1", "朋友圈", "人设价值内容 1 条（非促购）", "运营", "20:00-22:00", "发布 1 条"),
    ("D2", "社群", "新人入群欢迎 + 精华帖引导", "助教", "入群 30 分钟内", "回复率 100%"),
    ("D3", "企微 1 对 1", "1 对 1 需求诊断（3 问清单）", "运营", "D3 上午", "覆盖率 ≥80%"),
    ("D4", "社群", "案例/干货分享（不促购）", "助教", "20:00-22:00", "互动 ≥5 人次"),
    ("D5", "企微 1 对 1", "体验课邀约（按分层话术）", "运营", "D5 上午", "邀约覆盖率 ≥80%"),
    ("D6", "企微 1 对 1", "未响应者换角度二次触达", "运营", "D6 上午", "二次触达率 100%"),
    ("D7", "企微 1 对 1", "优惠提醒 + 结营倒计时（可承诺口径内）", "运营", "D7 上午", "付款转化率 ≥5%"),
]

# 新粉动态分层规则（S2）：按 7 天内信号分层
SEGMENT_RULES = [
    ("高意向", "打开领取+回复诊断 3 问+参加互动", "D5 优先 1 对 1 邀约体验课"),
    ("观望", "已领资料但未回复诊断", "D5 群内案例种草 + D7 优惠提醒"),
    ("沉默", "欢迎语后 72 小时无任何互动", "D6 二次触达一次，14 天无响应停止主动投入"),
]


def parse_input(text: str) -> dict:
    m = re.search(r"新粉丝共\s*(\d+)\s*人", text) or re.search(r"(\d+)\s*人", text)
    fans = int(m.group(1)) if m else 0
    price_m = re.search(r"体验课[（(]?(\d+(?:\.\d+)?)\s*元", text) or re.search(r"(\d+(?:\.\d+)?)\s*元", text)
    price = price_m.group(1) if price_m else "399"
    asset = re.search(r"《([^》]+)》", text)
    asset_name = asset.group(1) if asset else "引流资料包"
    who = re.search(r"(\d+-\d+\s*岁)", text)
    audience = who.group(1) + " 妈妈群体" if who else "新粉群体"
    return {"fans": fans, "price": price, "asset": asset_name, "audience": audience}


def step1_sop(ctx: dict) -> list:
    """S1：D1-D7 SOP 表（五要素齐全）。"""
    return [{"阶段": d, "触达渠道": ch, "动作": act, "话术/模板": "见节点话术",
             "负责人": owner, "时间点": when, "验收指标": kpi}
            for d, ch, act, owner, when, kpi in SOP_ROWS]


def step2_segments(ctx: dict) -> list:
    """S2：新粉动态分层规则与目标人数（按行业参考比例估算，标注为假设）。"""
    rows = []
    ratios = [("高意向", 0.2), ("观望", 0.5), ("沉默", 0.3)]
    for name, ratio in ratios:
        rule, action = next((r, a) for n, r, a in SEGMENT_RULES if n == name)
        est = int(round(ctx["fans"] * ratio)) if ctx["fans"] else 0
        rows.append({"层级": name, "识别信号": rule, "参考占比": f"{int(ratio*100)}%",
                     "估算人数": est, "触达策略": action})
    return rows


def step3_scripts(ctx: dict) -> list:
    """S3：欢迎语与关键节点话术（每条 50-120 字）。"""
    return [
        {"节点": "D1 欢迎语", "话术": (
            f"你好呀，我是晚晚老师的小助手～你要的《{ctx['asset']}》已经备好，"
            "回复「领取」马上发你。拿到后建议先看第一页的用法说明，"
            "有任何问题直接在这个对话框问我，工作日 10:00-19:00 都在。")},
        {"节点": "D3 需求诊断", "话术": (
            "想帮孩子做习惯培养的话，先了解 3 个小问题：①孩子几岁？②最想改善的习惯是哪个？"
            "③平时谁带孩子做练习？你说说看，我给你一份对应的建议。")},
        {"节点": "D5 体验课邀约", "话术": (
            f"根据你的情况，体验课（{ctx['price']} 元）比较合适：有老师带着走完一遍方法，"
            "你再决定要不要继续。本周有一期名额，要不要我帮你留一个？")},
        {"节点": "D7 付款提醒", "话术": (
            f"体验课名额今晚截止，{ctx['price']} 元包含全部课程与资料。"
            "你之前提到孩子的习惯问题，这期内容正好对症。需要我发你报名链接吗？")},
    ]


def run(text: str) -> dict:
    ctx = parse_input(text)
    sop = step1_sop(ctx)
    segments = step2_segments(ctx)
    scripts = step3_scripts(ctx)
    deliverable_lines = [
        "<!-- AI 生成内容 · 人工复核后方可对外使用 -->",
        "", f"# 欢迎语与 7 天培育 SOP（{ctx['fans']} 名新粉 / 体验课 {ctx['price']} 元）", "",
        "## S1 7 天培育 SOP（D1-D7）", "",
        "| 阶段 | 触达渠道 | 动作 | 负责人 | 时间点 | 验收指标 |", "|---|---|---|---|---|---|",
    ]
    for r in sop:
        deliverable_lines.append(f"| {r['阶段']} | {r['触达渠道']} | {r['动作']} | "
                                 f"{r['负责人']} | {r['时间点']} | {r['验收指标']} |")
    deliverable_lines += ["", "## S2 新粉动态分层规则（7 天内信号）", "",
                          "| 层级 | 识别信号 | 参考占比 | 估算人数 | 触达策略 |", "|---|---|---|---|---|"]
    for sgm in segments:
        deliverable_lines.append(f"| {sgm['层级']} | {sgm['识别信号']} | {sgm['参考占比']} | "
                                 f"{sgm['估算人数']} | {sgm['触达策略']} |")
    deliverable_lines += ["", "（占比为行业参考假设，须以实际互动数据复算后修正。）", "",
                          "## S3 关键节点话术", ""]
    for s in scripts:
        deliverable_lines += [f"### {s['节点']}", "", s["话术"], ""]
    deliverable_lines += [
        "## 验收口径", "",
        "欢迎语 10 分钟内送达率 100%；D3 诊断覆盖率 ≥80%；D5 邀约覆盖率 ≥80%；"
        f"D7 体验课付款转化率 ≥5%（约 {int(ctx['fans']*0.05)} 人 / {ctx['fans']} 人）。", "",
        "> 单人日执行动作 ≤6 项；触达频次企微每日 ≤2 次；执行前经运营负责人人工审核。", "",
    ]
    deliverable = "\n".join(deliverable_lines)
    return {
        "summary": (f"生成 D1-D7 培育 SOP 共 {len(sop)} 步、动态分层规则 {len(segments)} 层、"
                    f"节点话术 {len(scripts)} 条；目标 D7 付款转化率 ≥5%。"),
        "steps": [
            {"step": "S1 转化 SOP 生成", "asset": "conversion-sop-generate", "output": f"D1-D7 共 {len(sop)} 步（五要素齐全）"},
            {"step": "S2 粉丝分层打标", "asset": "fan-segmentation-tag", "output": f"动态分层 {len(segments)} 层（信号驱动）"},
            {"step": "S3 回复话术生成", "asset": "reply-script-generate（跨仓）", "output": f"节点话术 {len(scripts)} 条（50-120 字）"},
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

    ctx = result["context"]
    sop = step1_sop(ctx)
    p2 = os.path.join(outdir, "7天培育SOP.csv")
    with open(p2, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["阶段", "触达渠道", "动作", "话术/模板", "负责人", "时间点", "验收指标"])
        w.writeheader()
        w.writerows(sop)
    paths.append(p2)

    p3 = os.path.join(outdir, "培育交付物.md")
    with open(p3, "w", encoding="utf-8") as f:
        f.write(result["deliverable"])
    paths.append(p3)
    return paths


def main():
    ap = argparse.ArgumentParser(description="欢迎语与 7 天培育 SOP 工作流")
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
