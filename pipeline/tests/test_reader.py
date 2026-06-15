from reader import VaultReader, _combine_links, parse_links


class TestParseLinks:
    def test_simple_link(self):
        text = "[[叶凡]]是主角"
        names = parse_links(text)
        assert names == ["叶凡"]

    def test_multiple_links(self):
        text = "[[叶凡]]在[[青云宗]]修行，手持[[寒蝉石]]"
        names = parse_links(text)
        assert names == ["叶凡", "青云宗", "寒蝉石"]

    def test_link_with_alias(self):
        text = "[[叶凡|主角]]登场"
        names = parse_links(text)
        assert names == ["叶凡"]

    def test_link_with_heading(self):
        text = "参见[[青云宗#历史]]部分"
        names = parse_links(text)
        assert names == ["青云宗"]

    def test_no_links(self):
        text = "这是一段没有链接的普通文本。"
        names = parse_links(text)
        assert names == []

    def test_non_entity_prefix_filtered(self):
        text = "[[plot:伏笔1]]已回收，[[叶凡]]出现"
        names = parse_links(text)
        assert names == ["叶凡"]


class TestCombineLinks:
    def test_from_body_and_list_fields(self):
        body = "[[叶凡]]来到[[青云宗]]"
        meta = {"角色": ["叶凡", "[[林霜]]"], "位置": "[[青云宗]]"}
        names = _combine_links(body, meta)
        assert "叶凡" in names
        assert "林霜" in names
        assert "青云宗" in names

    def test_from_nested_dict_metadata(self):
        body = "正文"
        meta = {"关系": [{"name": "[[叶凡]]", "relation": "师徒"}, {"name": "[[林霜]]"}]}
        names = _combine_links(body, meta)
        assert "叶凡" in names
        assert "林霜" in names


class TestVaultReader:
    def test_init(self, tmp_vault):
        reader = VaultReader(str(tmp_vault))
        assert reader.entity_dir.name == "entity"
        assert reader.chapter_dir.name == "chapter"

    def test_read_entity(self, vault_with_entity):
        reader = VaultReader(str(vault_with_entity))
        result = reader.read_entity("person", "叶凡")
        assert result is not None
        meta, body = result
        assert meta["修为"] == "筑基巅峰"
        assert "叶凡" in body

    def test_read_nonexistent_entity(self, tmp_vault):
        reader = VaultReader(str(tmp_vault))
        result = reader.read_entity("person", "不存在")
        assert result is None

    def test_read_main_plot(self, vault_with_main_plot):
        reader = VaultReader(str(vault_with_main_plot))
        result = reader.read_main_plot()
        assert result is not None
        meta, body = result
        assert meta["title"] == "测试主线"
        assert "叶凡" in body
        assert "寒蝉石" in body

    def test_read_main_plot_nonexistent(self, tmp_vault):
        reader = VaultReader(str(tmp_vault))
        result = reader.read_main_plot()
        assert result is None
