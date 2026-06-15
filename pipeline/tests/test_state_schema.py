import json

from state_schema import (
    EntityFact,
    EntityState,
    NovelSchema,
    OverridePolicy,
    StateDelta,
    ValidationSeverity,
    apply_delta_to_state,
    load_entity_state,
    save_entity_state,
)


class TestEntityFact:
    def test_basic_attrs(self):
        fact = EntityFact(
            predicate="修为",
            object="筑基巅峰",
            since_chapter=1,
            source="ch_001.md",
        )
        assert fact.predicate == "修为"
        assert fact.object == "筑基巅峰"
        assert fact.since_chapter == 1
        assert fact.source == "ch_001.md"
        assert fact.until_chapter is None
        assert fact.evidence == ""

    def test_to_dict(self):
        fact = EntityFact(
            predicate="修为",
            object="筑基巅峰",
            since_chapter=1,
            source="ch_001.md",
        )
        d = fact.to_dict()
        assert d["predicate"] == "修为"
        assert d["object"] == "筑基巅峰"
        assert d["since_chapter"] == 1
        assert "until_chapter" not in d

    def test_to_dict_with_until(self):
        fact = EntityFact(
            predicate="修为", object="筑基巅峰",
            since_chapter=1, until_chapter=5, source="ch_005.md",
        )
        d = fact.to_dict()
        assert d["until_chapter"] == 5

    def test_roundtrip_via_json(self):
        fact = EntityFact(
            predicate="本命法宝", object="寒蝉剑", since_chapter=5,
            source="ch_005.md", until_chapter=None,
        )
        d = fact.to_dict()
        data = json.loads(json.dumps(d))
        f2 = EntityFact(
            predicate=data["predicate"],
            object=data["object"],
            since_chapter=data["since_chapter"],
            source=data.get("source", ""),
        )
        assert f2.predicate == fact.predicate
        assert f2.object == fact.object
        assert f2.since_chapter == fact.since_chapter


class TestStateDelta:
    def test_empty_delta(self):
        delta = StateDelta(entity="叶凡", entity_type="person", chapter=5)
        assert delta.entity == "叶凡"
        assert delta.entity_type == "person"
        assert delta.chapter == 5
        assert len(delta.facts_added) == 0
        assert len(delta.facts_retired) == 0

    def test_with_facts(self):
        delta = StateDelta(entity="叶凡", entity_type="person", chapter=10)
        delta.facts_added.append(
            EntityFact(predicate="修为", object="金丹", since_chapter=10, source="ch_010.md")
        )
        assert len(delta.facts_added) == 1


class TestNovelSchema:
    def test_from_dict(self, sample_novel_schema):
        schema = NovelSchema.from_dict(sample_novel_schema)
        assert schema.novel == "测试小说"
        assert "person" in schema.entity_schemas
        assert "item" in schema.entity_schemas

    def test_get_predicates(self, sample_novel_schema):
        schema = NovelSchema.from_dict(sample_novel_schema)
        preds = schema.get_predicates("person")
        assert "修为" in preds
        assert "身份" in preds

    def test_get_allowed_values(self, sample_novel_schema):
        schema = NovelSchema.from_dict(sample_novel_schema)
        vals = schema.get_allowed_values("person", "修为")
        assert "筑基" in vals
        assert "金丹" in vals

    def test_get_override_policy(self, sample_novel_schema):
        schema = NovelSchema.from_dict(sample_novel_schema)
        policy = schema.get_override_policy("person", "本命法宝")
        assert policy == OverridePolicy.APPEND_ONLY

    def test_validate_fact_passes(self, sample_novel_schema):
        schema = NovelSchema.from_dict(sample_novel_schema)
        fact = EntityFact(predicate="修为", object="筑基", since_chapter=1)
        errors = schema.validate_fact(fact, "person")
        assert errors == []

    def test_validate_fact_enum_violation(self, sample_novel_schema):
        schema = NovelSchema.from_dict(sample_novel_schema)
        fact = EntityFact(predicate="修为", object="不可境界", since_chapter=1)
        errors = schema.validate_fact(fact, "person")
        assert len(errors) >= 1
        assert errors[0][1] == ValidationSeverity.WARN

    def test_validate_fact_fatal(self, sample_novel_schema):
        schema = NovelSchema.from_dict(sample_novel_schema)
        fact = EntityFact(predicate="", object="test", since_chapter=1)
        errors = schema.validate_fact(fact, "person")
        assert len(errors) >= 1
        assert errors[0][1] == ValidationSeverity.FATAL

    def test_validate_delta_unknown_entity_is_warn(self, sample_novel_schema):
        schema = NovelSchema.from_dict(sample_novel_schema)
        delta = StateDelta(entity="未知角色", entity_type="person", chapter=1)
        errors = schema.validate_delta(delta, {"叶凡"})
        assert len(errors) == 1
        assert errors[0][1] == ValidationSeverity.WARN

    def test_load_from_file(self, schema_json):
        schema = NovelSchema.load(schema_json.parent)
        assert schema is not None
        assert schema.novel == "测试小说"

    def test_load_nonexistent(self, tmp_vault):
        schema = NovelSchema.load(tmp_vault)
        assert schema is None

    def test_default(self):
        schema = NovelSchema.default()
        assert schema.novel == "(default)"
        assert "person" in schema.entity_schemas


class TestApplyDelta:
    def test_add_new_facts(self):
        state = EntityState(entity="叶凡", entity_type="person")
        delta = StateDelta(entity="叶凡", entity_type="person", chapter=1)
        delta.facts_added.extend([
            EntityFact(predicate="修为", object="筑基", since_chapter=1, source="ch_001.md"),
            EntityFact(predicate="身份", object="外门弟子", since_chapter=1, source="ch_001.md"),
        ])
        result = apply_delta_to_state(state, delta)
        assert result is not state
        assert result.entity == "叶凡"
        assert result.last_updated_chapter == 1
        assert len(result.facts) == 2

    def test_change_existing_fact_retires_old(self):
        state = EntityState(entity="叶凡", entity_type="person", last_updated_chapter=1)
        state.facts = [
            EntityFact(predicate="修为", object="筑基", since_chapter=1, source="ch_001.md"),
        ]
        delta = StateDelta(entity="叶凡", entity_type="person", chapter=5)
        delta.facts_added.append(
            EntityFact(predicate="修为", object="金丹", since_chapter=5, source="ch_005.md")
        )
        result = apply_delta_to_state(state, delta)
        assert len(result.facts) == 2
        old_fact = [f for f in result.facts if f.object == "筑基"][0]
        assert old_fact.until_chapter == 5
        active = result.get_fact("修为")
        assert active is not None
        assert active.object == "金丹"


class TestSaveLoadEntityState:
    def test_save_and_load(self, tmp_vault):
        state = EntityState(entity="叶凡", entity_type="person")
        state.facts = [
            EntityFact(predicate="修为", object="筑基", since_chapter=1, source="ch_001.md"),
        ]
        state_path = tmp_vault / "state" / "person" / "叶凡.state.json"
        save_entity_state(state, state_path)

        loaded = load_entity_state(state_path)
        assert loaded is not None
        assert loaded.entity == "叶凡"
        assert len(loaded.facts) == 1
        assert loaded.facts[0].predicate == "修为"
        assert loaded.facts[0].object == "筑基"

    def test_load_nonexistent(self, tmp_vault):
        state_path = tmp_vault / "state" / "person" / "不存在.state.json"
        loaded = load_entity_state(state_path)
        assert loaded is None
