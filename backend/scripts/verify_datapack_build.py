#!/usr/bin/env python3
"""端到端验收脚本：按需求逐条验证批量构建、冲突裁决、失败重试、快照冻结与复用。

仅使用标准库，运行：python scripts/verify_datapack_build.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")

FIXED_MEMBERS = [
    {"source_field": "member_id", "target_field": "member_count", "agg_rule": "count_distinct"},
    {"source_field": "register_channel", "target_field": "register_channel", "agg_rule": "direct"},
    {"source_field": "register_date", "target_field": "register_date", "agg_rule": "direct"},
]

checks: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    checks.append((name, condition, detail))
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and not condition else ""))


def call(method: str, path: str, body: object | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def main() -> int:
    # 数据源目录
    _, payload = call("GET", "/api/datapack/builds/sources")
    source_names = sorted(item["name"] for item in payload["items"])
    check("数据源目录含 orders/stores/members", source_names == ["members", "orders", "stores"], str(source_names))

    # 1) 三源整组提交（members 默认映射含非法规则）
    _, build = call("POST", "/api/datapack/builds", {
        "name": "批量样例",
        "sources": [{"source": s} for s in ["orders", "stores", "members"]],
    })
    results = {r["source"]: r for r in build["source_results"]}
    check("orders 处理成功", results["orders"]["status"] == "success")
    check("stores 处理成功", results["stores"]["status"] == "success")
    check("members 校验失败并单独列出", results["members"]["status"] == "failed"
          and any("按日汇总求和" in e and "members" in e for e in results["members"]["errors"]))
    check("失败源隔离：members 不进输出结构",
          all(f["source"] != "members" for f in build["output_fields"]))
    check("orders.amount 按日汇总进入输出结构",
          any(f["source_field"] == "amount" and f["agg_rule"] == "sum_daily" for f in build["output_fields"]))
    check("members 提供注册渠道（待重试通过后生效）",
          any(m["target_field"] == "register_channel" for m in results["members"]["mappings"]))

    # 2) store_level 冲突项
    level = [c for c in build["conflicts"] if c["target_field"] == "store_level"]
    check("store_level 两个聚合写法给出冲突项", len(level) == 1
          and {level[0]["winner"]["agg_rule"], level[0]["loser"]["agg_rule"]} == {"direct", "lookup"})
    check("冲突裁决唯一口径：direct 生效",
          len([f for f in build["output_fields"] if f["target_field"] == "store_level"]) == 1
          and next(f for f in build["output_fields"] if f["target_field"] == "store_level")["agg_rule"] == "direct")

    # 3) 有失败源时发布被拦
    code, _ = call("POST", f"/api/datapack/builds/{build['id']}/publish")
    check("失败源未修复时禁止发布（400）", code == 400)

    # 4) 失败项单独重新提交
    _, retried = call("POST", f"/api/datapack/builds/{build['id']}/retry",
                      {"source": "members", "mappings": FIXED_MEMBERS})
    check("members 重试通过，整组 all_passed", retried["all_passed"] and retried["failed_count"] == 0)
    check("重试后 members 进入输出结构",
          any(f["source"] == "members" for f in retried["output_fields"]))
    code, _ = call("POST", f"/api/datapack/builds/{build['id']}/retry",
                   {"source": "orders", "mappings": []})
    check("成功源不允许重试（仅失败项可重试，400）", code == 400)

    # 5) 发布冻结
    _, published = call("POST", f"/api/datapack/builds/{build['id']}/publish")
    snap = published["snapshot"]
    check("发布后状态为已发布且快照 frozen", published["status"] == "已发布" and snap["frozen"] is True)
    check("快照含三个源的完整字段映射",
          {g["source"] for g in snap["field_mappings"]} == {"orders", "stores", "members"})
    check("快照保留 store_level 冲突记录",
          any(c["target_field"] == "store_level" for c in snap["conflicts"]))
    code, _ = call("POST", f"/api/datapack/builds/{build['id']}/retry",
                   {"source": "members", "mappings": FIXED_MEMBERS})
    check("冻结后禁止重试（400）", code == 400)

    # 6) 接口配置侧读取同一份快照
    _, api_snaps = call("GET", "/api/apigen/datapack-snapshots")
    check("接口配置侧读到同一份快照",
          api_snaps["total"] == 1 and api_snaps["items"][0]["snapshot_id"] == snap["snapshot_id"])
    code, one = call("GET", f"/api/apigen/datapack-snapshots/{snap['snapshot_id']}")
    check("单快照接口可读且版本一致", code == 200 and one["version"] == published["version"])

    # 7) 同组数据源重跑复用，不产生第二份版本
    _, rerun = call("POST", "/api/datapack/builds", {
        "sources": [
            {"source": "orders"},
            {"source": "stores"},
            {"source": "members", "mappings": FIXED_MEMBERS},
        ],
    })
    check("同组重跑命中复用（reused=true 且同一构建 id）",
          rerun["reused"] is True and rerun["id"] == build["id"])
    _, builds = call("GET", "/api/datapack/builds")
    _, snaps_after = call("GET", "/api/apigen/datapack-snapshots")
    check("重跑后构建与快照均未增加", builds["total"] == 1 and snaps_after["total"] == 1)

    # 8) 单独重跑 orders 是另一组，不影响三源组版本
    _, orders_only = call("POST", "/api/datapack/builds", {"sources": [{"source": "orders"}]})
    check("单独重跑 orders 产生独立构建", orders_only["reused"] is False and orders_only["id"] != build["id"])
    _, snaps_final = call("GET", "/api/apigen/datapack-snapshots")
    three_source = [s for s in snaps_final["items"] if s["source_names"] == ["orders", "stores", "members"]]
    check("三源组快照始终只有一份", len(three_source) == 1)

    failed = [name for name, ok, _ in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} 项通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
