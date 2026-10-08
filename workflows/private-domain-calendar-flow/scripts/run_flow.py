# -*- coding: utf-8 -*-
"""
私域内容日历工作流 —— 端到端编排脚本。

流程（与 SKILL.md 的 DAG 一致）：
  S1 moments-copy        朋友圈内容：一周 7 天排期（促购 ≤2 条/周）
  S2 community-script    社群话术：群内互动与关键节点提醒话术
  S3 人设语气库（跨仓）   人设一致性检查清单（称呼/口头禅/禁用词）

本脚本承担三步的确定性部分：排期规则生成、促购频次控制、话术模板组装、
禁用词扫描。模型负责在编排平台上做语境化润色；排期与频次口径以本脚本为准。

用法：
  python run_flow.py --input examples/input.json --outdir out
  python run_flow.py --demo

产物：
  out/flow_result.json     机器可读结果（summary/steps/deliverable）
  out/内容日历.csv          一周 7 天排期表
  out/内容日历交付物.md     人读版：日历 + 社群话术 + 人设检查清单
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.dirname(HERE)

DEMO = {
    "input": "下一周（2026-10-12 至 10-18）私域内容排期：人设为幼儿教育顾问「晚晚老师」，"
             "受众为企业微信两个妈妈群（共约 480 人）及微信朋友圈好友；本周业务目标：推广"
             "《在家感统训练 8 节录播课》（199 元）开班报名，课程 10 月 14 日上架，"
             "10 月 18 日 24:00 早鸟价 159 元截止。"
}

# 一周内容骨架：促购条数 ≤2（每周硬约束）
WEEK_SKELETON = [
    ("周一", "价值", "干货分享：与本周课程主题相关的实用技巧"),
    ("周二", "人设", "工作日常/备课花絮，强化顾问身份"),
    ("周三", "促购", "课程上架：课程构成 + 开班信息 + 领取方式"),
    ("周四", "互动", "提问征集：家长最头疼的养育问题，评论区答疑"),
    ("周五", "价值", "学员案例复盘（只陈述事实，不做效果承诺）"),
    ("周六", "促购", "早鸟价倒计时提醒（含截止时间与价格差）"),
    ("周日", "人设", "一周复盘 + 下周预告，收束节奏"),
]

VOICE_BANNED = ["最好", "第一", "100%", "国家级", "顶级", "史上", "绝对", "稳赚"]


def parse_input(text: str) -> dict:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})\s*至\s*(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        start = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        end = date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
    else:
        start, end = date(2026, 10, 12), date(2026, 10, 18)
    persona = re.search(r"[「\u300c]([^」\u300d]+)[」\u300d]", text)
    course = re.search(r"《([^》]+)》", text)
    prices = re.findall(r"(\d+(?:\.\d+)?)\s*元", text)
    launch = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日上架", text)
    deadline = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(\d{1,2}[:：]\d{2})?\s*早鸟", text)
    return {
        "start": start, "end": end,
        "persona": persona.group(1) if persona else "顾问",
        "course": course.group(1) if course else "本周课程",
        "price": prices[0] if prices else "199",
        "early_price": prices[1] if len(prices) > 1 else prices[0] if prices else "159",
        "launch": f"{launch.group(1)}月{launch.group(2)}日" if launch else "本周三",
        "deadline": (f"{deadline.group(1)}月{deadline.group(2)}日 "
                     f"{(deadline.group(3) or '24:00').replace(':', ':')}") if deadline else "周日 24:00",
    }


def step1_calendar(ctx: dict) -> list:
    """S1：一周朋友圈排期（促购 ≤2 条/周），每天配文案要点。"""
    rows = []
    promo_used = 0
    for i, (wd, kind, brief) in enumerate(WEEK_SKELETON):
        d = ctx["start"] + timedelta(days=i)
        if kind == "促购":
            promo_used += 1
        if kind == "促购" and "上架" in brief:
            point = (f"《{ctx['course']}》{ctx['launch']}上架：课程构成 + 适合人群，"
                     f"原价 {ctx['price']} 元，早鸟价 {ctx['early_price']} 元")
        elif kind == "促购":
            point = (f"早鸟价 {ctx['early_price']} 元（原价 {ctx['price']} 元）"
                     f"{ctx['deadline']}截止，附唯一行动指令")
        else:
            point = brief
        rows.append({"日期": d.isoformat(), "星期": wd, "类型": kind,
                     "内容要点": point, "行动指令": "见当日文案" if kind == "促购" else "无（价值/人设内容）"})
    return rows


def step2_group_scripts(ctx: dict) -> list:
    """S2：社群话术（关键节点，每条 50-120 字）。"""
    return [
        {"节点": "课程上架（周三）", "话术": (
            f"姐妹们，《{ctx['course']}》今天上架啦～一共 8 节录播课，在家就能跟练。"
            f"{ctx['launch']}前下单享早鸟价 {ctx['early_price']} 元（原价 {ctx['price']} 元）。"
            "想了解适合自家情况的，群里问我或私我都行。")},
        {"节点": "互动征集（周四）", "话术": (
            "来聊聊：带娃最让你头疼的习惯问题是什么？本周日 24:00 前在群里留言，"
            "我挑 3 个高频问题写一篇具体做法拆解，下周发出来。")},
        {"节点": "早鸟截止提醒（周六）", "话术": (
            f"提醒一下：早鸟价 {ctx['early_price']} 元 {ctx['deadline']}截止，"
            f"之后恢复 {ctx['price']} 元。已经想好要跟练的直接私我「报名」，"
            "我发你链接；还在犹豫的可以先看看前面发的课程介绍。")},
    ]


def step3_voice_check(ctx: dict, texts: list) -> list:
    """S3：人设一致性检查（称呼统一 + 禁用词扫描）。"""
    rows = []
    for t in texts:
        hits = [w for w in VOICE_BANNED if w in t["话术"]]
        rows.append({"对象": t["节点"], "称呼一致": "✅" if "姐妹" in t["话术"] or "家长" in t["话术"] else "⚠️ 检查",
                     "命中禁用词": "、".join(hits) if hits else "无",
                     "结论": "❌ 改写" if hits else "✅ 通过"})
    return rows


def run(text: str) -> dict:
    ctx = parse_input(text)
    calendar = step1_calendar(ctx)
    scripts = step2_group_scripts(ctx)
    checks = step3_voice_check(ctx, scripts)
    n_promo = sum(1 for r in calendar if r["类型"] == "促购")
    n_pass = sum(1 for c in checks if "通过" in c["结论"])

    lines = [
        "<!-- AI 生成内容 · 人工复核后方可对外使用 -->",
        "", f"# 私域内容日历（{ctx['start'].isoformat()} 至 {ctx['end'].isoformat()}）", "",
        f"人设：{ctx['persona']}（幼儿教育顾问）｜主推：《{ctx['course']}》（{ctx['price']} 元，"
        f"早鸟 {ctx['early_price']} 元至 {ctx['deadline']}）", "",
        "## S1 一周朋友圈排期（促购 ≤2 条/周）", "",
        "| 日期 | 星期 | 类型 | 内容要点 |", "|---|---|---|---|",
    ]
    for r in calendar:
        lines.append(f"| {r['日期']} | {r['星期']} | {r['类型']} | {r['内容要点']} |")
    lines += ["", "## S2 社群关键节点话术", ""]
    for s in scripts:
        lines += [f"### {s['节点']}", "", s["话术"], ""]
    lines += ["## S3 人设一致性检查", "",
              "| 对象 | 称呼一致 | 命中禁用词 | 结论 |", "|---|---|---|---|"]
    for c in checks:
        lines.append(f"| {c['对象']} | {c['称呼一致']} | {c['命中禁用词']} | {c['结论']} |")
    lines += [
        "", "## 验收口径", "",
        f"本周排期 7 条（促购 {n_promo} 条，达标线 ≤2）；社群话术 {len(scripts)} 条；"
        f"禁用词扫描通过 {n_pass}/{len(checks)}。全部内容发布前经人工确认。", "",
    ]
    deliverable = "\n".join(lines)
    return {
        "summary": (f"生成 {ctx['start'].isoformat()} 至 {ctx['end'].isoformat()} 排期 7 条"
                    f"（促购 {n_promo} 条 ≤2 达标）、社群话术 {len(scripts)} 条、禁用词通过 {n_pass}/{len(checks)}。"),
        "steps": [
            {"step": "S1 朋友圈文案", "asset": "moments-copy", "output": f"一周 7 天排期（促购 {n_promo} 条，≤2 达标）"},
            {"step": "S2 社群话术", "asset": "community-script", "output": f"关键节点话术 {len(scripts)} 条（50-120 字）"},
            {"step": "S3 人设语气库", "asset": "persona-voice-library（跨仓）", "output": f"一致性检查通过 {n_pass}/{len(checks)}"},
        ],
        "deliverable": deliverable,
        "context": ctx,
    }


def save(result: dict, outdir: str) -> list:
    os.makedirs(outdir, exist_ok=True)
    paths = []
    p1 = os.path.join(outdir, "flow_result.json")
    with open(p1, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1, default=str)
    paths.append(p1)

    p2 = os.path.join(outdir, "内容日历.csv")
    with open(p2, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["日期", "星期", "类型", "内容要点", "行动指令"])
        w.writeheader()
        w.writerows(step1_calendar(result["context"]))
    paths.append(p2)

    p3 = os.path.join(outdir, "内容日历交付物.md")
    with open(p3, "w", encoding="utf-8") as f:
        f.write(result["deliverable"])
    paths.append(p3)
    return paths


def main():
    ap = argparse.ArgumentParser(description="私域内容日历工作流")
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
