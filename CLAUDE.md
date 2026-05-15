# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

WenMai (文脉) is an automated Chinese web novel writing system combining Obsidian as a knowledge base with LLM-based chapter generation. The core insight: LLM context windows are too small for hundreds of chapters, so **generation and storage are decoupled** — chapter text is a "stream" (written then distilled), while entity cards (characters/items/locations/concepts) are the persistent "library" and single source of truth.

## Commands

```bash
cd pipeline

# Install
pip install -r requirements.txt

# Generate an arc outline (30 chapters starting from where you left off)
python main.py plan -d "叶凡离开青云宗前往中州，途中遭遇魔道追杀" -n 30

# Write all chapters in an arc (pauses between chapters)
python main.py write --arc arc_001_030
# Auto-continuous mode
python main.py write --arc arc_001_030 -y
# Force re-generate existing chapters
python main.py write --arc arc_001_030 -f -y
# Custom word count
python main.py write --arc arc_001_030 -w 4000

# Write a single chapter
python main.py write-one -c 5 -o "叶凡突破金丹四层"

# Re-distill a chapter after manual edits
python main.py distill -c 15

# Show project status (chapters, entities, arcs)
python main.py status
```

## Architecture

The pipeline (`pipeline/`) is a linear 6-stage process, each module handling one stage:

1. **reader.py** (`VaultReader`) — Parses Obsidian markdown files (frontmatter + body + `[[wikilinks]]`). All file I/O for reading entities, chapters, summaries, arcs, and the inverted index.
2. **retriever.py** (`EntityRetriever`) — Entity-driven retrieval. Given a chapter outline, extracts mentioned entities from `[[wikilinks]]`, expands one hop via linked entities, and returns full entity cards. This is the precision retrieval alternative to vector RAG.
3. **context_builder.py** (`ContextBuilder`) — Assembles the LLM prompt context: world constraints (all concept cards), arc metadata, entity cards, recent chapter summaries, previous chapter residue, and active plot threads.
4. **generator.py** (`LLMGenerator`) — LLM API abstraction. Supports DeepSeek, Anthropic, OpenAI, and any OpenAI-compatible endpoint. Configured via `config.yaml`.
5. **distiller.py** (`ChapterDistiller`) — Extracts structured info from generated chapters: entity presence/frequency, status changes, new entities, new/revealed plot threads, summary, and key residue for the next chapter.
6. **writer.py** (`VaultWriter`) — Writes generated content back to the Obsidian vault: chapter markdown, summary markdown, entity card updates, new entity creation, inverted index updates, and plot thread additions.

**Prompt templates** live in `pipeline/prompts/` — each has a `_SYSTEM` and `_USER` string that get `.format()`-ed with context.

## Vault data model

The Obsidian vault uses **frontmatter YAML** on every markdown file for structured state. Entity cards are the persistent truth — they accumulate state changes chapter after chapter.

- **entity/person/** — Character cards with fields like `修为`, `身份`, `所在`, `持有`, `状态`
- **entity/item/** — Weapons, artifacts, consumables; fields like `current_holder`, `category`
- **entity/location/** — Sects, cities, realms; fields like `parent_location`, `掌控者/势力`
- **entity/concept/** — Cultivation systems, organizations, world rules; collected as "world constraints"
- **chapter/** — `ch_NNN.md`, generated chapter text
- **summary/** — `ch_NNN_summary.md`, per-chapter summary with entity frequency frontmatter
- **plot/arcs/** — Arc outlines with chapter range, key entities, and per-chapter one-liner tables
- **plot/伏笔池.md** — Plot thread pool with ID-based tracking (埋下/进行中/已回收)
- **index/entity_chapter_index.json** — Inverted index mapping entity names → chapters they appear in

Entity relationships are modeled via Obsidian `[[wikilinks]]` in card bodies and frontmatter fields.

## Key constraints

- The `config.yaml` currently contains a hardcoded API key — **do not commit this file** to any public repository.
- Chapter filenames are zero-padded: `ch_005.md`, `ch_015.md`. The `chapter_count()` method counts files, so gaps in numbering will cause incorrect counts.
- Entity names must match exactly between `[[wikilinks]]`, frontmatter references, and the file system — the retriever does exact-name lookups, not fuzzy matching.
- `httpx` is used directly instead of provider SDKs — all LLM calls go through raw HTTP to OpenAI-compatible or Anthropic endpoints with a 300s timeout.
