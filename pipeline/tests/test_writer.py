import json

from writer import VaultWriter, _write_frontmatter_md


class TestWriteFrontmatterMd:
    def test_creates_file_with_frontmatter(self, tmp_path):
        md_path = tmp_path / "test.md"
        _write_frontmatter_md(md_path, {"title": "测试", "tags": ["a", "b"]}, "正文内容")
        assert md_path.exists()
        content = md_path.read_text("utf-8")
        assert "---" in content
        assert "title: 测试" in content
        assert "tags:" in content
        assert "正文内容" in content

    def test_strips_none_and_empty_values(self, tmp_path):
        md_path = tmp_path / "test.md"
        _write_frontmatter_md(
            md_path,
            {"keep": "val", "drop_none": None, "drop_empty": "", "drop_empty_list": []},
            "body",
        )
        content = md_path.read_text("utf-8")
        assert "keep: val" in content
        assert "drop_none" not in content


class TestVaultWriter:
    def test_init(self, tmp_vault):
        writer = VaultWriter(str(tmp_vault))
        assert writer.entity_dir.name == "entity"

    def test_write_chapter(self, tmp_vault):
        writer = VaultWriter(str(tmp_vault))
        chapter_path = writer.write_chapter(1, "测试标题", "第一章正文内容")
        assert chapter_path.exists()
        content = chapter_path.read_text("utf-8")
        assert "第一章正文内容" in content
        assert "测试标题" in content

    def test_write_chapter_zero_padded(self, tmp_vault):
        writer = VaultWriter(str(tmp_vault))
        path = writer.write_chapter(5, "title", "content")
        assert path.name == "ch_005.md"

    def test_write_summary(self, tmp_vault):
        writer = VaultWriter(str(tmp_vault))
        path = writer.write_summary(1, {"word_count": 500}, "摘要内容")
        assert path.name == "ch_001_summary.md"
        assert "摘要内容" in path.read_text("utf-8")

    def test_update_entity(self, tmp_vault):
        writer = VaultWriter(str(tmp_vault))
        writer.update_entity("person", "叶凡", {"修为": "筑基巅峰", "身份": "外门弟子"}, chapter=1)
        path = tmp_vault / "entity" / "person" / "叶凡.md"
        assert path.exists()
        content = path.read_text("utf-8")
        assert "修为:" in content

    def test_create_entity(self, tmp_vault):
        writer = VaultWriter(str(tmp_vault))
        writer.create_entity("person", "叶凡", "主角简介")
        path = tmp_vault / "entity" / "person" / "叶凡.md"
        assert path.exists()
        content = path.read_text("utf-8")
        assert "叶凡" in content
        assert "主角简介" in content

    def test_update_entity_index(self, tmp_vault):
        writer = VaultWriter(str(tmp_vault))
        writer.update_entity_index({"叶凡": [1, 5], "寒蝉石": [5]})
        index_path = tmp_vault / "index" / "entity_chapter_index.json"
        assert index_path.exists()
        data = json.loads(index_path.read_text("utf-8"))
        assert "叶凡" in data["entities"]
        assert "寒蝉石" in data["entities"]
        assert data["entities"]["叶凡"]["chapters"] == [1, 5]

    def test_update_entity_index_accumulates(self, tmp_vault):
        writer = VaultWriter(str(tmp_vault))
        writer.update_entity_index({"叶凡": [1]})
        writer.update_entity_index({"叶凡": [3, 1], "林霜": [3]})
        index_path = tmp_vault / "index" / "entity_chapter_index.json"
        data = json.loads(index_path.read_text("utf-8"))
        assert data["entities"]["叶凡"]["chapters"] == [1, 3]
        assert data["entities"]["林霜"]["chapters"] == [3]
