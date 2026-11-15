"""数据模型封装批量构建的规则测试（仅用标准库 unittest）。

覆盖需求口径：
- 多原始数据源整组校验、逐条结果、失败源隔离；
- 字段映射先校验目标字段再校验聚合规则；
- 同一输出字段多个聚合写法的冲突裁决与冲突项；
- 失败项单独重新提交；
- 发布冻结快照、接口配置侧读取同一份快照；
- 同组数据源重复构建复用原快照，不产生第二份版本。
"""
from __future__ import annotations

import copy
import unittest

from app.services.datapack_build import (
    AGG_RULES,
    SOURCE_CATALOG,
    SOURCE_FAILED,
    SOURCE_OK,
    DatapackBuildService,
)


def default_sources(names: list[str]) -> list[dict]:
    return [{"source": name} for name in names]


class BuildServiceTestBase(unittest.TestCase):
    def setUp(self) -> None:
        # 每个用例独立服务，避免状态串扰。
        self.service = DatapackBuildService()

    def _fixed_members(self) -> list[dict]:
        mappings = copy.deepcopy(SOURCE_CATALOG["members"]["default_mappings"])
        mappings = [m for m in mappings if m["agg_rule"] != "sum_daily"]
        mappings.append({
            "source_field": "register_date",
            "target_field": "register_date",
            "agg_rule": "direct",
        })
        return mappings


class CatalogTests(BuildServiceTestBase):
    def test_catalog_contains_three_batch_sources(self) -> None:
        names = {item["name"] for item in self.service.list_sources()}
        self.assertEqual(names, {"orders", "stores", "members"})
        orders = self.service.get_source("orders")
        amount_mapping = next(
            m for m in orders["default_mappings"] if m["source_field"] == "amount"
        )
        # orders 的 amount 按日汇总。
        self.assertEqual(amount_mapping["agg_rule"], "sum_daily")
        self.assertEqual(amount_mapping["target_field"], "daily_amount")

    def test_stores_seed_contains_store_level_conflict(self) -> None:
        stores = SOURCE_CATALOG["stores"]
        level_writes = [
            m for m in stores["default_mappings"] if m["target_field"] == "store_level"
        ]
        self.assertEqual(len(level_writes), 2)
        self.assertEqual(len({m["agg_rule"] for m in level_writes}), 2)


class BatchValidationTests(BuildServiceTestBase):
    def test_batch_validates_every_source_and_isolates_failed_one(self) -> None:
        build = self.service.submit_build({
            "name": "三源批量",
            "sources": default_sources(["orders", "stores", "members"]),
        })
        results = {r["source"]: r for r in build["source_results"]}
        self.assertEqual(results["orders"]["status"], SOURCE_OK)
        self.assertEqual(results["stores"]["status"], SOURCE_OK)
        self.assertEqual(results["members"]["status"], SOURCE_FAILED)
        self.assertEqual(build["success_count"], 2)
        self.assertEqual(build["failed_count"], 1)
        self.assertFalse(build["all_passed"])
        self.assertEqual(build["status"], "待发布")
        # 失败源的错误逐条可读，且点明目标字段与聚合规则。
        self.assertTrue(
            any("register_date" in err and "按日汇总求和" in err for err in results["members"]["errors"])
        )
        # 失败源被隔离：其字段不进入输出结构。
        output_sources = {f["source"] for f in build["output_fields"]}
        self.assertNotIn("members", output_sources)
        # 通过源（orders、stores）的模型封装仍可进入待发布。
        self.assertIn("orders", output_sources)
        self.assertIn("stores", output_sources)

    def test_target_field_validated_before_agg_rule(self) -> None:
        build = self.service.submit_build({"sources": [{
            "source": "orders",
            "mappings": [{"source_field": "amount", "target_field": "", "agg_rule": ""}],
        }]})
        result = build["source_results"][0]
        self.assertEqual(result["status"], SOURCE_FAILED)
        errors = " ".join(result["mappings"][0]["errors"])
        self.assertIn("缺少目标字段", errors)
        self.assertIn("缺少聚合规则", errors)

    def test_unsupported_rule_and_missing_source_field_are_rejected(self) -> None:
        build = self.service.submit_build({"sources": [{
            "source": "members",
            "mappings": [
                {"source_field": "register_date", "target_field": "reg_date", "agg_rule": "sum_daily"},
                {"source_field": "not_exists", "target_field": "ghost", "agg_rule": "direct"},
            ],
        }]})
        result = build["source_results"][0]
        self.assertEqual(result["status"], SOURCE_FAILED)
        joined = " ".join(result["errors"])
        self.assertIn("不适用于数据源「members」", joined)
        self.assertIn("来源字段「not_exists」不存在", joined)

    def test_unknown_source_is_failed_item_not_crash(self) -> None:
        build = self.service.submit_build({"sources": default_sources(["ghost_db"])})
        self.assertEqual(build["source_results"][0]["status"], SOURCE_FAILED)
        self.assertIn("不在数据源目录中", build["source_results"][0]["errors"][0])

    def test_empty_submission_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.submit_build({"sources": []})


class ConflictAdjudicationTests(BuildServiceTestBase):
    def test_store_level_conflict_is_listed_and_unique_winner_wins(self) -> None:
        build = self.service.submit_build({
            "sources": default_sources(["orders", "stores"]),
        })
        level_conflicts = [c for c in build["conflicts"] if c["target_field"] == "store_level"]
        self.assertEqual(len(level_conflicts), 1)
        conflict = level_conflicts[0]
        # 两个聚合写法同时写入 store_level：direct 与 lookup。
        self.assertEqual(conflict["winner"]["agg_rule"], "direct")
        self.assertEqual(conflict["loser"]["agg_rule"], "lookup")
        self.assertEqual(conflict["winner"]["source"], "stores")
        self.assertEqual(conflict["loser"]["source"], "stores")
        # 最终输出结构中 store_level 只保留唯一口径。
        level_fields = [f for f in build["output_fields"] if f["target_field"] == "store_level"]
        self.assertEqual(len(level_fields), 1)
        self.assertEqual(level_fields[0]["agg_rule"], "direct")

    def test_cross_source_conflict_uses_submission_order(self) -> None:
        # orders 在前，stores 的 store_id 写法应判负。
        build = self.service.submit_build({
            "sources": default_sources(["orders", "stores"]),
        })
        id_conflict = next(c for c in build["conflicts"] if c["target_field"] == "store_id")
        self.assertEqual(id_conflict["winner"]["source"], "orders")
        self.assertEqual(id_conflict["loser"]["source"], "stores")
        # 冲突不阻断构建：两源均成功、构建进入待发布。
        self.assertTrue(build["all_passed"])
        self.assertEqual(build["status"], "待发布")

    def test_orders_amount_daily_sum_survives_in_output(self) -> None:
        build = self.service.submit_build({
            "sources": default_sources(["orders", "stores", "members"]),
        })
        amount_field = next(f for f in build["output_fields"] if f["target_field"] == "daily_amount")
        self.assertEqual(amount_field["source"], "orders")
        self.assertEqual(amount_field["agg_rule"], "sum_daily")
        self.assertEqual(amount_field["agg_rule_label"], AGG_RULES["sum_daily"])


class RetryAndPublishTests(BuildServiceTestBase):
    def _build_with_failed_members(self) -> dict:
        return self.service.submit_build({
            "sources": default_sources(["orders", "stores", "members"]),
        })

    def test_publish_blocked_while_failed_source_exists(self) -> None:
        build = self._build_with_failed_members()
        with self.assertRaises(ValueError) as ctx:
            self.service.publish(build["id"])
        self.assertIn("members", str(ctx.exception))

    def test_retry_single_failed_source_then_publish_freezes_snapshot(self) -> None:
        build = self._build_with_failed_members()
        retried = self.service.retry_source(build["id"], {
            "source": "members",
            "mappings": self._fixed_members(),
        })
        self.assertTrue(retried["all_passed"])
        self.assertEqual(retried["failed_count"], 0)
        # 重试通过后 members 才进入输出结构。
        self.assertIn("members", {f["source"] for f in retried["output_fields"]})
        self.assertIsNotNone(retried["fingerprint"])

        published = self.service.publish(build["id"])
        self.assertEqual(published["status"], "已发布")
        snapshot = published["snapshot"]
        self.assertTrue(snapshot["frozen"])
        self.assertEqual(snapshot["fingerprint"], published["fingerprint"])
        # 快照包含每个源的完整字段映射与冲突记录。
        snapshot_sources = {item["source"] for item in snapshot["field_mappings"]}
        self.assertEqual(snapshot_sources, {"orders", "stores", "members"})
        self.assertTrue(
            any(c["target_field"] == "store_level" for c in snapshot["conflicts"])
        )

    def test_frozen_snapshot_blocks_retry_and_discard(self) -> None:
        build = self._build_with_failed_members()
        self.service.retry_source(build["id"], {
            "source": "members",
            "mappings": self._fixed_members(),
        })
        self.service.publish(build["id"])
        with self.assertRaises(ValueError):
            self.service.retry_source(build["id"], {
                "source": "members",
                "mappings": self._fixed_members(),
            })
        with self.assertRaises(ValueError):
            self.service.discard(build["id"])

    def test_discard_allowed_only_before_publish(self) -> None:
        build = self._build_with_failed_members()
        discarded = self.service.discard(build["id"])
        self.assertEqual(discarded["status"], "已废弃")
        with self.assertRaises(ValueError):
            self.service.publish(build["id"])

    def test_retry_unknown_source_of_build_rejected(self) -> None:
        build = self._build_with_failed_members()
        with self.assertRaises(ValueError):
            self.service.retry_source(build["id"], {"source": "orders", "mappings": []})
        with self.assertRaises(KeyError):
            self.service.retry_source(999, {"source": "members"})


class SnapshotReuseTests(BuildServiceTestBase):
    def _publish_full_group(self) -> dict:
        build = self.service.submit_build({
            "sources": default_sources(["orders", "stores", "members"]),
        })
        self.service.retry_source(build["id"], {
            "source": "members",
            "mappings": self._fixed_members(),
        })
        return self.service.publish(build["id"])

    def test_same_group_rerun_reuses_snapshot_no_second_version(self) -> None:
        first = self._publish_full_group()
        total_builds_before = len(self.service.list_builds())
        snapshots_before = len(self.service.list_snapshots())

        # 用完全相同的一组数据源与映射口径重新构建。
        rerun = self.service.submit_build({"sources": [
            {"source": "orders"},
            {"source": "stores"},
            {"source": "members", "mappings": self._fixed_members()},
        ]})
        self.assertTrue(rerun["reused"])
        self.assertEqual(rerun["id"], first["id"])
        self.assertEqual(len(self.service.list_builds()), total_builds_before)
        self.assertEqual(len(self.service.list_snapshots()), snapshots_before)

    def test_apigen_reads_the_same_frozen_snapshot(self) -> None:
        published = self._publish_full_group()
        # 接口配置侧（apigen）与封装侧读取同一份快照对象。
        api_snapshots = self.service.list_snapshots()
        self.assertEqual(len(api_snapshots), 1)
        self.assertEqual(api_snapshots[0]["snapshot_id"], published["snapshot"]["snapshot_id"])
        one = self.service.get_snapshot(published["snapshot"]["snapshot_id"])
        self.assertEqual(one["version"], published["version"])
        self.assertTrue(one["frozen"])
        self.assertIsNone(self.service.get_snapshot("SNAP-not-exists"))

    def test_orders_only_rerun_is_distinct_group_but_no_extra_snapshot_for_full_group(self) -> None:
        # 场景：三源整组已发布；之后单独重跑 orders 是另一组数据源，
        # 它不会给三源整组多出第二份版本；三源快照始终只有一份。
        full = self._publish_full_group()
        orders_only = self.service.submit_build({"sources": default_sources(["orders"])})
        self.assertFalse(orders_only["reused"])
        self.assertNotEqual(orders_only["id"], full["id"])
        full_snapshots = [
            s for s in self.service.list_snapshots() if s["fingerprint"] == full["fingerprint"]
        ]
        self.assertEqual(len(full_snapshots), 1)

    def test_snapshot_is_deep_frozen_from_later_builds(self) -> None:
        first = self._publish_full_group()
        snapshot_id = first["snapshot"]["snapshot_id"]
        # 再构建一组不同口径的数据源并发布，老快照内容不受影响。
        other = self.service.submit_build({"sources": default_sources(["orders"])})
        self.service.publish(other["id"])
        frozen = self.service.get_snapshot(snapshot_id)
        self.assertEqual(
            {item["source"] for item in frozen["field_mappings"]},
            {"orders", "stores", "members"},
        )
        self.assertEqual(len(self.service.list_snapshots()), 2)

    def test_discarded_build_is_not_resurrected_by_fingerprint_reuse(self) -> None:
        # 一组全部通过的构建被废弃后，同口径重新提交应产生新构建而不是复用废弃记录。
        build = self.service.submit_build({"sources": default_sources(["orders", "stores"])})
        self.assertTrue(build["all_passed"])
        fingerprint = build["fingerprint"]
        self.service.discard(build["id"])

        rerun = self.service.submit_build({"sources": default_sources(["orders", "stores"])})
        self.assertFalse(rerun["reused"])
        self.assertNotEqual(rerun["id"], build["id"])
        self.assertEqual(rerun["status"], "待发布")
        self.assertEqual(rerun["fingerprint"], fingerprint)

    def test_fingerprint_consistent_between_submit_and_retry_paths(self) -> None:
        # 提交路径与重试路径计算出的指纹必须一致，重跑才能命中同一份快照。
        build = self.service.submit_build({
            "sources": default_sources(["orders", "stores", "members"]),
        })
        retried = self.service.retry_source(build["id"], {
            "source": "members",
            "mappings": self._fixed_members(),
        })
        self.assertIsNotNone(retried["fingerprint"])
        rerun = self.service.submit_build({"sources": [
            {"source": "orders"},
            {"source": "stores"},
            {"source": "members", "mappings": self._fixed_members()},
        ]})
        self.assertTrue(rerun["reused"])
        self.assertEqual(rerun["fingerprint"], retried["fingerprint"])


class RouterRegistrationTests(unittest.TestCase):
    def test_routes_registered(self) -> None:
        # 不依赖 httpx：递归遍历 FastAPI 0.163 的嵌套 include_router 结构。
        from app.main import app

        def collect_paths(router: object) -> set[str]:
            found: set[str] = set()
            for route in getattr(router, "routes", []):
                path = getattr(route, "path", None)
                if path:
                    found.add(path)
                nested = getattr(route, "original_router", None)
                if nested is not None:
                    found |= collect_paths(nested)
            return found

        paths = collect_paths(app)
        for path in [
            "/api/datapack/builds/sources",
            "/api/datapack/builds",
            "/api/datapack/builds/{build_id}",
            "/api/datapack/builds/{build_id}/retry",
            "/api/datapack/builds/{build_id}/publish",
            "/api/datapack/builds/{build_id}/discard",
            "/api/apigen/datapack-snapshots",
            "/api/apigen/datapack-snapshots/{snapshot_id}",
        ]:
            self.assertIn(path, paths)


if __name__ == "__main__":
    unittest.main()
