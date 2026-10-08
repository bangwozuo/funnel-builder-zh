# -*- coding: utf-8 -*-
"""
转化漏斗分析 —— 确定性漏斗计算脚本。

职责：从输入数据中解析私域转化漏斗各环节人数（新添加企业微信 → 7 天内私聊互动
→ 体验课付款 → 年课升级），**由脚本重算各环节转化率**（不信任文本中自带的百分比）、
计算环比变化与周度趋势，产出漏斗诊断报告。

解析规则（确定性）：
  - 环节人数：匹配「<环节名> <N> 人」类句式（新添加企业微信/私聊互动/体验课付款/升级年课等）
  - 对比期：识别 8 月/上月段落中的同口径人数
  - 周度：识别「第 N 周新粉 A/体验课 B」句式
  - 转化率一律 = 下游人数 ÷ 上游人数，保留 1 位小数

用法：
  python funnel_analysis.py --demo --outdir out
  python funnel_analysis.py --input examples/input.json --outdir out

产物（写入 out/）：
  漏斗转化率.csv / 漏斗诊断.json / 转化漏斗分析报告.md
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

DEMO = {
    "data": "私域转化漏斗 2026-09-01 至 09-30：新添加企业微信 820 人；添加后 7 天内发生私聊互动 492 人（60.0%）；体验课付款 74 人（占新粉 9.0%，占已触达 15.0%）；体验课学员升级年课 11 人（14.9%）。8 月对比：新粉 610 人，7 天内私聊 397 人（65.1%），体验课付款 61 人（10.0%），年课转化 9 人（14.8%）。按周拆分：第 1 周新粉 260/体验课 26，第 2 周新粉 210/体验课 18，第 3 周新粉 190/体验课 14，第 4 周新粉 160/体验课 16。",
    "context": "9 月上线两次《自律孩子养成电子书》异业引流活动，合计带来 410 名新粉（占当月新粉一半）；9 月中旬履约助教病假约 1 周，期间欢迎语与 D3 触达延后 1-2 天执行。",
}

STAGE_KEYS = [
    ("新粉", r"(?:新添加企业微信|新粉)\s*(\d+)\s*人"),
    ("7天私聊", r"(?:私聊互动|7\s*天内私聊|7天内私聊)\s*(\d+)\s*人"),
    ("体验课", r"(?:体验课付款|体验课)\s*(\d+)\s*人"),
    ("年课", r"(?:升级年课|年课转化|年课)\s*(\d+)\s*人"),
]


def _first(text: str, pattern: str) -> int | None:
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


def parse_period(text: str, month_hint: str | None = None) -> dict:
    """从一段文本解析漏斗四环节人数。month_hint 用于切分对比期段落。"""
    seg = text
    if month_hint:
        # 取对比期标记之后到下一个句号段落
        m = re.search(r"(?:" + month_hint + r")[^。：:]*[：:](.*?)(?:。|$)", text, re.S)
        if m and m.group(1):
            seg = m.group(1)
    stages = {}
    for name, pat in STAGE_KEYS:
        v = _first(seg, pat)
        if v is not None and name not in stages:
            stages[name] = v
    return stages


def rates(stages: dict) -> list:
    """按链条顺序计算逐级转化率（脚本重算，不采信文本百分比）。"""
    chain = [k for k in ("新粉", "7天私聊", "体验课", "年课") if k in stages]
    rows = []
    for i, k in enumerate(chain):
        row = {"环节": k, "人数": stages[k]}
        if i == 0:
            row["相对上环节"] = "—"
        else:
            prev = stages[chain[i - 1]]
            row["相对上环节"] = f"{round(stages[k] / prev * 100, 1)}%" if prev else "—"
        rows.append(row)
    return rows


def parse_weeks(text: str) -> list:
    weeks = []
    for m in re.finditer(r"第\s*(\d)\s*周新粉\s*(\d+)\s*/\s*体验课\s*(\d+)", text):
        weeks.append({"周": f"第{m.group(1)}周", "新粉": int(m.group(2)), "体验课": int(m.group(3))})
    return weeks


def run(data: dict) -> dict:
    text = data.get("data", "")
    context = data.get("context", "")
    if not text.strip():
        print("[错误] data 为空，无漏斗数据可分析", file=sys.stderr)
        sys.exit(1)

    cur = parse_period(text)
    prev = parse_period(text, r"8\s*月对比|上月对比|7\s*月对比")
    cur_rows = rates(cur)
    prev_rows = rates(prev)
    weeks = parse_weeks(text)

    # 环节级环比
    mom = []
    for k in ("新粉", "7天私聊", "体验课", "年课"):
        if k in cur and k in prev and prev[k]:
            delta = round((cur[k] - prev[k]) / prev[k] * 100, 1)
            mom.append({"环节": k, "本期": cur[k], "上期": prev[k], "环比": f"{delta:+.1f}%"})
        elif k in cur:
            mom.append({"环节": k, "本期": cur[k], "上期": "—", "环比": "—"})

    # 找最大断点：逐级转化率中环比降幅最大的一级
    bottleneck, note = None, ""
    for i in range(1, len(cur_rows)):
        k = cur_rows[i]["环节"]
        if k in cur and k in prev and k in ("7天私聊", "体验课", "年课"):
            rate_cur = cur[k] / cur["新粉"] if cur.get("新粉") else None
            rate_prev = prev[k] / prev["新粉"] if prev.get("新粉") else None
            if rate_cur is not None and rate_prev:
                d = (rate_cur - rate_prev) * 100
                if bottleneck is None or d < bottleneck[1]:
                    bottleneck = (k, d)
    if bottleneck:
        note = (f"漏斗断点在「{bottleneck[0]}」环节：占新粉比例环比 "
                f"{bottleneck[1]:+.1f} 个百分点（本期 {round(cur[bottleneck[0]]/cur['新粉']*100,1)}% "
                f"vs 上期 {round(prev[bottleneck[0]]/prev['新粉']*100,1)}%）。")

    return {
        "本期漏斗": cur_rows, "上期漏斗": prev_rows, "环比": mom,
        "周度": weeks, "断点提示": note, "context": context,
        "assumption": [
            "各环节转化率由脚本按「下游人数÷上游人数」重算并保留 1 位小数，不采信原文自带百分比。",
            "对比期（如 8 月）与周度数据依赖原文句式解析；未解析到的环节在表中记「—」，不做估算。",
            "活动/事件与指标变化仅为时间相关性，因果结论需分渠道数据验证。",
        ],
    }


def save(result: dict, outdir: str) -> list:
    os.makedirs(outdir, exist_ok=True)
    paths = []

    p1 = os.path.join(outdir, "漏斗转化率.csv")
    with open(p1, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["环节", "本期人数", "本期相对上环节", "上期人数", "上期相对上环节"])
        cur = {r["环节"]: r for r in result["本期漏斗"]}
        prev = {r["环节"]: r for r in result["上期漏斗"]}
        for k in ("新粉", "7天私聊", "体验课", "年课"):
            c = cur.get(k, {})
            p = prev.get(k, {})
            w.writerow([k, c.get("人数", "—"), c.get("相对上环节", "—"),
                        p.get("人数", "—"), p.get("相对上环节", "—")])
    paths.append(p1)

    p2 = os.path.join(outdir, "漏斗诊断.json")
    with open(p2, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    paths.append(p2)

    lines = [
        "<!-- AI 生成内容 · 人工复核后方可对外使用 -->",
        "", "# 转化漏斗分析报告", "",
        "## 漏斗转化率（脚本重算）", "",
        "| 环节 | 本期人数 | 相对上环节 | 上期人数 | 上期相对上环节 |",
        "|---|---|---|---|---|",
    ]
    cur = {r["环节"]: r for r in result["本期漏斗"]}
    prev = {r["环节"]: r for r in result["上期漏斗"]}
    for k in ("新粉", "7天私聊", "体验课", "年课"):
        c, p = cur.get(k, {}), prev.get(k, {})
        lines.append(f"| {k} | {c.get('人数','—')} | {c.get('相对上环节','—')} | "
                     f"{p.get('人数','—')} | {p.get('相对上环节','—')} |")
    if result["断点提示"]:
        lines += ["", f"**断点提示**：{result['断点提示']}"]
    if result["周度"]:
        lines += ["", "## 周度趋势", "", "| 周 | 新粉 | 体验课 | 体验课/新粉 |", "|---|---|---|---|"]
        for wk in result["周度"]:
            conv = f"{round(wk['体验课']/wk['新粉']*100,1)}%" if wk["新粉"] else "—"
            lines.append(f"| {wk['周']} | {wk['新粉']} | {wk['体验课']} | {conv} |")
    lines += ["", "## 环节环比", "", "| 环节 | 本期 | 上期 | 环比 |", "|---|---|---|---|"]
    for m in result["环比"]:
        lines.append(f"| {m['环节']} | {m['本期']} | {m['上期']} | {m['环比']} |")
    lines += ["", "## 假设说明", ""]
    for i, a in enumerate(result["assumption"], 1):
        lines.append(f"{i}. {a}")
    if result["context"]:
        lines += ["", f"**背景信息**：{result['context']}"]
    lines += ["", "> 注：以上为 AI 生成内容，结论需人工复核后再用于决策。", ""]
    p3 = os.path.join(outdir, "转化漏斗分析报告.md")
    with open(p3, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    paths.append(p3)
    return paths


def main():
    ap = argparse.ArgumentParser(description="转化漏斗分析（确定性脚本）")
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
    print(f"[完成] 漏斗 {len(result['本期漏斗'])} 个环节 → {args.outdir}/")


if __name__ == "__main__":
    main()
