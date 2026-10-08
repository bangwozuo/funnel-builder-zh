# -*- coding: utf-8 -*-
"""
粉丝分层打标 —— 确定性打分脚本。

规则（与 prompt.txt / SKILL.md 一致）：
  维度与权重：互动活跃度 40% / 付费行为 40% / 影响力 20%，每维度 0-100 分
  互动活跃度 A = min(100, 近30天群发言数×2)×0.6 + 私聊响应率×40（响应率=响应次数÷私聊次数，0~1）
  付费行为   B = 未付费记 10 分；已付费 = min(100, 50 + 付费单数×15 + min(30, 近90天付费总额÷100))
  影响力     C = min(100, 10 + 朋友圈分享次数×10 + 转介绍人数×15)
  总分 = A×0.4 + B×0.4 + C×0.2，保留 1 位小数
  分层阈值：80-100 关键户 / 60-79 活跃户 / 40-59 潜力户 / 0-39 沉默户

用法：
  python fan_segmentation_tag.py --demo --outdir out
  python fan_segmentation_tag.py --input examples/input.json --outdir out

产物（写入 out/）：
  评分明细.csv / 分层汇总.csv / 打标结果.json / 粉丝分层报告.md
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)

WEIGHTS = {"A": 0.4, "B": 0.4, "C": 0.2}
THRESHOLDS = [(80, 100, "关键户"), (60, 79, "活跃户"), (40, 59, "潜力户"), (0, 39, "沉默户")]

DEMO = {
    "records": (
        "F001|小林|近30天群发言45条，私聊响应8次中6次|近90天付费1单（399元体验课）|朋友圈分享3次，转介绍报名2人\n"
        "F002|阿真|近30天群发言12条，私聊响应3次中1次|近90天付费0单|朋友圈分享1次，转介绍0人\n"
        "F003|多多妈|近30天群发言2条，私聊响应2次中0次|近90天付费2单（199+1999元）|朋友圈分享0次，转介绍0人\n"
        "F004|壳壳|近30天群发言0条，私聊响应5次中0次|近90天付费0单|朋友圈分享0次，转介绍0人\n"
        "F005|Mia|近30天群发言18条，私聊响应4次中3次|近90天付费1单（199元）|朋友圈分享2次，转介绍1人\n"
        "F006|桃子|近30天群发言6条，私聊响应2次中2次|近90天付费0单|朋友圈分享0次，转介绍0人\n"
        "F007|大圣妈|近30天群发言30条，私聊响应4次中4次|近90天付费1单（1999元）|朋友圈分享5次，转介绍3人\n"
        "F008|Aiden爸|近30天群发言1条，私聊响应1次中1次|近90天付费0单|朋友圈分享0次，转介绍0人"
    ),
    "dimensions": "互动活跃度（权重40%）：近30天群发言数与私聊响应率；付费行为（权重40%）：近90天付费单数与金额档位；影响力（权重20%）：朋友圈分享次数与转介绍人数。每维度0-100分。",
    "thresholds": "总分0-100：80-100为关键户，60-79为活跃户，40-59为潜力户，0-39为沉默户",
}


def segment(total: float) -> str:
    for lo, hi, name in THRESHOLDS:
        if lo <= total <= hi:
            return name
    return "沉默户"


def parse_record(line: str) -> dict:
    """解析一行打标记录：ID|昵称|互动字段|付费字段|影响力字段"""
    parts = [p.strip() for p in line.split("|")]
    rid = parts[0] if parts else ""
    name = parts[1] if len(parts) > 1 else ""
    inter = parts[2] if len(parts) > 2 else ""
    pay = parts[3] if len(parts) > 3 else ""
    influ = parts[4] if len(parts) > 4 else ""

    posts = int(re.search(r"群发言(\d+)条", inter).group(1)) if re.search(r"群发言(\d+)条", inter) else 0
    m = re.search(r"私聊响应(\d+)次中(\d+)次", inter)
    chats, replies = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    resp_rate = (replies / chats) if chats > 0 else 0.0

    m = re.search(r"付费(\d+)单", pay)
    orders = int(m.group(1)) if m else 0
    amounts = [int(x) for x in re.findall(r"(\d+)元", pay)]
    amount = sum(amounts)

    m = re.search(r"分享(\d+)次", influ)
    shares = int(m.group(1)) if m else 0
    m = re.search(r"转介绍(?:报名)?(\d+)人", influ)
    referrals = int(m.group(1)) if m else 0
    return {"id": rid, "name": name, "posts": posts, "chats": chats, "replies": replies,
            "resp_rate": resp_rate, "orders": orders, "amount": amount,
            "shares": shares, "referrals": referrals}


def score(rec: dict) -> dict:
    a = min(100.0, rec["posts"] * 2) * 0.6 + rec["resp_rate"] * 40
    if rec["orders"] <= 0:
        b = 10.0
    else:
        b = min(100.0, 50 + rec["orders"] * 15 + min(30, rec["amount"] / 100))
    c = min(100.0, 10 + rec["shares"] * 10 + rec["referrals"] * 15)
    a, b, c = round(a, 1), round(b, 1), round(c, 1)
    total = round(a * WEIGHTS["A"] + b * WEIGHTS["B"] + c * WEIGHTS["C"], 1)
    return {"A": a, "B": b, "C": c, "total": total, "segment": segment(total)}


def run(data: dict) -> dict:
    records_raw = data.get("records", "")
    if not isinstance(records_raw, str):
        records_raw = "\n".join(str(x) for x in records_raw)
    lines = [ln.strip() for ln in records_raw.splitlines() if ln.strip()]
    if not lines:
        print("[错误] records 为空，无记录可打标", file=sys.stderr)
        sys.exit(1)

    rows = []
    for i, ln in enumerate(lines, 1):
        rec = parse_record(ln)
        s = score(rec)
        rows.append({
            "#": i, "ID": rec["id"], "对象": rec["name"],
            "互动活跃度": s["A"], "付费行为": s["B"], "影响力": s["C"],
            "总分": s["total"], "分层": s["segment"],
            "证据": f"发言{rec['posts']}条/响应率{round(rec['resp_rate']*100)}%，"
                    f"付费{rec['orders']}单{rec['amount']}元，分享{rec['shares']}次/转介绍{rec['referrals']}人",
        })

    n = len(rows)
    seg_names = [t[2] for t in THRESHOLDS]
    actions = {
        "关键户": "邀请成为群内 KOC，新品优先内测，转介绍给予约定权益",
        "活跃户": "推升级包或 1 对 1 私聊维护，14 天内跟进一次",
        "潜力户": "用 199 元内低价引流品做首单破冰，两周内观察响应",
        "沉默户": "一次唤醒触达（群内@+朋友圈钩子内容），14 天无响应则停止主动投入",
    }
    summary = []
    for name in seg_names:
        members = [r for r in rows if r["分层"] == name]
        if not members:
            continue
        summary.append({
            "层级": name, "数量": len(members), "占比": f"{round(len(members)/n*100, 1)}%",
            "典型特征": "、".join(f"{r['对象']}(总分{r['总分']})" for r in members[:5]),
            "建议动作": actions[name],
        })

    return {"summary_count": n, "scores": rows, "segments": summary,
            "assumption": [
                "维度与权重（互动活跃度 40%、付费行为 40%、影响力 20%）与分层阈值（80/60/40）来自用户输入，非脚本自定。",
                "各维度 0-100 分由确定性公式计算：互动=min(100,发言×2)×0.6+响应率×40；付费=未付费 10 分，已付费 min(100, 50+单数×15+min(30, 金额÷100))；影响力=min(100, 10+分享×10+转介绍×15)。",
                "同一规则可复算复核；样本少于 30 条时占比波动大（本批每条约占 " + f"{round(1/n*100, 1)}%" + "），批量打标前建议扩大样本复核。",
            ]}


def save(result: dict, outdir: str) -> list:
    os.makedirs(outdir, exist_ok=True)
    paths = []

    p1 = os.path.join(outdir, "评分明细.csv")
    with open(p1, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(result["scores"][0].keys()))
        w.writeheader()
        w.writerows(result["scores"])
    paths.append(p1)

    p2 = os.path.join(outdir, "分层汇总.csv")
    with open(p2, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["层级", "数量", "占比", "典型特征", "建议动作"])
        w.writeheader()
        w.writerows(result["segments"])
    paths.append(p2)

    p3 = os.path.join(outdir, "打标结果.json")
    with open(p3, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    paths.append(p3)

    lines = [
        "<!-- AI 生成内容 · 人工复核后方可对外使用 -->",
        "",
        "# 粉丝分层打标报告",
        "",
        f"本批打标 {result['summary_count']} 条记录，维度权重：互动活跃度 40% / 付费行为 40% / 影响力 20%，"
        "分层阈值 80/60/40（0-100 分）。",
        "",
        "## 评分明细",
        "",
        "| # | 对象 | 互动活跃度 | 付费行为 | 影响力 | 总分 | 分层 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in result["scores"]:
        lines.append(f"| {r['#']} | {r['对象']} | {r['互动活跃度']} | {r['付费行为']} | "
                     f"{r['影响力']} | {r['总分']} | {r['分层']} |")
    lines += ["", "## 分层汇总", "",
              "| 层级 | 数量 | 占比 | 典型特征 | 建议动作 |", "|---|---|---|---|---|"]
    for sgm in result["segments"]:
        lines.append(f"| {sgm['层级']} | {sgm['数量']} | {sgm['占比']} | {sgm['典型特征']} | {sgm['建议动作']} |")
    lines += ["", "## 假设说明", ""]
    for i, a in enumerate(result["assumption"], 1):
        lines.append(f"{i}. {a}")
    lines += ["", "> 注：以上为 AI 生成内容，打标结果需人工复核后再写入粉丝系统（CRM）。", ""]
    p4 = os.path.join(outdir, "粉丝分层报告.md")
    with open(p4, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    paths.append(p4)
    return paths


def main():
    ap = argparse.ArgumentParser(description="粉丝分层打标（确定性脚本）")
    ap.add_argument("--input", help="输入 JSON 路径（缺省读 examples/input.json）")
    ap.add_argument("--demo", action="store_true", help="使用内置演示数据")
    ap.add_argument("--outdir", default="out", help="产物目录（默认 out/）")
    args = ap.parse_args()

    if args.demo:
        data = DEMO
    else:
        path = args.input or os.path.join(SKILL_DIR, "examples", "input.json")
        if not os.path.exists(path):
            print(f"[错误] 找不到输入文件 {path}（可用 --demo 查看效果）", file=sys.stderr)
            sys.exit(1)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

    result = run(data)
    for p in save(result, args.outdir):
        print(f"[产物] {p}")
    print(f"[完成] 打标 {result['summary_count']} 条 → {args.outdir}/")


if __name__ == "__main__":
    main()
