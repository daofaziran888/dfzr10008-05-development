"""数据模型封装：多原始数据源批量构建与字段映射快照。

业务口径（与需求逐条对应）：

1. 一次批量构建可以多选多个原始数据源，整组校验、逐条给出每个源的处理结果；
2. 每条字段映射先校验目标字段与聚合规则：目标字段不能为空、聚合规则必须在
   该数据源支持的规则清单内、来源字段必须真实存在；
3. 同一个输出字段被多个聚合规则写入时按数据源顺序裁决唯一口径：排序靠前者生效，
   其余规则记为冲突项（conflicts），冲突不会让构建失败，但必须显式列出；
4. 校验失败的源单独列入 source_results（status=failed），其余源仍然产出输出结构，
   构建整体进入「待发布」，失败项支持单独重新提交；
5. 发布时冻结字段映射快照（深拷贝，之后任何重试/废弃都不影响已发布内容），
   接口配置侧（apigen）读取的是同一份快照对象；
6. 同一组数据源（含完全相同的映射口径）重复构建时按指纹复用，不再产生第二份版本。
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from threading import Lock
from typing import Any

# 支持的聚合规则全集；每个数据源再声明自己实际支持的子集。
AGG_RULES: dict[str, str] = {
    "direct": "直接映射",
    "sum_daily": "按日汇总求和",
    "count_daily": "按日计数",
    "avg": "平均值",
    "count_distinct": "去重计数",
    "lookup": "维度关联取值",
}

# 原始数据源目录：样例数据只用于展示与校验，真实项目里会换成元数据库。
SOURCE_CATALOG: dict[str, dict[str, Any]] = {
    "orders": {
        "name": "orders",
        "title": "订单流水",
        "description": "门店每日订单明细，amount 为订单金额。",
        "fields": [
            {"name": "order_id", "type": "string", "label": "订单编号"},
            {"name": "store_id", "type": "string", "label": "门店编号"},
            {"name": "amount", "type": "decimal", "label": "订单金额"},
            {"name": "order_date", "type": "date", "label": "下单日期"},
            {"name": "member_id", "type": "string", "label": "会员编号"},
        ],
        "sample_rows": [
            {"order_id": "O1001", "store_id": "S01", "amount": 128.0, "order_date": "2026-11-13", "member_id": "M001"},
            {"order_id": "O1002", "store_id": "S01", "amount": 72.5, "order_date": "2026-11-13", "member_id": "M002"},
            {"order_id": "O1003", "store_id": "S02", "amount": 256.0, "order_date": "2026-11-14", "member_id": "M003"},
        ],
        "supported_rules": ["direct", "sum_daily", "count_daily", "avg", "count_distinct"],
        "default_mappings": [
            {"source_field": "order_date", "target_field": "stat_date", "agg_rule": "direct"},
            {"source_field": "store_id", "target_field": "store_id", "agg_rule": "direct"},
            {"source_field": "amount", "target_field": "daily_amount", "agg_rule": "sum_daily"},
            {"source_field": "order_id", "target_field": "order_count", "agg_rule": "count_daily"},
        ],
    },
    "stores": {
        "name": "stores",
        "title": "门店主数据",
        "description": "门店等级与基础属性，给封装结果提供门店维度。",
        "fields": [
            {"name": "store_id", "type": "string", "label": "门店编号"},
            {"name": "store_name", "type": "string", "label": "门店名称"},
            {"name": "store_level", "type": "string", "label": "门店等级"},
            {"name": "city", "type": "string", "label": "所在城市"},
        ],
        "sample_rows": [
            {"store_id": "S01", "store_name": "杭州西湖店", "store_level": "A", "city": "杭州"},
            {"store_id": "S02", "store_name": "上海静安店", "store_level": "B", "city": "上海"},
        ],
        "supported_rules": ["direct", "lookup", "count_distinct"],
        "default_mappings": [
            {"source_field": "store_id", "target_field": "store_id", "agg_rule": "direct"},
            {"source_field": "store_name", "target_field": "store_name", "agg_rule": "direct"},
            # 同一输出字段 store_level 出现两条聚合写法，用于验证冲突裁决。
            {"source_field": "store_level", "target_field": "store_level", "agg_rule": "direct"},
            {"source_field": "city", "target_field": "store_level", "agg_rule": "lookup"},
        ],
    },
    "members": {
        "name": "members",
        "title": "会员注册数据",
        "description": "会员注册渠道与来源，给封装结果补充会员维度。",
        "fields": [
            {"name": "member_id", "type": "string", "label": "会员编号"},
            {"name": "register_channel", "type": "string", "label": "注册渠道"},
            {"name": "register_date", "type": "date", "label": "注册日期"},
            {"name": "city", "type": "string", "label": "所在城市"},
        ],
        "sample_rows": [
            {"member_id": "M001", "register_channel": "app", "register_date": "2026-10-01", "city": "杭州"},
            {"member_id": "M002", "register_channel": "mini_program", "register_date": "2026-10-05", "city": "杭州"},
            {"member_id": "M003", "register_channel": "offline", "register_date": "2026-10-11", "city": "上海"},
        ],
        "supported_rules": ["direct", "lookup", "count_distinct"],
        "default_mappings": [
            {"source_field": "member_id", "target_field": "member_count", "agg_rule": "count_distinct"},
            {"source_field": "register_channel", "target_field": "register_channel", "agg_rule": "direct"},
            # 该规则不在 members 支持的规则清单内，首提时必然校验失败，
            # 用来演示「失败源单独列出、修复后单独重新提交」。
            {"source_field": "register_date", "target_field": "register_date", "agg_rule": "sum_daily"},
        ],
    },
}

BUILD_STATUS_PENDING = "待发布"
BUILD_STATUS_PUBLISHED = "已发布"
BUILD_STATUS_DISCARDED = "已废弃"

SOURCE_OK = "success"
SOURCE_FAILED = "failed"


def _fingerprint(source_set: list[dict[str, Any]]) -> str:
    """按数据源集合与其最终映射口径生成稳定指纹，作为快照复用依据。"""
    canonical = [
        {
            "source": item["source"],
            "mappings": [
                [str(m.get("source_field") or ""), str(m.get("target_field") or ""), str(m.get("agg_rule") or "")]
                for m in item.get("mappings", [])
            ],
        }
        for item in source_set
    ]
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class DatapackBuildService:
    """批量构建、冲突裁决、发布冻结与快照复用的全部状态都收在这一个服务里。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._builds: list[dict[str, Any]] = []
        self._seq = 0
        self._version_seq = 0
        # 指纹 -> 已冻结快照；同一组数据源永远复用同一份快照对象。
        self._snapshot_by_fingerprint: dict[str, dict[str, Any]] = {}

    # ---------- 数据源目录 ----------
    def list_sources(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(SOURCE_CATALOG[name]) for name in sorted(SOURCE_CATALOG)]

    def get_source(self, source_name: str) -> dict[str, Any] | None:
        source = SOURCE_CATALOG.get(source_name)
        return copy.deepcopy(source) if source else None

    # ---------- 单源校验 ----------
    def _validate_source(self, source_name: str, mappings: list[dict[str, Any]]) -> dict[str, Any]:
        """校验一个数据源的全部字段映射，返回该源的逐条处理结果。"""
        catalog = SOURCE_CATALOG.get(source_name)
        if catalog is None:
            return {
                "source": source_name,
                "status": SOURCE_FAILED,
                "errors": [f"原始数据源「{source_name}」不在数据源目录中"],
                "mappings": [],
                "resolved_fields": [],
            }

        field_names = {field["name"] for field in catalog["fields"]}
        supported = set(catalog["supported_rules"])
        mapping_results: list[dict[str, Any]] = []
        errors: list[str] = []
        seen_targets: set[str] = set()

        if not mappings:
            errors.append(f"数据源「{source_name}」未提供任何字段映射，至少需要一条映射")

        for index, mapping in enumerate(mappings, start=1):
            source_field = str(mapping.get("source_field") or "").strip()
            target_field = str(mapping.get("target_field") or "").strip()
            agg_rule = str(mapping.get("agg_rule") or "").strip()
            item_errors: list[str] = []

            # 先校验目标字段，再校验聚合规则，顺序与需求描述保持一致。
            if not target_field:
                item_errors.append(f"第 {index} 条映射缺少目标字段")
            if not agg_rule:
                item_errors.append(f"目标字段「{target_field or '未命名'}」缺少聚合规则")
            elif agg_rule not in AGG_RULES:
                item_errors.append(f"目标字段「{target_field or '未命名'}」使用了未知聚合规则「{agg_rule}」")
            elif agg_rule not in supported:
                item_errors.append(
                    f"目标字段「{target_field or '未命名'}」的聚合规则「{AGG_RULES[agg_rule]}」"
                    f"不适用于数据源「{source_name}」"
                )
            if source_field and source_field not in field_names:
                item_errors.append(f"来源字段「{source_field}」不存在于数据源「{source_name}」")
            elif not source_field:
                item_errors.append(f"第 {index} 条映射缺少来源字段")

            result = {
                "source_field": source_field,
                "target_field": target_field,
                "agg_rule": agg_rule,
                "agg_rule_label": AGG_RULES.get(agg_rule, agg_rule),
                "valid": not item_errors,
                "errors": item_errors,
                "duplicate_target": bool(target_field) and target_field in seen_targets,
            }
            if target_field:
                seen_targets.add(target_field)
            if item_errors:
                errors.extend(item_errors)
            mapping_results.append(result)

        return {
            "source": source_name,
            "source_title": catalog["title"],
            "status": SOURCE_FAILED if errors else SOURCE_OK,
            "errors": errors,
            "mappings": mapping_results,
            # 仅校验通过的映射才有资格参与后续冲突裁决。
            "resolved_fields": [
                {
                    "source": source_name,
                    "source_field": item["source_field"],
                    "target_field": item["target_field"],
                    "agg_rule": item["agg_rule"],
                    "agg_rule_label": item["agg_rule_label"],
                }
                for item in mapping_results
                if item["valid"]
            ],
        }

    # ---------- 冲突裁决 ----------
    @staticmethod
    def _canonical_set(source_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """从逐源校验结果提取归一化映射集合，作为指纹计算的统一输入。"""
        return [
            {
                "source": result["source"],
                "mappings": [
                    {
                        "source_field": field["source_field"],
                        "target_field": field["target_field"],
                        "agg_rule": field["agg_rule"],
                    }
                    for field in result["resolved_fields"]
                ],
            }
            for result in source_results
        ]

    @staticmethod
    def _adjudicate(source_results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """同一输出字段被多个聚合规则写入时裁决唯一口径。

        按数据源提交顺序、映射在源内的顺序，首个写法生效，后续写法记为冲突项。
        """
        winner_by_target: dict[str, dict[str, Any]] = {}
        conflicts: list[dict[str, Any]] = []
        for result in source_results:
            for field in result["resolved_fields"]:
                target = field["target_field"]
                if target not in winner_by_target:
                    winner_by_target[target] = field
                    continue
                winner = winner_by_target[target]
                conflicts.append({
                    "target_field": target,
                    "winner": winner,
                    "loser": field,
                    "reason": (
                        f"输出字段「{target}」同时被「{winner['source']}.{winner['source_field']}」"
                        f"（{winner['agg_rule_label']}）与「{field['source']}.{field['source_field']}」"
                        f"（{field['agg_rule_label']}）写入，按数据源顺序裁决前者为唯一口径"
                    ),
                })
        output_fields = [
            {
                "target_field": target,
                "source": winner["source"],
                "source_field": winner["source_field"],
                "agg_rule": winner["agg_rule"],
                "agg_rule_label": winner["agg_rule_label"],
            }
            for target, winner in winner_by_target.items()
        ]
        return output_fields, conflicts

    # ---------- 批量构建 ----------
    def submit_build(self, payload: dict[str, Any]) -> dict[str, Any]:
        sources = payload.get("sources") or []
        if not isinstance(sources, list) or not sources:
            raise ValueError("请至少选择一个原始数据源")

        # 同一批次内数据源去重，保留首次提交顺序。
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in sources:
            name = str((item or {}).get("source") or "").strip()
            if not name:
                raise ValueError("存在未指定名称的原始数据源")
            if name in seen:
                continue
            seen.add(name)
            mappings = item.get("mappings")
            if not mappings:
                catalog = SOURCE_CATALOG.get(name)
                mappings = copy.deepcopy(catalog["default_mappings"]) if catalog else []
            deduped.append({"source": name, "mappings": mappings})

        with self._lock:
            source_results = [
                self._validate_source(item["source"], item["mappings"]) for item in deduped
            ]
            # 失败源单独列出，不参与输出结构与冲突裁决；重试通过后再纳入。
            passed_results = [r for r in source_results if r["status"] == SOURCE_OK]
            output_fields, conflicts = self._adjudicate(passed_results)
            failed = [r for r in source_results if r["status"] == SOURCE_FAILED]
            success = [r for r in source_results if r["status"] == SOURCE_OK]
            all_passed = not failed

            # 指纹基于校验通过后的归一化映射，与重试路径保持同一口径。
            fingerprint = _fingerprint(self._canonical_set(passed_results)) if all_passed else None

            # 同组数据源 + 相同映射口径重复构建：直接复用原构建/快照，不新增版本；
            # 已废弃构建不参与复用，避免把废弃记录「复活」。
            if fingerprint:
                for existing in self._builds:
                    if existing.get("fingerprint") == fingerprint and existing["status"] != BUILD_STATUS_DISCARDED:
                        reused = copy.deepcopy(existing)
                        reused["reused"] = True
                        return reused

            self._seq += 1
            build_id = self._seq
            build = {
                "id": build_id,
                "build_no": f"BLD-{build_id:04d}",
                "name": str(payload.get("name") or "").strip() or f"批量构建 {build_id}",
                "source_names": [item["source"] for item in deduped],
                "source_results": source_results,
                "output_fields": output_fields,
                "conflicts": conflicts,
                "status": BUILD_STATUS_PENDING,
                "all_passed": all_passed,
                "success_count": len(success),
                "failed_count": len(failed),
                "fingerprint": fingerprint,
                "version": None,
                "snapshot": None,
                "published_at": None,
                "reused": False,
            }
            self._builds.append(build)
            return copy.deepcopy(build)

    # ---------- 失败项单独重新提交 ----------
    def retry_source(self, build_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        source_name = str(payload.get("source") or "").strip()
        if not source_name:
            raise ValueError("请指定要重新提交的原始数据源")
        mappings = payload.get("mappings")
        with self._lock:
            build = self._find(build_id)
            if build is None:
                raise KeyError(f"构建记录 {build_id} 不存在")
            if build["status"] != BUILD_STATUS_PENDING:
                raise ValueError(f"构建「{build['build_no']}」已{build['status']}，映射快照已冻结，不能再重试")
            if source_name not in build["source_names"]:
                raise ValueError(f"数据源「{source_name}」不属于构建「{build['build_no']}」的数据源组")
            current = next(r for r in build["source_results"] if r["source"] == source_name)
            # 只有失败项允许单独重新提交；通过项的口径已经参与裁决，不能在待发布构建里偷改。
            if current["status"] != SOURCE_FAILED:
                raise ValueError(
                    f"数据源「{source_name}」当前处理结果为成功，仅失败项支持单独重新提交"
                )
            if not mappings:
                catalog = SOURCE_CATALOG.get(source_name)
                mappings = copy.deepcopy(catalog["default_mappings"]) if catalog else []

            revalidated = self._validate_source(source_name, mappings)
            results = build["source_results"]
            for index, result in enumerate(results):
                if result["source"] == source_name:
                    results[index] = revalidated
                    break

            # 失败源保持隔离：只有通过源参与输出结构与冲突裁决。
            passed_results = [r for r in results if r["status"] == SOURCE_OK]
            output_fields, conflicts = self._adjudicate(passed_results)
            failed = [r for r in results if r["status"] == SOURCE_FAILED]
            build["output_fields"] = output_fields
            build["conflicts"] = conflicts
            build["all_passed"] = not failed
            build["success_count"] = len(results) - len(failed)
            build["failed_count"] = len(failed)

            # 全部通过后才生成指纹；与提交路径共用同一份归一化口径。
            if build["all_passed"]:
                build["fingerprint"] = _fingerprint(self._canonical_set(results))
            return copy.deepcopy(build)

    # ---------- 发布：冻结快照 ----------
    def publish(self, build_id: int) -> dict[str, Any]:
        with self._lock:
            build = self._find(build_id)
            if build is None:
                raise KeyError(f"构建记录 {build_id} 不存在")
            if build["status"] == BUILD_STATUS_PUBLISHED:
                return copy.deepcopy(build)
            if build["status"] == BUILD_STATUS_DISCARDED:
                raise ValueError(f"构建「{build['build_no']}」已废弃，不能发布")
            if not build["all_passed"]:
                failed_names = "、".join(
                    result["source"] for result in build["source_results"] if result["status"] == SOURCE_FAILED
                )
                raise ValueError(f"仍有失败数据源（{failed_names}），请先逐条重新提交通过后再发布")

            fingerprint = build["fingerprint"]
            assert fingerprint is not None
            snapshot = self._snapshot_by_fingerprint.get(fingerprint)
            if snapshot is None:
                self._version_seq += 1
                published_at = datetime.now().isoformat(timespec="seconds")
                snapshot = {
                    "snapshot_id": f"SNAP-{fingerprint}",
                    "fingerprint": fingerprint,
                    "version": f"v1.{self._version_seq}.0",
                    "source_names": list(build["source_names"]),
                    "output_fields": copy.deepcopy(build["output_fields"]),
                    "field_mappings": [
                        {
                            "source": result["source"],
                            "source_title": result.get("source_title"),
                            "mappings": copy.deepcopy(result["mappings"]),
                        }
                        for result in build["source_results"]
                    ],
                    "conflicts": copy.deepcopy(build["conflicts"]),
                    "frozen": True,
                    "published_at": published_at,
                }
                self._snapshot_by_fingerprint[fingerprint] = snapshot

            # 发布动作冻结：构建内保存同一份快照对象的引用，接口配置侧也读它。
            build["snapshot"] = snapshot
            build["version"] = snapshot["version"]
            build["status"] = BUILD_STATUS_PUBLISHED
            build["published_at"] = snapshot["published_at"]
            return copy.deepcopy(build)

    def discard(self, build_id: int) -> dict[str, Any]:
        with self._lock:
            build = self._find(build_id)
            if build is None:
                raise KeyError(f"构建记录 {build_id} 不存在")
            if build["status"] == BUILD_STATUS_PUBLISHED:
                raise ValueError("已发布构建的快照已冻结，不允许废弃")
            build["status"] = BUILD_STATUS_DISCARDED
            return copy.deepcopy(build)

    # ---------- 查询 ----------
    def list_builds(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._builds)

    def get_build(self, build_id: int) -> dict[str, Any] | None:
        with self._lock:
            build = self._find(build_id)
            return copy.deepcopy(build) if build else None

    def list_snapshots(self) -> list[dict[str, Any]]:
        """接口配置侧读取：只暴露已发布且冻结的快照。"""
        with self._lock:
            return copy.deepcopy(list(self._snapshot_by_fingerprint.values()))

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._lock:
            snapshot = next(
                (item for item in self._snapshot_by_fingerprint.values() if item["snapshot_id"] == snapshot_id),
                None,
            )
            return copy.deepcopy(snapshot) if snapshot else None

    def _find(self, build_id: int) -> dict[str, Any] | None:
        return next((item for item in self._builds if item["id"] == build_id), None)


# 单例：路由层与 apigen 共享同一个服务实例，保证读到同一份快照。
build_service = DatapackBuildService()
