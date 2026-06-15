import pytest
from distiller import DistillResult


class TestDistillResult:
    def test_getitem_existing_key(self):
        r = DistillResult({"summary": "章节摘要", "entities": []})
        assert r["summary"] == "章节摘要"

    def test_getitem_missing_key_raises_keyerror(self):
        r = DistillResult({"summary": "章节摘要"})
        with pytest.raises(KeyError):
            _ = r["nonexistent"]

    def test_get_method_default(self):
        r = DistillResult({"summary": "test"})
        assert r.get("nonexistent") is None
        assert r.get("nonexistent", "default") == "default"

    def test_bool_false_when_empty(self):
        r = DistillResult({})
        assert not r

    def test_bool_true_when_has_data(self):
        r = DistillResult({"summary": "test"})
        assert r

    def test_degraded_false_by_default(self):
        r = DistillResult({"summary": "test"})
        assert r.degraded is False

    def test_degraded_true_when_set(self):
        r = DistillResult({}, degraded=True)
        assert r.degraded is True
