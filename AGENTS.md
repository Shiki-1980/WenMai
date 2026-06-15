# AGENTS.md

This file provides guidance to OpenCode when working with code in this repository.

## Project overview

WenMai (文脉) is an automated Chinese web novel writing system combining Obsidian as a knowledge base with LLM-based chapter generation. The core insight: LLM context windows are too small for hundreds of chapters, so **generation and storage are decoupled** — chapter text is a "stream" (written then distilled), while entity cards (characters/items/locations/concepts) are the persistent "library" and single source of truth.

## Commands

### Web UI (推荐)

```bash
# Start the web interface for visual novel management
cd web/backend && pip install -r requirements.txt && python server.py &
cd web/frontend && npm install && npm run dev
# Open http://localhost:5173
```

### CLI

```bash
cd pipeline

# Install
pip install -r requirements.txt

# Generate an arc outline (30 chapters starting from where you left off)
python main.py plan -d "叶凡离开青云宗前往中州，途中遭遇魔道追杀" -n 30

# Write all chapters in an arc
python main.py write --arc arc_001_030
python main.py write --arc arc_001_030 -y      # auto-continuous
python main.py write --arc arc_001_030 -f -y   # force re-generate
python main.py write --arc arc_001_030 -w 4000 # custom word count

# Write a single chapter
python main.py write-one -c 5 -o "叶凡突破金丹四层"

# Re-distill a chapter after manual edits
python main.py distill -c 15

# Show project status (chapters, entities, arcs)
python main.py status

# Audit and revise generated content
python main.py audit                              # show project summary
python main.py audit -r "主角性格太弱，加强"        # revise with LLM
python main.py audit -t world -r "添加一个魔法公会"  # revise specific target

# Rollback a chapter's state changes
python main.py rollback 5                         # rollback chapter 5
python main.py rollback 5 --force                 # skip confirmation
```

### Development

```bash
# Run tests
python -m pytest pipeline/tests/ -v

# Lint
ruff check pipeline/

# Type check (loose mode)
mypy pipeline/
```

## Architecture

### Code organization

The pipeline is split into modules:

- **`main.py`** — CLI entrypoint: args + dispatch only (~170 lines)
- **`config_helper.py`** — Shared config loading, path resolution
- **`commands/`** — One file per CLI command:
  - `plan.py` — Arc outline generation + entity card creation
  - `init.py` — Full novel initialization pipeline
  - `write.py` — Incremental chapter writing (`write`, `write-one`)
  - `distill.py` — Chapter re-distillation after manual edits
  - `enrich.py` — Entity card enrichment from accumulated context
  - `audit.py` — LLM-based content audit and revision
  - `status.py` — Project health dashboard
  - `worldbuild.py` — World setting generation from main plot
  - `rollback.py` — Revert chapter state changes
  - `manage.py` — List, switch, rename novels; schema/index operations
  - `_utils.py` — Shared helpers: post-write commit, entity stub creation, auto-enrich

### Core pipeline modules

1. **reader.py** (`VaultReader`) — Parses Obsidian markdown files (frontmatter + body + `[[wikilinks]]`)
2. **retriever.py** (`EntityRetriever`) — Entity-driven retrieval, exact-name `[[wikilink]]` matching
3. **context_builder.py** (`ContextBuilder`) — Assembles LLM prompt context
4. **generator.py** (`LLMGenerator`) — LLM API abstraction (DeepSeek, Anthropic, OpenAI)
5. **distiller.py** (`ChapterDistiller`) — 3-stage distillation: Observer → Settler → State Validator
6. **writer.py** (`VaultWriter`) — Writes content back to vault, uses atomic writes

Supporting modules:
- **state_schema.py** — `EntityState`, `EntityFact`, `StateDelta`, `NovelSchema`, `ValidationSeverity`
- **md_renderer.py** — Renders entity state into Obsidian markdown cards
- **entity_index.py** — Trie-based entity name matching
- **commit_store.py** — Immutable delta commits (`ChapterCommit`) with rollback support
- **schema_gen.py** — LLM generates `novel_schema.json`
- **tools.py** — Agent tool definitions for LLM function calling

## Vault data model

- **entity/person/** — Character cards (修为, 身份, 所在, 持有, 状态)
- **entity/item/** — Weapons, artifacts (品阶, 持有者)
- **entity/location/** — Sects, cities, realms (parent_location, 掌控者/势力)
- **entity/concept/** — Cultivation systems, organizations, world rules
- **chapter/** — `ch_NNN.md` (zero-padded: `ch_005.md`)
- **summary/** — `ch_NNN_summary.md` with entity frequency frontmatter
- **plot/arcs/** — Arc outlines with chapter range, key entities
- **plot/伏笔池.md** — Plot thread pool (埋下/进行中/已回收)
- **index/entity_chapter_index.json** — Inverted index: entity → chapters
- **index/entity_list.json** — Master entity list from wikilink parsing
- **state/** — Per-entity `state.json` with `[{predicate, object, since_chapter, source}]`

Entity relationships are modeled via Obsidian `[[wikilinks]]`.

## Key constraints

- `pipeline/config.yaml` is gitignored — contains API keys. Never commit it.
- Chapter filenames are zero-padded: `ch_005.md`. Gaps break `chapter_count()`.
- Entity names must match exactly between `[[wikilinks]]`, frontmatter, and filesystem. No fuzzy matching.
- `httpx` is used directly instead of provider SDKs with a 300s timeout.
- `init` is idempotent: skips existing files unless `--force`.
- World/plot prompts require `[[wikilink]]` wrapping for all entities.
- `config.yaml` uses `ruamel.yaml` for round-trip write to preserve comments.
- Validator uses severity levels: WARN (recorded), ERROR (triggers retry), FATAL (state-degraded).
- `NovelSchema` supports `ignore_patterns` for skipping temp entities like "出租车".
- `commit_store` supports `rollback_commit()` to reverse entity state changes.

## Project overview

WenMai (文脉) is an automated Chinese web novel writing system combining Obsidian as a knowledge base with LLM-based chapter generation. The core insight: LLM context windows are too small for hundreds of chapters, so **generation and storage are decoupled** — chapter text is a "stream" (written then distilled), while entity cards (characters/items/locations/concepts) are the persistent "library" and single source of truth.

## Commands

### Web UI (推荐)

```bash
# Start the web interface for visual novel management
cd web/backend && pip install -r requirements.txt && python server.py &
cd web/frontend && npm install && npm run dev
# Open http://localhost:5173
```

### CLI

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

# Audit and revise generated content (worldview, plot, entities, outlines)
python main.py audit                          # show project summary
python main.py audit -r "主角性格太弱，加强"    # revise with LLM
python main.py audit -t world -r "添加一个魔法公会"  # revise specific target
```

## Architecture

The pipeline (`pipeline/`) is a linear 6-stage process, each module handling one stage:

1. **reader.py** (`VaultReader`) — Parses Obsidian markdown files (frontmatter + body + `[[wikilinks]]`). All file I/O for reading entities, chapters, summaries, arcs, and the inverted index.
2. **retriever.py** (`EntityRetriever`) — Entity-driven retrieval. Given a chapter outline, extracts mentioned entities from `[[wikilinks]]`, expands one hop via linked entities, and returns full entity cards. This is the precision retrieval alternative to vector RAG.
3. **context_builder.py** (`ContextBuilder`) — Assembles the LLM prompt context: world constraints (all concept cards), arc metadata, entity cards, recent chapter summaries, previous chapter residue, and active plot threads.
4. **generator.py** (`LLMGenerator`) — LLM API abstraction. Supports DeepSeek, Anthropic, OpenAI, and any OpenAI-compatible endpoint. Configured via `config.yaml`.
5. **distiller.py** (`ChapterDistiller`) — Extracts structured info from generated chapters: entity presence/frequency, status changes, new entities, new/revealed plot threads, summary, and key residue for the next chapter.
6. **writer.py** (`VaultWriter`) — Writes generated content back to the Obsidian vault: chapter markdown, summary markdown, entity card updates, new entity creation, inverted index updates, and plot thread additions.

Supporting modules:
- **state_schema.py** — Typed entity state model (`EntityState`, `EntityFact`) with `save_entity_state`/`load_entity_state`. Each entity has a `state.json` that accumulates structured facts across chapters.
- **md_renderer.py** (`MarkdownRenderer`) — Renders entity state + novel schema into Obsidian markdown cards with YAML frontmatter.
- **entity_index.py** — Alias-based entity name resolution and inverted chapter index.
- **commit_store.py** — Immutable delta commits for entity state changes, enabling rollback.
- **schema_gen.py** — Generates `novel_schema.json` defining the structured fields per entity type.
- **tools.py** — Agent tool definitions for LLM tool-calling during distillation/generation.

**State architecture:** Entities exist in two layers — structured `state.json` (machine-readable, accumulates facts chapter by chapter) and rendered markdown cards (human-readable in Obsidian). The markdown is derived from state.json at render time, not edited directly by the pipeline.

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
- **index/entity_list.json** — Master entity list extracted from wikilink parsing during init (name, type, importance, source file, source context)
- **state/** — Per-entity `state.json` files with structured, machine-readable fact tables (`[{predicate, object, since_chapter, source}]`)

Entity relationships are modeled via Obsidian `[[wikilinks]]` in card bodies and frontmatter fields.

### Init flow (wikilink-driven entity extraction)

During `init`, entities are extracted by parsing `[[wikilinks]]` from the generated worldview and main plot (not LLM enumeration). This ensures entity names are consistent with the narrative text. Each wikilink's source file and surrounding context are recorded. LLM is only used for classification (type + importance) when there are ≤30 entities; larger sets get default classifications. Protagonist entities get LLM-generated structured state; all others start as stubs with source references, waiting for `enrich` to populate them during chapter generation.

## Key constraints

- The `config.yaml` currently contains a hardcoded API key — **do not commit this file** to any public repository.
- Chapter filenames are zero-padded: `ch_005.md`, `ch_015.md`. The `chapter_count()` method counts files, so gaps in numbering will cause incorrect counts.
- Entity names must match exactly between `[[wikilinks]]`, frontmatter references, and the file system — the retriever does exact-name lookups, not fuzzy matching.
- `httpx` is used directly instead of provider SDKs — all LLM calls go through raw HTTP to OpenAI-compatible or Anthropic endpoints with a 300s timeout.
- `init` is idempotent: it skips existing files unless `--force` is passed. This allows re-running init to fill in gaps without regenerating everything.
- World and plot generation prompts require `[[wikilink]]` wrapping for all entities — this is critical for accurate entity extraction during init.
