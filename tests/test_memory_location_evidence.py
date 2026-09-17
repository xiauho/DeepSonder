"""Regression cases for chapter-memory location evidence, without AI calls."""
from dataclasses import replace
from unittest import TestCase

from core.chapter_facts import FactRecord
from core.chapter_memory import parse_memory_proposal
from test_chapter_memory import make_ledger, base_state, valid_proposal


class LocationEvidenceTests(TestCase):
    def proposal(self, predicate, *, category="character_state", subject="林舟",
                 value="脚下全是血", place=None, destination="山门"):
        ledger = make_ledger()
        fact = FactRecord("fact_location", category, subject, predicate, value,
                          "chapter_01:p0032", "explicit")
        facts = (fact,) if place is None else (fact, place)
        ledger = replace(ledger, facts=facts)
        state = base_state()
        raw = valid_proposal(ledger, state)
        raw["changes"][0]["value"] = destination
        raw["changes"][0]["evidence_fact_ids"] = [f.fact_id for f in facts]
        return parse_memory_proposal(raw, ledger, state, expected_context_hash="context-hash")

    def place(self, predicate="在山门前那片空地上交战", anchor="chapter_01:p0025"):
        return FactRecord("fact_place", "event", "六位长老", predicate,
                          "与敌交战", anchor, "explicit")

    def test_real_failed_proposals_support_local_empty_ground_reference(self):
        for place in (self.place(), self.place("在山门前顶出一块空地", "chapter_01:p0030")):
            with self.subTest(place=place):
                result = self.proposal("冲到空地", place=place)
                self.assertFalse(result.has_blockers)
                self.assertEqual(result.resulting_state["characters"]["林舟"]["location"], "山门")
                self.assertIn("evidence_value_mismatch", {c.kind for c in result.conflicts})

    def test_direct_arrivals_across_fact_categories(self):
        for category in ("location", "event", "character_state"):
            for verb in ("冲到", "跑到", "赶到", "走到", "奔至", "退到", "抵达", "到达"):
                with self.subTest(category=category, verb=verb):
                    self.assertFalse(self.proposal(verb + "山门", category=category).has_blockers)

    def test_compact_destination_matches_deictic_location_description(self):
        for phrase in ("山门前那片空地", "山门前这片空地", "山门前一块空地"):
            with self.subTest(phrase=phrase):
                result = self.proposal("冲到空地", place=self.place("在" + phrase + "上与敌交战"),
                                       destination="山门前空地")
                self.assertFalse(result.has_blockers)
                self.assertEqual(result.resulting_state["characters"]["林舟"]["location"], "山门前空地")
                self.assertIn("evidence_value_mismatch", {c.kind for c in result.conflicts})

    def test_normalization_preserves_direction_and_qualifiers(self):
        for source, destination in (
            ("山门前那片空地", "山门后空地"),
            ("山门东面那片空地", "山门西面空地"),
            ("山门外那片空地", "山门内空地"),
            ("山门前那片空地", "后山前空地"),
            ("山门前那片血色空地", "山门前空地"),
        ):
            with self.subTest(source=source, destination=destination):
                self.assertTrue(self.proposal("冲到空地", place=self.place("在" + source + "交战"),
                                              destination=destination).has_blockers)

    def test_direct_and_split_location_values_use_same_normalization(self):
        self.assertFalse(self.proposal("冲到山门前那片空地", destination="山门前空地").has_blockers)
        self.assertFalse(self.proposal("抵达", value="山门前那片空地",
                                       destination="山门前空地").has_blockers)

    def test_normalization_does_not_bypass_arrival_or_anchor_checks(self):
        for predicate in ("未冲到空地", "准备冲到空地", "看见苏婉冲到空地"):
            self.assertTrue(self.proposal(predicate, place=self.place(), destination="山门前空地").has_blockers)
        self.assertTrue(self.proposal("冲到空地", place=self.place(anchor="chapter_01:p0001"),
                                     destination="山门前空地").has_blockers)

    def test_qualified_destination_and_non_negative_tunmo(self):
        place = FactRecord("fact_place", "event", "青云宗弟子", "三五成群结成小阵抵抗",
                           "在山门前顶出一块空地，但最前排弟子被黑潮吞没，只剩断剑落地",
                           "chapter_01:p0030", "explicit")
        result = self.proposal("冲到空地，脚下全是血，攥着半截古剑",
                               place=place, destination="青云宗山门")
        self.assertFalse(result.has_blockers)
        self.assertIn("evidence_value_mismatch", {c.kind for c in result.conflicts})
        self.assertTrue(self.proposal("冲到空地", place=place, destination="其他宗山门").has_blockers)

    def test_existing_first_chapter_named_group_arrival_remains_supported(self):
        place = FactRecord("fact_place", "location", "祖师雕塑广场",
                           "位于山门内第一层台地", "祖师雕塑广场",
                           "chapter_01:p0032", "explicit")
        result = self.proposal("林舟与苏婉赶到时广场已站了上千人", subject="广场弟子人群",
                               category="location", value="祖师雕塑广场",
                               place=place, destination="祖师雕塑广场")
        self.assertFalse(result.has_blockers)

    def test_negative_planned_and_other_actor_arrivals_block(self):
        for predicate in ("没有冲到山门", "未到达山门", "没能走到山门", "准备冲到山门",
                          "下令弟子冲到山门", "要求林舟赶到山门", "如果冲到山门",
                          "看见苏婉冲到山门", "苏婉冲到山门，林舟留在后方",
                          "林舟看着苏婉冲到山门", "林舟命苏婉冲到山门", "冲到的不是山门",
                          "昨日到达山门", "梦见冲到山门"):
            for category in ("event", "location"):
                with self.subTest(predicate=predicate, category=category):
                    result = self.proposal(predicate, category=category, value="山门", place=self.place())
                    self.assertTrue(result.has_blockers)
                    self.assertEqual(result.resulting_state["characters"]["林舟"]["location"], "旧站")

    def test_other_character_and_unrelated_place_block(self):
        self.assertTrue(self.proposal("冲到山门", subject="苏婉").has_blockers)
        self.assertTrue(self.proposal("冲到后山", place=self.place()).has_blockers)
        self.assertTrue(self.proposal("冲到东面空地", place=self.place("在山门西面空地交战")).has_blockers)
        for anchor in ("chapter_01:p0001", "chapter_02:p0032", "chapter_01:p0040"):
            self.assertTrue(self.proposal("冲到空地", place=self.place(anchor=anchor)).has_blockers)

    def test_diagnostics_name_target_value_and_evidence(self):
        result = self.proposal("未到达山门")
        self.assertIn("characters.林舟.location", result.preview_text())
        self.assertIn("拟写入：山门", result.preview_text())
        self.assertIn("未到达山门", result.conflict_evidence_text())
        self.assertIn("chapter_01:p0032", result.conflict_evidence_text())
