"""Validate the V1 contract ledger and render local progress views (standard library only).

Run --render then --check after editing plan/fusion/PROGRESS.json. Validation
checks structure, arithmetic and repository reference existence, never evidence truth.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import math
from pathlib import Path, PureWindowsPath
import re
import sys
import tempfile
import unittest
from urllib.parse import quote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("plan/fusion/PROGRESS.json")
OUTPUTS = (Path("plan/fusion/PROGRESS.md"), Path("docs/开发进度看板.html"))
STATES = {"met", "unmet", "unknown", "not_applicable"}
BLOCKERS = {"code", "data", "observation", "authorization", "dependency", "decision", "verification"}
LABELS = {"met": "已满足", "unmet": "未满足", "unknown": "待核验", "not_applicable": "不适用"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def repository_url_valid(value):
    if not isinstance(value, str) or any(char.isspace() for char in value) or "\\" in value:
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        labels = hostname.split(".")
        valid_host = len(hostname) <= 253 and all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels)
        # Accessing port also rejects malformed/non-numeric/out-of-range ports.
        parsed.port
        return (parsed.scheme == "https" and valid_host and parsed.username is None
                and parsed.password is None and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def fingerprint(tasks):
    keys = ("id", "title", "primary_g", "tags", "requirements", "weight", "required", "acceptance_criterion", "applicability")
    rows = [{k: sorted(t[k]) if k in {"tags", "requirements"} else t[k] for k in keys}
            for t in sorted(tasks, key=lambda row: row["id"])]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def reference(root, value, *, allow_missing=False):
    require(nonempty(value), "引用路径不能为空")
    win = PureWindowsPath(value)
    path = Path(value.replace("\\", "/"))
    require(not path.is_absolute() and not win.drive and not win.root and ".." not in path.parts
            and ":" not in value, "引用必须为仓库内相对路径")
    resolved = (root / path).resolve()
    require(resolved.is_relative_to(root.resolve()), "引用逃逸仓库")
    require(resolved.is_file() or (allow_missing and not resolved.exists()), f"引用文件不存在: {value}")
    return path.as_posix()


def closed(task):
    return all(task[k]["status"] in {"met", "not_applicable"} for k in ("engineering", "acceptance"))


def validate(data, root, *, allow_generated_missing=False):
    require(isinstance(data, dict) and data.get("schema_version") == 1, "schema_version 必须为1")
    for key in ("scope_revision", "as_of", "product_base_commit"):
        require(nonempty(data.get(key)), f"缺少 {key}")
    if "repository_url" in data:
        require(repository_url_valid(data["repository_url"]), "repository_url须为无凭据、查询或片段的合法HTTPS仓库URL")
    from datetime import date
    require(re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["as_of"]) is not None, "as_of 日期格式非法")
    date.fromisoformat(data["as_of"])
    mix = data.get("metric_mix", {})
    require(set(mix) == {"engineering", "acceptance"}, "metric_mix 字段不完整")
    require(all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in mix.values())
            and math.isclose(sum(mix.values()), 1, abs_tol=1e-12), "metric_mix 必须为正且和为1")
    require(mix == {"engineering": 0.5, "acceptance": 0.5}, "schema_version=1固定工程/验收各50%；调整须建立新计分合同")
    milestones = data.get("milestones", [])
    require(isinstance(milestones, list), "milestones 必须为列表")
    gids = [m["id"] for m in milestones]
    require(len(gids) == len(set(gids)) and set(gids) == {f"G{i}" for i in range(1, 9)}, "milestones 必须唯一覆盖G1-G8")
    tasks = data.get("tasks", [])
    require(isinstance(tasks, list) and tasks, "tasks 不能为空")
    ids = [t["id"] for t in tasks]
    require(all(nonempty(i) for i in ids) and len(ids) == len(set(ids)), "任务ID为空或重复")
    requirements = set()
    for t in tasks:
        tid = t["id"]
        for field in ("title", "acceptance_criterion"):
            require(nonempty(t.get(field)), f"{tid}: 缺少{field}")
        require(t.get("primary_g") in gids, f"{tid}: primary_g非法")
        require(type(t.get("weight")) is int and t["weight"] in (1, 2, 3), f"{tid}: 权重须为1/2/3")
        require(type(t.get("required")) is bool, f"{tid}: required须为布尔值")
        applicability = t.get("applicability", {})
        require(isinstance(applicability, dict) and set(applicability) == {"engineering", "acceptance"}
                and all(type(v) is bool for v in applicability.values()), f"{tid}: applicability须声明两个通道的布尔适用性")
        require(not t["required"] or applicability["acceptance"], f"{tid}: 必需条款验收必须适用")
        for field, pattern in (("tags", r"T0[1-7]"), ("requirements", r"R0[1-9]")):
            vals = t.get(field)
            require(isinstance(vals, list) and all(isinstance(v, str) and re.fullmatch(pattern, v) for v in vals)
                    and len(vals) == len(set(vals)), f"{tid}: {field}非法或重复")
        require(t["requirements"], f"{tid}: 必须映射需求")
        requirements.update(t["requirements"])
        require(t.get("phase") in {f"S{i}" for i in range(1, 6)}, f"{tid}: phase非法")
        for channel in ("engineering", "acceptance"):
            state = t.get(channel, {})
            require(isinstance(state, dict) and state.get("status") in STATES, f"{tid}: {channel}状态非法")
            require((state["status"] == "not_applicable") == (not applicability[channel]),
                    f"{tid}: {channel}的NA状态与冻结适用性不符")
            evidence = state.get("evidence")
            require(isinstance(evidence, list), f"{tid}: evidence须为列表")
            require(state["status"] != "met" or bool(evidence), f"{tid}: met必须有证据")
            require(state["status"] != "not_applicable" or nonempty(state.get("reason")), f"{tid}: NA必须有理由")
            for e in evidence:
                require(isinstance(e, dict), f"{tid}: 证据须为对象")
                reference(root, e.get("path"))
                for optional in ("revision", "anchor"):
                    require(optional not in e or isinstance(e[optional], str), f"{tid}: 证据{optional}非法")
                if "revision" in e:
                    require(re.fullmatch(r"[0-9a-fA-F]{40}", e["revision"]) is not None,
                            f"{tid}: 证据revision须为40位十六进制提交号")
                    require(repository_url_valid(data.get("repository_url")), f"{tid}: 固定版本证据需要合法repository_url")
        blockers = t.get("blockers")
        require(isinstance(blockers, list), f"{tid}: blockers须为列表")
        for b in blockers:
            require(b.get("type") in BLOCKERS and nonempty(b.get("reason"))
                    and nonempty(b.get("release_condition")), f"{tid}: 阻断须有类型、原因和解除条件")
        require(closed(t) or (nonempty(t.get("next_action")) and bool(blockers)), f"{tid}: 未完成须有下一步与阻断解除条件")
        deps = t.get("depends_on")
        require(isinstance(deps, list) and all(d in ids for d in deps)
                and len(deps) == len(set(deps)), f"{tid}: 依赖不存在或重复")
    require(requirements == {f"R{i:02}" for i in range(1, 10)}, "R01-R09必须全部映射")
    by_id = {t["id"]: t for t in tasks}
    visiting, visited = set(), set()

    def visit(tid):
        require(tid not in visiting, "依赖存在环")
        if tid in visited:
            return
        visiting.add(tid)
        for dep in by_id[tid]["depends_on"]:
            visit(dep)
        visiting.remove(tid)
        visited.add(tid)

    for tid in ids:
        visit(tid)
    roadmap = data.get("roadmap", [])
    require(isinstance(roadmap, list) and len(roadmap) == 5
            and {p["id"] for p in roadmap} == {f"S{i}" for i in range(1, 6)}, "路线须唯一覆盖S1-S5")
    assigned = []
    for phase in roadmap:
        require(nonempty(phase.get("title")) and bool(phase.get("exit_criteria")), "路线须有标题与退出条件")
        require(isinstance(phase.get("task_ids"), list), "路线task_ids须为列表")
        for tid in phase["task_ids"]:
            require(tid in by_id and by_id[tid]["phase"] == phase["id"], "路线任务不存在或阶段错配")
            assigned.append(tid)
    require(sorted(assigned) == sorted(ids), "每个任务须恰归一个路线阶段")
    actual = fingerprint(tasks)
    require(data.get("scope_fingerprint") == actual, "scope_fingerprint与条款不符")
    changes = data.get("changes", [])
    require(isinstance(changes, list) and changes, "缺少范围变更账本")
    previous_hash = previous_weight = None
    change_ids = set()
    for change in changes:
        require(nonempty(change.get("id")) and change["id"] not in change_ids, "变更ID为空或重复")
        change_ids.add(change["id"])
        require(change.get("type") in {"baseline", "scope_change", "progress", "reopen"}
                and all(nonempty(change.get(k)) for k in ("date", "scope_revision", "reason")), "变更记录字段非法")
        if change["type"] in {"baseline", "scope_change"}:
            require(re.fullmatch(r"[0-9a-f]{64}", change.get("new_fingerprint", "")) is not None
                    and type(change.get("new_total_weight")) is int and change["new_total_weight"] > 0, "范围记录缺少新指纹或总权重")
            if change["type"] == "baseline":
                require(previous_hash is None, "baseline只能建立一次")
            else:
                require(previous_hash is not None and change.get("old_fingerprint") == previous_hash
                        and change.get("old_total_weight") == previous_weight, "scope_change旧范围与前次声明不符")
            previous_hash, previous_weight = change["new_fingerprint"], change["new_total_weight"]
            last_scope_revision = change["scope_revision"]
    require(previous_hash == actual and previous_weight == sum(t["weight"] for t in tasks)
            and last_scope_revision == data["scope_revision"], "当前范围与最后范围声明不符")
    batches = data.get("history_batches", [])
    require(isinstance(batches, list), "history_batches须为列表")
    batch_ids = [batch.get("id") for batch in batches]
    require(all(nonempty(i) for i in batch_ids) and len(batch_ids) == len(set(batch_ids)), "历史批次ID为空或重复")
    for batch in batches:
        require(batch.get("progress_delta") is None and nonempty(batch.get("reason")), "历史批次缺少可比基线，不得虚构百分点")
        require(isinstance(batch.get("task_ids"), list) and all(t in ids for t in batch["task_ids"]), "历史批次任务引用非法")
    require(isinstance(data.get("plan_catalog"), list) and isinstance(data.get("governance"), dict), "缺少计划目录或治理说明")
    for p in data["plan_catalog"]:
        # Only --render may bootstrap its two fixed outputs. Evidence references
        # and every other catalog reference always require existing files.
        bootstrap = (allow_generated_missing and p.get("role") == "generated"
                     and p.get("path") in {output.as_posix() for output in OUTPUTS})
        reference(root, p.get("path"), allow_missing=bootstrap)
        require(nonempty(p.get("role")) and nonempty(p.get("purpose")), "计划目录缺少职责或用途")
    legacy = data.get("legacy_backlog", [])
    require(isinstance(legacy, list), "legacy_backlog须为列表")
    legacy_ids = set()
    for item in legacy:
        require(isinstance(item, dict) and nonempty(item.get("id")) and item["id"] not in legacy_ids,
                "历史遗留ID为空或重复")
        legacy_ids.add(item["id"])
        require(item.get("classification") in {"mapped", "needs_revalidation", "optional_decision", "historical_closed"},
                "历史遗留分类非法")
        require(item.get("scored") is False, "历史遗留scored必须为false，不重复计分")
        require(isinstance(item.get("task_ids"), list) and all(t in ids for t in item["task_ids"])
                and len(item["task_ids"]) == len(set(item["task_ids"])), "历史遗留映射不存在或重复")
        require(all(nonempty(item.get(k)) for k in ("title", "reason", "next_action")), "历史遗留缺少标题、原因或下一步")
        reference(root, item.get("source"))
    return data


def metric(tasks, mix):
    result = {}
    for channel in ("engineering", "acceptance"):
        weights = {s: sum(t["weight"] for t in tasks if t[channel]["status"] == s) for s in STATES}
        denominator = sum(weights[s] for s in STATES if s != "not_applicable")
        result[channel] = {"denominator": denominator, "met": weights["met"], "unknown": weights["unknown"],
                           "lower": 100 * weights["met"] / denominator if denominator else None,
                           "upper": 100 * (weights["met"] + weights["unknown"]) / denominator if denominator else None}
    for bound in ("lower", "upper"):
        result[bound] = (sum(result[c][bound] * mix[c] for c in mix)
                         if all(result[c][bound] is not None for c in mix) else None)
    required = [t for t in tasks if t["required"]]
    result["closed"] = bool(required) and all(closed(t) for t in required)
    return result


def interval(lower, upper):
    return "不适用" if lower is None else f"{lower:.1f}%–{upper:.1f}%"


def cells(label, tasks, mix):
    m = metric(tasks, mix)
    values = [label, str(len(tasks)), str(sum(t["weight"] for t in tasks))]
    for channel in ("engineering", "acceptance"):
        c = m[channel]
        values.append(f'{c["met"]}/{c["denominator"]}; ' + interval(c["lower"], c["upper"]))
    return values + [interval(m["lower"], m["upper"]), "已闭合" if m["closed"] else "未闭合"]


EXPLANATION = ("V1合同进度指数 / 确认下界–可能上界。E=工程met权重/工程适用权重，A=验收met权重/验收适用权重；"
               "not_applicable仅按冻结适用性及理由剔除分母，改变适用性须登记scope_change；必需条款验收始终适用。unknown保留分母、仅计入可能上界，unmet不增加上界。"
               "P按metric_mix加权E与A；任一通道无适用分母时指数为不适用。"
               "不是工时、投资效果或发布通过率；100%不能绕过required条款全部工程与验收满足或有理由不适用。"
               "G0持续治理不在分母。T标签可重叠，仅供分组观察，不能相加为总分。"
               "校验仅检查结构、计算及仓库引用存在，不验证证据内容或替代独立审批。")
HEADERS = ["范围", "条款数", "总权重", "工程 已满足/适用; 下界–上界", "验收 已满足/适用; 下界–上界", "合同指数", "required门"]


def summaries(data):
    tasks, mix = data["tasks"], data["metric_mix"]
    rows = [cells("整体", tasks, mix)]
    rows.extend(cells(m["id"] + " " + m["title"], [t for t in tasks if t["primary_g"] == m["id"]], mix)
                for m in data["milestones"])
    rows.extend(cells(tag, [t for t in tasks if tag in t["tags"]], mix) for tag in [f"T{i:02}" for i in range(1, 8)])
    return rows


def md_text(value):
    return html.escape(str(value), quote=False).replace("|", "&#124;").replace("\n", "<br>")


def link(evidence, prefix, markdown=True, repository_url=None):
    path = quote(evidence["path"].replace("\\", "/"), safe="/")
    url = (repository_url.rstrip("/") + "/blob/" + evidence["revision"] + "/" + path
           if evidence.get("revision") else prefix + path)
    label = evidence["path"] + (" @" + evidence["revision"] if evidence.get("revision") else "")
    locator = "；定位：" + evidence["anchor"] if evidence.get("anchor") else ""
    if markdown:
        return f'[{md_text(label).replace("[", "&#91;").replace("]", "&#93;")}]({url})' + md_text(locator)
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>' + html.escape(locator)


def status_text(task):
    return " / ".join(LABELS[task[c]["status"]] for c in ("engineering", "acceptance"))


def render_markdown(data):
    lines = ["<!-- Generated by scripts/check_progress.py; edit PROGRESS.json instead. -->",
             "# V1合同进度看板", "", f'基准日：{md_text(data["as_of"])}；范围：{md_text(data["scope_revision"])}；产品基线：{md_text(data["product_base_commit"])}。',
             "", EXPLANATION, "", f'当前混合权重：工程 {data["metric_mix"]["engineering"]:g}，验收 {data["metric_mix"]["acceptance"]:g}。', "",
             "| " + " | ".join(HEADERS) + " |", "| " + " | ".join(["---"] * len(HEADERS)) + " |"]
    lines.extend("| " + " | ".join(md_text(v) for v in row) + " |" for row in summaries(data))
    for done, title in ((True, "已闭合条款"), (False, "剩余条款")):
        lines += ["", "## " + title, ""]
        for t in data["tasks"]:
            if closed(t) != done:
                continue
            lines += [f'### {md_text(t["id"])} · {md_text(t["title"])}', "",
                      f'权重 {t["weight"]}；{t["primary_g"]}；{t["phase"]}；{md_text(", ".join(t["tags"] + t["requirements"]))}；required={str(t["required"]).lower()}；工程/验收：{status_text(t)}。', "",
                      "验收条件：" + md_text(t["acceptance_criterion"]), ""]
            for c, label in (("engineering", "工程证据"), ("acceptance", "验收证据")):
                evidence = "; ".join(link(e, "../../", repository_url=data.get("repository_url")) for e in t[c]["evidence"]) or "无"
                lines += [label + "：" + evidence + ("；理由：" + md_text(t[c]["reason"]) if t[c].get("reason") else ""), ""]
            lines += ["依赖：" + md_text(", ".join(t["depends_on"]) or "无"), ""]
            for b in t["blockers"]:
                lines += [f'- {md_text(b["type"])}：{md_text(b["reason"])}；解除条件：{md_text(b["release_condition"])}']
            lines += ["", "下一步：" + md_text(t.get("next_action") or "无"), ""]
    lines += ["## 路线与计划目录", ""]
    for p in data["roadmap"]:
        lines += [f'- {md_text(p["id"])} {md_text(p["title"])}：{md_text(p["exit_criteria"])}；条款：{md_text(", ".join(p["task_ids"]))}']
    lines += [""]
    for p in data["plan_catalog"]:
        lines += [f'- {link(p, "../../")}：{md_text(p["role"])}；{md_text(p["purpose"])}']
    lines += ["", "## 最近批次与范围账本", "", "本次建立管理基线，不虚构原三批的进度百分点；历史批次缺少当时冻结分母时不追算。", ""]
    for b in data.get("history_batches", []):
        lines += [f'- {md_text(b["id"])} @{md_text(b.get("commit", "未登记"))}：{md_text(b.get("summary", ""))}；Δ=未追算；{md_text(b["reason"])}']
    for c in data["changes"]:
        lines += [f'- {md_text(c["date"])} {md_text(c["id"])} / {md_text(c["type"])} / {md_text(c["scope_revision"])}：{md_text(c["reason"])}']
    lines += ["", "## 历史遗留分流（不重复计分；待复核不是剔除V1）", ""]
    for item in data.get("legacy_backlog", []):
        lines += [f'- {md_text(item["id"])} {md_text(item["title"])}；状态：{md_text(item["classification"])}；来源：'
                  + link({"path": item["source"]}, "../../")
                  + f'；映射：{md_text(", ".join(item["task_ids"]) or "暂无，待分流")}'
                  + f'；原因：{md_text(item["reason"])}；下一步：{md_text(item["next_action"])}']
    lines += ["", "治理：" + md_text(json.dumps(data["governance"], ensure_ascii=False, sort_keys=True)), ""]
    return "\n".join(lines)


def render_html(data):
    esc = lambda v: html.escape(str(v), quote=True)
    overall = metric(data["tasks"], data["metric_mix"])
    percent = lambda value: "不适用" if value is None else f"{value:.1f}%"
    closed_count = sum(closed(t) for t in data["tasks"])
    total_count = len(data["tasks"])
    cards = "".join(
        '<article class="card" data-metric="' + key + '"><span>' + esc(label)
        + '</span><strong>' + esc(value) + '</strong><small>' + esc(note) + '</small></article>'
        for key, label, value, note in (
            ("overall-lower", "已确认综合指数", percent(overall["lower"]), "待核上界 " + percent(overall["upper"])),
            ("engineering-lower", "工程确认下界", percent(overall["engineering"]["lower"]), "按适用条款权重计算"),
            ("acceptance-lower", "验收确认下界", percent(overall["acceptance"]["lower"]), "按适用条款权重计算"),
            ("closed-count", "闭合条款", f"{closed_count}/{total_count}", f"剩余 {total_count - closed_count} 条")))
    gate_note = ("必需条款门已满足，发布仍须独立审批。" if overall["closed"]
                 else "必需条款门尚未通过，V1整体未完成。")
    rows = "".join("<tr>" + "".join("<td>" + esc(v) + "</td>" for v in row) + "</tr>" for row in summaries(data))
    task_rows = []
    for t in data["tasks"]:
        state = "closed" if closed(t) else "open"
        evidence = "<br>".join(esc(c) + ": " + ("; ".join(link(e, "../", False, data.get("repository_url")) for e in t[c]["evidence"]) or "无")
                               + ("; " + esc(t[c]["reason"]) if t[c].get("reason") else "") for c in ("engineering", "acceptance"))
        blockers = "<br>".join(esc(b["type"] + ": " + b["reason"] + "；解除: " + b["release_condition"]) for b in t["blockers"])
        values = [esc(t["id"] + " " + t["title"]), esc(t["primary_g"] + "/" + t["phase"] + " " + ",".join(t["tags"] + t["requirements"])),
                  esc(str(t["weight"]) + (" 必需" if t["required"] else " 可选")), esc(status_text(t)),
                  esc(t["acceptance_criterion"]) + "<br>" + evidence,
                  esc(", ".join(t["depends_on"]) or "无") + "<br>" + blockers, esc(t.get("next_action") or "无")]
        task_rows.append(f'<tr data-phase="{esc(t["phase"])}" data-state="{state}" data-status="{esc(t["engineering"]["status"] + " " + t["acceptance"]["status"])}">'
                         + "".join("<td>" + v + "</td>" for v in values) + "</tr>")
    roadmap = "".join("<li>" + esc(p["id"] + " " + p["title"] + ": " + str(p["exit_criteria"]) + "；" + ", ".join(p["task_ids"])) + "</li>" for p in data["roadmap"])
    catalog = "".join("<li>" + link(p, "../", False) + " " + esc(p["role"] + ": " + p["purpose"]) + "</li>" for p in data["plan_catalog"])
    batches = "".join("<li>" + esc(b["id"] + " @" + b.get("commit", "未登记") + " " + b.get("summary", "") + "；Δ未追算；" + b["reason"]) + "</li>" for b in data.get("history_batches", []))
    changes = "".join("<li>" + esc(c["date"] + " " + c["id"] + " / " + c["type"] + " / " + c["scope_revision"] + ": " + c["reason"]) + "</li>" for c in data["changes"])
    legacy_labels = {"mapped": "已归并", "needs_revalidation": "待源码复核", "optional_decision": "待范围裁决", "historical_closed": "历史已处理记录"}
    legacy = "".join('<li data-classification="' + esc(item["classification"]) + '">'
                     + esc(item["id"] + " " + item["title"] + "；状态：" + legacy_labels[item["classification"]] + "；来源：")
                     + link({"path": item["source"]}, "../", False)
                     + esc("；映射：" + (", ".join(item["task_ids"]) or "暂无，待分流") + "；原因：" + item["reason"] + "；下一步：" + item["next_action"])
                     + "</li>" for item in data.get("legacy_backlog", []))
    return ('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>V1合同进度看板</title><style>body{font:15px/1.6 system-ui,sans-serif;margin:28px;color:#182c40;background:#f6f8fb}h1,h2{color:#123e66}table{border-collapse:collapse;background:white;width:100%;margin:18px 0}td,th{padding:10px;border:1px solid #d7e0e8;text-align:left;vertical-align:top}th{background:#e5edf5}input,select{padding:9px;margin:6px}a{color:#125e97}tr[hidden]{display:none}.scroll{overflow:auto}small{color:#52667a}.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:22px 0}.card{background:white;border:1px solid #d7e0e8;border-radius:12px;padding:20px}.card span,.card small{display:block}.card strong{display:block;font-size:36px;line-height:1.5;color:#123e66}.gate{font-weight:600;color:#754a08}details{background:#edf2f7;padding:12px 16px;border-radius:8px}summary{cursor:pointer}@media(max-width:800px){.cards{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:450px){body{margin:16px}.cards{grid-template-columns:1fr}}</style>
<h1>V1合同进度看板</h1><p>'''
            + esc(data["as_of"] + " / " + data["scope_revision"] + " / 产品基线 " + data["product_base_commit"])
            + '</p><div class="cards">' + cards + '</div><p>依据现有合同/验收记录建立，未重新全面代码审计。</p>'
            + '<p>以上为合同进度确认下界；待核上界仅包含unknown可能贡献，不代表实际完工或发布通过。</p>'
            + '<p class="gate">' + esc(gate_note) + '</p><details><summary>查看计分口径与限制</summary><p>'
            + esc(EXPLANATION) + "</p><p>工程/验收混合权重: "
            + esc(str(data["metric_mix"]["engineering"]) + " / " + str(data["metric_mix"]["acceptance"]))
            + '</p></details><div class="scroll"><table><thead><tr>' + "".join("<th>" + esc(h) + "</th>" for h in HEADERS)
            + "</tr></thead><tbody>" + rows + "</tbody></table></div><h2>条款明细</h2>"
            + '<label>搜索 <input id="query" type="search"></label><label>阶段 <select id="phase"><option value="">全部</option>'
            + "".join(f'<option>S{i}</option>' for i in range(1, 6))
            + '</select></label><label>状态 <select id="state"><option value="">全部</option><option value="closed">已闭合</option><option value="open">未闭合</option>'
            + "".join(f'<option value="{s}">{esc(LABELS[s])}</option>' for s in sorted(STATES))
            + '</select></label><span id="count"></span><div class="scroll"><table><thead><tr>'
            + "".join("<th>" + h + "</th>" for h in ("条款", "归属", "权重", "工程/验收", "验收条件与证据", "依赖与阻断", "下一步"))
            + '</tr></thead><tbody id="tasks">' + "".join(task_rows) + "</tbody></table></div><h2>路线与计划目录</h2><ul>"
            + roadmap + "</ul><ul>" + catalog + "</ul><h2>最近批次</h2><p>本次建立管理基线，不虚构原三批百分点。</p><ul>"
            + batches + "</ul><h2>范围与进度账本</h2><ul>" + changes
            + "</ul><h2>历史遗留分流（不重复计分；待复核不是剔除V1）</h2><ul>" + legacy
            + "</ul><p>治理: " + esc(json.dumps(data["governance"], ensure_ascii=False, sort_keys=True))
            + '''</p><small>由 scripts/check_progress.py 从 PROGRESS.json 生成。样式与筛选脚本无需网络；带提交号的证据链接固定仓库版本，定位说明为文字；未带提交号的目录链接当前工作树。脚本不读取远端。</small>
<script>const q=document.getElementById('query'),p=document.getElementById('phase'),s=document.getElementById('state');function filter(){let count=0;for(const row of document.querySelectorAll('#tasks tr')){row.hidden=!(row.textContent.toLowerCase().includes(q.value.toLowerCase())&&(!p.value||row.dataset.phase===p.value)&&(!s.value||row.dataset.state===s.value||row.dataset.status.split(' ').includes(s.value)));if(!row.hidden)count++;}document.getElementById('count').textContent='显示 '+count+' 条';}for(const el of [q,p,s])el.addEventListener('input',filter);filter();</script></html>
''')


def no_duplicate_keys(pairs):
    result = {}
    for k, v in pairs:
        require(k not in result, "JSON重复键: " + k)
        result[k] = v
    return result


def self_test():
    class ProgressTests(unittest.TestCase):
        def setUp(self):
            self.temp = tempfile.TemporaryDirectory()
            self.addCleanup(self.temp.cleanup)
            self.root = Path(self.temp.name)
            (self.root / "proof.md").write_text("proof", encoding="utf-8")
            t = dict(id="one", title="one", primary_g="G1", tags=["T01"], requirements=[f"R{i:02}" for i in range(1, 10)],
                     weight=1, required=True, acceptance_criterion="condition", applicability=dict(engineering=True, acceptance=True),
                     phase="S1", depends_on=[], next_action="check",
                     engineering=dict(status="met", evidence=[dict(path="proof.md")]),
                     acceptance=dict(status="unknown", evidence=[]),
                     blockers=[dict(type="verification", reason="pending", release_condition="independent check")])
            self.data = dict(schema_version=1, scope_revision="r1", as_of="2026-10-25", product_base_commit="abc",
                             metric_mix=dict(engineering=.5, acceptance=.5), milestones=[dict(id=f"G{i}", title=f"G{i}") for i in range(1, 9)],
                             tasks=[t], changes=[], history_batches=[], plan_catalog=[], governance={},
                             roadmap=[dict(id=f"S{i}", title="stage", exit_criteria="exit", task_ids=["one"] if i == 1 else []) for i in range(1, 6)])
            self.freeze()

        def freeze(self):
            self.data["scope_fingerprint"] = fingerprint(self.data["tasks"])
            self.data["changes"] = [dict(id="base", date="2026-10-25", type="baseline", scope_revision="r1", reason="baseline",
                                         new_fingerprint=self.data["scope_fingerprint"], new_total_weight=sum(t["weight"] for t in self.data["tasks"]))]

        def test_denominator_bounds_and_mix(self):
            tasks = []
            for state, weight in (("met", 1), ("unmet", 2), ("unknown", 3), ("not_applicable", 3)):
                t = copy.deepcopy(self.data["tasks"][0])
                t["weight"] = weight
                t["engineering"]["status"] = state
                t["acceptance"]["status"] = "met"
                tasks.append(t)
            m = metric(tasks, self.data["metric_mix"])
            self.assertEqual(m["engineering"]["denominator"], 6)
            self.assertAlmostEqual(m["engineering"]["lower"], 100 / 6)
            self.assertAlmostEqual(m["engineering"]["upper"], 400 / 6)
            self.assertAlmostEqual(m["lower"], (100 / 6 + 100) / 2)
            self.assertAlmostEqual(m["upper"], (400 / 6 + 100) / 2)

        def test_valid(self):
            validate(self.data, self.root)

        def test_mix_change_rejected(self):
            self.data["metric_mix"] = dict(engineering=.9, acceptance=.1)
            with self.assertRaisesRegex(ValueError, "各50%"):
                validate(self.data, self.root)

        def test_html_summary_cards_use_shared_metrics(self):
            self.data["scope_revision"] = '<script>scope</script>'
            output = render_html(self.data)
            expected = metric(self.data["tasks"], self.data["metric_mix"])
            for key, value in (("overall-lower", expected["lower"]),
                               ("engineering-lower", expected["engineering"]["lower"]),
                               ("acceptance-lower", expected["acceptance"]["lower"])):
                self.assertRegex(output, rf'data-metric="{key}">.*?<strong>{value:.1f}%</strong>')
            self.assertIn('data-metric="closed-count"><span>闭合条款</span><strong>0/1</strong>', output)
            self.assertIn("待核上界 100.0%", output)
            self.assertIn("剩余 1 条", output)
            self.assertIn("必需条款门尚未通过", output)
            self.assertIn("&lt;script&gt;scope&lt;/script&gt;", output)
            self.assertNotIn("<script>scope</script>", output)
            self.assertIn("<details><summary>", output)
            self.assertNotIn("<details open", output)

        def test_legacy_backlog_is_visible_but_unscored(self):
            before = metric(self.data["tasks"], self.data["metric_mix"])
            before_scope = fingerprint(self.data["tasks"])
            item = dict(id="ISS-OLD", title="旧事项<script>", classification="needs_revalidation",
                        task_ids=["one"], source="proof.md", reason="待独立复核", next_action="核原证据", scored=False)
            self.data["legacy_backlog"] = [item]
            validate(self.data, self.root)
            self.assertEqual(before, metric(self.data["tasks"], self.data["metric_mix"]))
            self.assertEqual(before_scope, fingerprint(self.data["tasks"]))
            for output in (render_markdown(self.data), render_html(self.data)):
                self.assertIn("历史遗留分流（不重复计分；待复核不是剔除V1）", output)
                self.assertIn("旧事项&lt;script&gt;", output)
                self.assertIn("needs_revalidation", output)
                self.assertIn("核原证据", output)
            for key, value in (("scored", True), ("task_ids", ["missing"]),
                               ("classification", "auto_fixed"), ("source", "missing.md"),
                               ("reason", ""), ("next_action", "")):
                old = item[key]
                item[key] = value
                with self.assertRaises(ValueError):
                    validate(self.data, self.root)
                item[key] = old
            self.data["legacy_backlog"].append(copy.deepcopy(item))
            with self.assertRaisesRegex(ValueError, "ID为空或重复"):
                validate(self.data, self.root)

        def test_revision_links_and_locator_text(self):
            repository = "https://github.com/happye/MuyunTradeAssistant"
            revision = "f" * 40
            evidence = dict(path="proof.md", revision=revision, anchor='G1/T01定位<script>')
            self.data["repository_url"] = repository
            self.data["tasks"][0]["engineering"]["evidence"] = [evidence]
            validate(self.data, self.root)
            expected = repository + "/blob/" + revision + "/proof.md"
            for output in (render_markdown(self.data), render_html(self.data)):
                self.assertIn(expected, output)
                self.assertIn("定位：G1/T01定位&lt;script&gt;", output)
                self.assertNotIn(expected + "#", output)
            self.assertEqual(link(dict(path="proof.md"), "../../"), "[proof.md](../../proof.md)")
            evidence["revision"] = "f07"
            with self.assertRaisesRegex(ValueError, "40位"):
                validate(self.data, self.root)

        def test_repository_url_validation(self):
            for value in ("http://github.com/a/b", "https://user:secret@github.com/a/b",
                          "https://github.com/a/b?q=1", "https://github.com/a/b#anchor",
                          "https://bad_host/a/b", "https://github.com:invalid/a/b", "https:///a/b"):
                self.data["repository_url"] = value
                with self.assertRaisesRegex(ValueError, "repository_url"):
                    validate(self.data, self.root)

        def test_engineering_na_with_acceptance_met_can_close(self):
            task = self.data["tasks"][0]
            self.assertFalse(metric([task], self.data["metric_mix"])["closed"])
            task["applicability"]["engineering"] = False
            task["engineering"] = dict(status="not_applicable", evidence=[], reason="data-only criterion")
            task["acceptance"] = dict(status="met", evidence=[dict(path="proof.md")])
            self.freeze()
            result = metric([task], self.data["metric_mix"])
            self.assertIsNone(result["lower"])
            self.assertTrue(result["closed"])
            validate(self.data, self.root)

        def test_required_acceptance_na_rejected(self):
            task = self.data["tasks"][0]
            task["applicability"]["acceptance"] = False
            task["acceptance"] = dict(status="not_applicable", evidence=[], reason="attempt to bypass")
            self.freeze()
            with self.assertRaisesRegex(ValueError, "必需条款验收"):
                validate(self.data, self.root)

        def test_na_cannot_shrink_frozen_scope(self):
            task = self.data["tasks"][0]
            task["engineering"] = dict(status="not_applicable", evidence=[], reason="attempt to shrink")
            with self.assertRaisesRegex(ValueError, "冻结适用性"):
                validate(self.data, self.root)
            task["applicability"]["engineering"] = False
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                validate(self.data, self.root)

        def test_duplicate_rejected(self):
            self.data["tasks"] *= 2
            with self.assertRaisesRegex(ValueError, "重复"):
                validate(self.data, self.root)

        def test_met_requires_evidence(self):
            self.data["tasks"][0]["engineering"]["evidence"] = []
            with self.assertRaisesRegex(ValueError, "证据"):
                validate(self.data, self.root)

        def test_scope_tamper_rejected(self):
            self.data["tasks"][0]["weight"] = 2
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                validate(self.data, self.root)

        def test_cycle_rejected(self):
            self.data["tasks"][0]["depends_on"] = ["one"]
            with self.assertRaisesRegex(ValueError, "环"):
                validate(self.data, self.root)

        def test_escape(self):
            self.data["tasks"][0]["title"] = '<script>alert("x")</script>|'
            rendered = render_html(self.data)
            self.assertNotIn('<script>alert("x")', rendered)
            self.assertIn("&lt;script&gt;", rendered)
            self.assertIn("&#124;", render_markdown(self.data))

        def test_reference_escape(self):
            for path in ("../secret", "C:/secret", "https://example.com", "/etc/passwd"):
                with self.assertRaises(ValueError):
                    reference(self.root, path)

        def test_scope_chain_rejected(self):
            change = copy.deepcopy(self.data["changes"][0])
            change.update(id="change", type="scope_change", old_fingerprint="0" * 64, old_total_weight=1)
            self.data["changes"].append(change)
            with self.assertRaisesRegex(ValueError, "旧范围"):
                validate(self.data, self.root)

        def test_generated_catalog_bootstrap_is_restricted(self):
            self.data["plan_catalog"] = [dict(path=p.as_posix(), role="generated", purpose="view") for p in OUTPUTS]
            with self.assertRaisesRegex(ValueError, "引用文件不存在"):
                validate(self.data, self.root)
            validate(self.data, self.root, allow_generated_missing=True)
            for p in OUTPUTS:
                target = self.root / p
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("generated", encoding="utf-8")
            validate(self.data, self.root)
            self.data["plan_catalog"].append(dict(path="arbitrary.md", role="generated", purpose="unapproved"))
            with self.assertRaisesRegex(ValueError, "引用文件不存在"):
                validate(self.data, self.root, allow_generated_missing=True)
            self.data["plan_catalog"].pop()
            self.data["tasks"][0]["engineering"]["evidence"] = [dict(path="missing-proof.md")]
            with self.assertRaisesRegex(ValueError, "引用文件不存在"):
                validate(self.data, self.root, allow_generated_missing=True)

    return unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ProgressTests)).wasSuccessful()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="校验事实源及已有生成文件是否一致；更新后先--render再--check")
    group.add_argument("--render", action="store_true", help="校验并只重写PROGRESS.md与开发进度看板.html")
    group.add_argument("--self-test", action="store_true", help="运行隔离临时目录内建自测，不写正式生成文件")
    args = parser.parse_args()
    if args.self_test:
        return 0 if self_test() else 1
    try:
        data = validate(json.loads((ROOT / SOURCE).read_text(encoding="utf-8-sig"), object_pairs_hook=no_duplicate_keys),
                        ROOT, allow_generated_missing=args.render)
        rendered = (render_markdown(data), render_html(data))
        for path, content in zip(OUTPUTS, rendered):
            target = ROOT / path
            if args.render:
                target.write_text(content, encoding="utf-8", newline="\n")
            elif target.exists():
                require(target.read_text(encoding="utf-8") == content, f"生成文件过期: {path.as_posix()}；请先--render再--check")
        m = metric(data["tasks"], data["metric_mix"])
        print(f'结构/计算/引用存在性校验通过；{len(data["tasks"])}条，权重{sum(t["weight"] for t in data["tasks"])}；V1合同进度指数 {interval(m["lower"], m["upper"])}；required门 {"已闭合" if m["closed"] else "未闭合"}。')
        print("已生成两个视图。" if args.render else "已有视图已比对；尚未生成的视图可用--render创建。")
        print("此校验不验证证据内容，不代表产品验收或发布通过。")
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"进度校验失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
