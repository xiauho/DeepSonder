import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from core.character_identity import (bind_state, canonical_name, conflict_options, fingerprint,
    load_registry, project_state, save_identity, remove_identity, REGISTRY)
from core.project import NovelProject
from core.chapter_memory import (MemoryPatch, apply_memory_patches, parse_memory_proposal, canonical_hash)
from core.chapter_facts import FactRecord
from core.character_cards import missing_character_cards
from core.accepted_memory import memory_base_state_for
from core.ai_result_service import AIResultService
from core.task_context import AIContextSnapshot
from tests.test_chapter_memory import make_ledger, valid_proposal


class CharacterIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = NovelProject.create(Path(self.tmp.name) / "novel", "身份测试")
        (self.project.chapters_dir / 'chapter_02.md').write_text('# 揭露\n\n## 正文\n白鸥就是林舟。', encoding='utf-8')
        self.project.save_story_state({'current_chapter': 1, 'characters': {
            '白鸥': {'state': '健康', 'location': '旧站', 'items': ['钥匙']},
            '林舟': {'state': '受伤', 'location': '北港', 'items': ['铜钱']}}})

    def associate(self, **kwargs):
        values = dict(primary='白鸥', names=['白鸥', '林舟'], chapter_id='chapter_02',
                      evidence='白鸥就是林舟，作者确认。',
                      choices={'state': '林舟', 'location': '林舟', 'items': '白鸥'},
                      expected_revision=fingerprint(load_registry(self.project)),
                      expected_state=fingerprint(self.project.load_story_state()))
        values.update(kwargs)
        return save_identity(self.project, **values)

    def test_associate_project_reopen_and_undo_preserve_bytes(self):
        path = self.project.memory_dir / 'story_state.json'
        original = path.read_bytes()
        group = self.associate()
        state = self.project.load_story_state()
        self.assertEqual(set(project_state(state)['characters']), {'白鸥'})
        self.assertEqual(project_state(state)['characters']['白鸥']['state'], '受伤')
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(canonical_name(state, '林舟'), '白鸥')
        remove_identity(self.project, group, fingerprint(load_registry(self.project)))
        self.assertEqual(set(project_state(self.project.load_story_state())['characters']), {'白鸥', '林舟'})
        self.assertEqual(path.read_bytes(), original)

    def test_conflicts_require_choice_and_revision_is_checked(self):
        with self.assertRaisesRegex(ValueError, '冲突字段'):
            self.associate(choices={})
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.associate(expected_state='old')
        self.assertFalse((self.project.memory_dir / 'character_identities.json').exists())

    def test_multiple_identities_preserve_id_and_prevent_overlap(self):
        group = self.associate()
        extended = self.associate(names=['白鸥', '林舟', '无名客'], primary='林舟')
        self.assertEqual(group, extended)
        self.assertEqual(canonical_name(self.project.load_story_state(), '无名客'), '林舟')
        with self.assertRaisesRegex(ValueError, '全部已有名称'):
            self.associate(names=['林舟', '陌客'])

    def test_earlier_chapter_does_not_receive_reveal(self):
        self.associate()
        earlier, _ = memory_base_state_for(self.project, 'chapter_01')
        later, _ = memory_base_state_for(self.project, 'chapter_02')
        self.assertEqual(canonical_name(earlier, '林舟'), '林舟')
        self.assertEqual(canonical_name(later, '林舟'), '白鸥')
        self.assertEqual(bind_state(self.project, {}, 1)[REGISTRY]['groups'], [])

    def patch(self, subject, field, value, before, kind='set_character_field'):
        fact = FactRecord('fact_' + subject, 'character_state' if field != 'location' else 'location',
                          subject, '状态变为' if field != 'location' else '抵达', value, 'chapter_02:p0001', 'explicit')
        patch = MemoryPatch('op_' + subject, kind, subject, field, before, value, (fact.fact_id,), 'explicit')
        return patch, fact

    def test_new_identity_updates_old_identity_and_undo_keeps_provenance(self):
        group = self.associate()
        state = self.project.load_story_state()
        patch, fact = self.patch('林舟', 'state', '昏迷', '受伤')
        result, conflicts = apply_memory_patches(state, (patch,), {fact.fact_id: fact}, 'chapter_02')
        self.assertFalse([c for c in conflicts if c.severity == 'blocker'])
        self.assertEqual(project_state(result)['characters']['白鸥']['state'], '昏迷')
        self.assertEqual(result['characters']['白鸥']['state'], '健康')
        self.project.save_story_state(result)
        remove_identity(self.project, group, fingerprint(load_registry(self.project)))
        restored = project_state(self.project.load_story_state())
        self.assertEqual(restored['characters']['林舟']['state'], '昏迷')
        self.assertEqual(restored['characters']['白鸥']['state'], '健康')

    def test_old_identity_can_update_after_new_identity(self):
        self.associate()
        state = self.project.load_story_state()
        for name, value, before in [('林舟', '昏迷', '受伤'), ('白鸥', '苏醒', '昏迷')]:
            patch, fact = self.patch(name, 'state', value, before)
            state, conflicts = apply_memory_patches(state, (patch,), {fact.fact_id: fact}, 'chapter_02')
            self.assertFalse([c for c in conflicts if c.severity == 'blocker'])
        self.assertEqual(project_state(state)['characters']['白鸥']['state'], '苏醒')

    def test_alias_collision_is_blocked_but_unrelated_person_is_not_merged(self):
        self.associate()
        p1, f1 = self.patch('林舟', 'state', '昏迷', '受伤')
        p2, f2 = self.patch('白鸥', 'state', '苏醒', '受伤')
        _, conflicts = apply_memory_patches(self.project.load_story_state(), (p1, p2), {f1.fact_id:f1, f2.fact_id:f2}, 'chapter_02')
        self.assertTrue(any(c.kind == 'patch_collision' for c in conflicts))
        self.assertEqual(canonical_name(self.project.load_story_state(), '白鸽'), '白鸽')

    def test_new_identity_location_still_requires_its_own_evidence(self):
        self.associate()
        patch, fact = self.patch('林舟', 'location', '西塔', '北港')
        state, conflicts = apply_memory_patches(self.project.load_story_state(), (patch,), {fact.fact_id:fact}, 'chapter_02')
        self.assertFalse([c for c in conflicts if c.severity == 'blocker'])
        self.assertEqual(project_state(state)['characters']['白鸥']['location'], '西塔')
        false_fact = replace(fact, subject='路人')
        _, conflicts = apply_memory_patches(self.project.load_story_state(), (patch,), {fact.fact_id:false_fact}, 'chapter_02')
        self.assertTrue(any(c.severity == 'blocker' for c in conflicts))

    def test_alias_card_retrieval_and_duplicate_prevention(self):
        self.associate()
        card = self.project.canon_dir / 'characters' / '白鸥.md'
        card.write_text('# 白鸥\n作者角色资料', encoding='utf-8')
        self.assertIn('作者角色资料', self.project.find_related_canon('chapter_02', character_query='林舟').characters)
        self.assertNotIn('作者角色资料', self.project.find_related_canon('chapter_01', character_query='林舟').characters)
        self.assertEqual(missing_character_cards(self.project, self.project.load_story_state()), [])
        card.unlink()
        self.assertEqual([c.name for c in missing_character_cards(self.project, self.project.load_story_state())], ['白鸥'])

    def test_identity_changes_invalidate_pending_memory_and_task_context(self):
        from core.text_chunking import chapter_content_hash
        state = self.project.load_story_state()
        ledger = make_ledger(chapter_content_hash(self.project.load_chapter('chapter_01').content))
        proposal = parse_memory_proposal(valid_proposal(ledger, state), ledger, state, expected_context_hash='context-hash')
        snapshot = AIContextSnapshot.capture(self.project, 'chapter_01', None)
        self.associate()
        with self.assertRaisesRegex(ValueError, '状态.*变化'):
            AIResultService.commit_memory_proposal(self.project, 'chapter_01', proposal)
        self.assertNotEqual(snapshot.context_hash, AIContextSnapshot.capture(self.project, 'chapter_01', None).context_hash)

    def test_graph_projects_names_with_stable_id(self):
        from application.relationship_graph_service import RelationshipGraphService
        group = self.associate()
        graph = RelationshipGraphService().snapshot(self.project)
        self.assertEqual(len(graph.nodes), 1)
        self.assertEqual(graph.nodes[0].node_id, group)
        self.assertIn('林舟', graph.nodes[0].aliases)


    def test_card_sync_uses_new_identity_and_rejects_changed_mapping(self):
        from tests.test_character_card_sync import CharacterCardSyncTests
        from core.accepted_memory import load_accepted_memory, save_accepted_memory, memory_hash
        from core.character_card_sync import build_character_card_sync_proposal, apply_character_card_sync, CharacterCardSyncError
        project, card = CharacterCardSyncTests()._project_with_memories(Path(self.tmp.name))
        records = load_accepted_memory(project)
        endpoint = records['chapter_02']
        endpoint['state']['characters']['守塔人'] = endpoint['state']['characters'].pop('林夜')
        for fact in endpoint['evidence_facts']:
            fact['subject'] = str(fact.get('subject', '')).replace('林夜', '守塔人')
            fact['anchor'] = str(fact.get('anchor', '')).replace('林夜', '守塔人')
        endpoint.pop('record_hash')
        endpoint['record_hash'] = memory_hash(endpoint)
        save_accepted_memory(project, records)
        before = (project.memory_dir/'accepted_chapter_memory.json').read_bytes()
        group = save_identity(project, primary='林夜', names=['林夜', '守塔人'], chapter_id='chapter_02',
            evidence='作者确认守塔人是林夜', choices={}, expected_revision=fingerprint(load_registry(project)),
            expected_state=fingerprint(project.load_story_state()))
        proposal = build_character_card_sync_proposal(project, card, ('chapter_01','chapter_02'))
        self.assertEqual(proposal.character_name, '林夜')
        self.assertEqual(next(p.value for p in proposal.patches if p.field == 'location'), '北塔')
        remove_identity(project, group, fingerprint(load_registry(project)))
        with self.assertRaisesRegex(CharacterCardSyncError, '身份.*变化'):
            apply_character_card_sync(project, proposal, ['location'])
        self.assertEqual((project.memory_dir/'accepted_chapter_memory.json').read_bytes(), before)

    def test_item_removal_uses_combined_state_and_does_not_resurrect_items(self):
        self.associate()
        state = self.project.load_story_state()
        fact = FactRecord('item', 'item', '林舟', '丢失', '钥匙', 'chapter_02:p0001', 'explicit')
        patch = MemoryPatch('remove', 'remove_character_item', '林舟', '', True, '钥匙', ('item',), 'explicit')
        state, conflicts = apply_memory_patches(state, (patch,), {'item':fact}, 'chapter_02')
        self.assertFalse([c for c in conflicts if c.severity == 'blocker'])
        self.assertEqual(project_state(state)['characters']['白鸥']['items'], [])
        self.assertEqual(state['characters']['白鸥']['items'], ['钥匙'])

    def test_registry_survives_reopen_and_original_context_recovers_after_undo(self):
        group = self.associate()
        card = self.project.canon_dir / 'characters' / '白鸥.md'
        card.write_text('# 白鸥\n身份档案', encoding='utf-8')
        self.assertIn('身份档案', self.project.find_related_canon('chapter_02', character_query='林舟').characters)
        other = NovelProject(self.project.root)
        self.assertEqual(load_registry(other)['groups'][0]['id'], group)
        remove_identity(other, group, fingerprint(load_registry(other)))
        self.assertNotIn('身份档案', self.project.find_related_canon('chapter_02', character_query='林舟').characters)


if __name__ == '__main__':
    unittest.main()
