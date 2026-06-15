"""WenMai Web Backend — FastAPI server wrapping the novel pipeline."""

import sys
import os

# Force unbuffered I/O — critical for SSE streaming to work.
# print() output must reach the OutputCapture immediately, not sit
# in Python's buffer waiting for a flush that never comes.
os.environ["PYTHONUNBUFFERED"] = "1"

import json
import queue
import threading
import time
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional

# Add pipeline to Python path
PIPELINE_DIR = Path(__file__).resolve().parent.parent.parent / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

# ── Config ──────────────────────────────────────────────────────────
CONFIG_PATH = PIPELINE_DIR / "config.yaml"
VAULT_PATH = Path(yaml.safe_load(open(CONFIG_PATH))["vault"]["path"])


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)


# ── Output Capture ───────────────────────────────────────────────────

class OutputCapture:
    """Line-buffered stdout capture that feeds an async queue.

    print() typically calls write(text) then write('\\n') in two calls.
    We accumulate writes and emit complete lines to the SSE queue.
    flush() emits any remaining partial line immediately.
    """

    def __init__(self, loop, on_input=None):
        self.loop = loop
        self.queue = asyncio.Queue()
        self.on_input = on_input
        self._original_stdout = None
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, text):
        if not text:
            return
        with self._lock:
            self._buffer += text
            # Emit every complete line (ends with \n)
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line.strip():
                    self.loop.call_soon_threadsafe(
                        self.queue.put_nowait,
                        {"type": "output", "text": line + "\n"},
                    )

    def flush(self):
        with self._lock:
            if self._buffer.strip():
                self.loop.call_soon_threadsafe(
                    self.queue.put_nowait,
                    {"type": "output", "text": self._buffer},
                )
                self._buffer = ""

    def __enter__(self):
        import builtins as _b

        self._original_stdout = sys.stdout
        sys.stdout = self
        # Patch print to always flush — this is the key fix for
        # Python buffering output when stdout is not a tty.
        self._original_print = _b.print
        _b.print = lambda *a, **kw: self._original_print(
            *a, **{**kw, "flush": True}
        )
        return self

    def __exit__(self, *args):
        import builtins as _b

        sys.stdout = self._original_stdout
        _b.print = self._original_print


# ── Input Patching ───────────────────────────────────────────────────

_input_events: dict[str, threading.Event] = {}
_input_values: dict[str, str] = {}
_command_lock = threading.Lock()


def _make_patched_input(task_id: str, capture: OutputCapture):
    """Create a patched input() for a specific task."""
    _input_events[task_id] = threading.Event()
    _input_values[task_id] = ""

    def _patched(prompt: str = "") -> str:
        if prompt:
            capture.loop.call_soon_threadsafe(
                capture.queue.put_nowait,
                {"type": "waiting_for_input", "text": prompt},
            )
        event = _input_events.get(task_id)
        if event:
            event.wait()
            event.clear()
        val = _input_values.get(task_id, "")
        capture.loop.call_soon_threadsafe(
            capture.queue.put_nowait,
            {"type": "output", "text": f">>> {val}\n"},
        )
        return val

    return _patched


# ── Command Runner ───────────────────────────────────────────────────

def _run_in_thread(coro_or_fn, *args, **kwargs):
    """Run a function or coroutine in a thread and capture exceptions."""
    try:
        coro_or_fn(*args, **kwargs)
    except SystemExit:
        pass


def execute_command(
    task_id: str,
    cmd_fn,
    cmd_args,
    loop,
    capture: OutputCapture,
    original_input,
) -> threading.Thread:
    """Run a pipeline command in a background thread with captured I/O."""

    def _run():
        import builtins

        builtins.input = _make_patched_input(task_id, capture)
        try:
            cmd_fn(cmd_args)
        except SystemExit:
            pass
        except Exception as e:
            import traceback

            tb = traceback.format_exc()
            loop.call_soon_threadsafe(
                capture.queue.put_nowait,
                {"type": "output", "text": f"\n[ERROR] {e}\n{tb}\n"},
            )
        finally:
            builtins.input = original_input
            loop.call_soon_threadsafe(
                capture.queue.put_nowait, {"type": "done", "text": ""}
            )
            _command_lock.release()

    _command_lock.acquire()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


# ── FastAPI App ──────────────────────────────────────────────────────

app = FastAPI(title="WenMai Studio", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic Models ──────────────────────────────────────────────────

class InitRequest(BaseModel):
    name: str = ""
    genre: str = "xuanhuan"
    desc: str = ""
    chapters: int = 30
    force: bool = False


class SwitchRequest(BaseModel):
    name: str


class RenameRequest(BaseModel):
    name: str = ""
    to: str


class PlanRequest(BaseModel):
    novel: str = ""
    direction: str = ""
    num_chapters: int = 0
    name: str = ""
    volume: int = 0
    entities: bool = True


class WriteRequest(BaseModel):
    novel: str = ""
    arc: str = ""
    words: int = 3000
    force: bool = False
    yes: bool = True  # Default auto-continue for web UI
    anti_ai: bool = False


class WriteOneRequest(BaseModel):
    novel: str = ""
    chapter: int = 0
    outline: str = ""
    words: int = 3000
    anti_ai: bool = False


class DistillRequest(BaseModel):
    novel: str = ""
    chapter: int = 0


class PolishRequest(BaseModel):
    novel: str = ""
    chapter: int = 0
    force: bool = False
    legacy: bool = False


class AuditRequest(BaseModel):
    novel: str = ""
    revise: str = ""
    target: str = "all"


class InputResponse(BaseModel):
    value: str


class ConfigUpdate(BaseModel):
    novel: str = ""


# ── Helper ───────────────────────────────────────────────────────────

def _make_namespace(**kwargs):
    """Create an argparse.Namespace from keyword arguments."""
    from argparse import Namespace

    return Namespace(**kwargs)


async def _sse_stream(task_id: str, cmd_fn, cmd_args, loop) -> StreamingResponse:
    """Generic SSE streaming endpoint for pipeline commands."""
    capture = OutputCapture(loop)
    original_input = __builtins__.input if hasattr(__builtins__, "input") else input

    async def event_generator():
        with capture:
            execute_command(task_id, cmd_fn, cmd_args, loop, capture, original_input)
            while True:
                try:
                    msg = await asyncio.wait_for(capture.queue.get(), timeout=0.1)
                    event_type = msg["type"]
                    if event_type == "done":
                        yield f"event: done\ndata: {{}}\n\n"
                        break
                    elif event_type == "waiting_for_input":
                        yield f"event: input\ndata: {json.dumps({'prompt': msg['text']})}\n\n"
                    else:
                        yield f"data: {json.dumps({'text': msg['text']})}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Config Endpoints ─────────────────────────────────────────────────

@app.get("/api/config")
def get_config():
    cfg = load_config()
    return {
        "vault_path": cfg["vault"]["path"],
        "active_novel": cfg["vault"].get("novel", ""),
        "provider": cfg["llm"]["provider"],
        "model": cfg["llm"]["model"],
        "chapter_words": cfg["generation"]["chapter_words"],
    }


@app.put("/api/config")
def update_config(body: ConfigUpdate):
    cfg = load_config()
    if body.novel:
        vault_path = Path(cfg["vault"]["path"])
        novel_path = vault_path / body.novel
        if not novel_path.exists():
            raise HTTPException(404, f"Novel not found: {body.novel}")
        cfg["vault"]["novel"] = body.novel
        save_config(cfg)
    return {"ok": True}


# ── Response Endpoint ────────────────────────────────────────────────

@app.post("/api/respond/{task_id}")
def respond_to_prompt(task_id: str, body: InputResponse):
    if task_id in _input_events:
        _input_values[task_id] = body.value
        _input_events[task_id].set()
        return {"ok": True}
    raise HTTPException(404, "Task not found or not waiting for input")


# ── Novel Management ─────────────────────────────────────────────────

@app.get("/api/novels")
def list_novels():
    novels_dir = VAULT_PATH / "novels"
    if not novels_dir.exists():
        return {"novels": [], "active": ""}

    cfg = load_config()
    active = cfg["vault"].get("novel", "")

    novels = []
    for d in sorted(novels_dir.iterdir()):
        if d.is_dir():
            ch_count = len(list((d / "chapter").glob("ch_*.md"))) if (d / "chapter").exists() else 0
            entity_count = sum(1 for _ in (d / "entity").rglob("*.md")) if (d / "entity").exists() else 0
            novels.append(
                {
                    "name": d.name,
                    "path": f"novels/{d.name}",
                    "chapter_count": ch_count,
                    "entity_count": entity_count,
                    "is_active": f"novels/{d.name}" == active,
                }
            )
    return {"novels": novels, "active": active}


@app.post("/api/novels/switch")
def switch_novel(body: SwitchRequest):
    cfg = load_config()
    novel_rel = f"novels/{body.name}"
    novel_path = VAULT_PATH / novel_rel
    if not novel_path.exists():
        raise HTTPException(404, f"Novel not found: {body.name}")
    cfg["vault"]["novel"] = novel_rel
    save_config(cfg)
    return {"ok": True, "active": novel_rel}


@app.post("/api/novels/rename")
def rename_novel(body: RenameRequest):
    cfg = load_config()
    old_name = body.name
    if not old_name:
        old_rel = cfg["vault"].get("novel", "")
        old_name = old_rel.replace("novels/", "") if old_rel else ""
    else:
        old_rel = f"novels/{old_name}"

    if not old_name:
        raise HTTPException(400, "No novel specified")

    old_path = VAULT_PATH / old_rel
    if not old_path.exists():
        raise HTTPException(404, f"Novel not found: {old_name}")

    new_rel = f"novels/{body.to}"
    new_path = VAULT_PATH / new_rel
    if new_path.exists():
        raise HTTPException(400, f"Target already exists: {body.to}")

    old_path.rename(new_path)
    if old_rel == cfg["vault"].get("novel", ""):
        cfg["vault"]["novel"] = new_rel
        save_config(cfg)

    return {"ok": True, "old": old_name, "new": body.to}


@app.post("/api/novels/init")
async def init_novel(body: InitRequest, request: Request):
    from main import cmd_init

    args = _make_namespace(
        name=body.name or None,
        genre=body.genre,
        desc=body.desc,
        chapters=body.chapters,
        force=body.force,
    )
    task_id = f"init_{int(time.time() * 1000)}"
    loop = asyncio.get_event_loop()
    return await _sse_stream(task_id, cmd_init, args, loop)


# ── Status ───────────────────────────────────────────────────────────

@app.get("/api/status")
def get_status(novel: str = ""):
    cfg = load_config()
    novel_rel = novel or cfg["vault"].get("novel", "")
    if not novel_rel:
        raise HTTPException(400, "No novel specified")

    content_root = VAULT_PATH / novel_rel
    if not content_root.exists():
        raise HTTPException(404, f"Novel not found: {novel_rel}")

    from reader import VaultReader
    from state_schema import NovelSchema
    from commit_store import CommitStore

    reader = VaultReader(str(content_root))
    schema = NovelSchema.load(Path(str(content_root)))
    store = CommitStore(Path(str(content_root)))

    total_ch = reader.chapter_count()
    entities = reader.all_entity_names()

    # Entity counts by type
    entity_types = {"person": 0, "item": 0, "location": 0, "concept": 0}
    stub_count = 0
    for etype, name in entities:
        entity_types[etype] = entity_types.get(etype, 0) + 1
        card = reader.read_entity(etype, name)
        if card and card[0].get("status") == "stub":
            stub_count += 1

    # Arc/volume progress
    arcs = []
    for arc_name in reader.list_arcs():
        arc = reader.read_arc(arc_name)
        if not arc:
            continue
        meta, _ = arc
        cr = meta.get("chapter_range", "")
        title = meta.get("title", arc_name)
        volume = meta.get("volume", 1)
        status = meta.get("status", "planned")
        done = 0
        total = 0
        start_ch = 0
        end_ch = 0
        if cr:
            parts = cr.split("-")
            if len(parts) == 2:
                try:
                    start_ch, end_ch = int(parts[0]), int(parts[1])
                    total = end_ch - start_ch + 1
                    for ch in range(start_ch, end_ch + 1):
                        if reader.read_chapter(ch):
                            done += 1
                except ValueError:
                    pass
        arcs.append({
            "name": arc_name, "title": title, "volume": volume,
            "chapter_range": cr, "status": status,
            "done": done, "total": total,
            "start_chapter": start_ch, "end_chapter": end_ch,
        })

    # Group arcs by volume
    volumes = {}
    for arc in arcs:
        vol = arc["volume"]
        if vol not in volumes:
            volumes[vol] = {"volume": vol, "arcs": [], "done": 0, "total": 0}
        volumes[vol]["arcs"].append(arc)
        volumes[vol]["done"] += arc["done"]
        volumes[vol]["total"] += arc["total"]

    # Chapter list
    chapters = []
    for ch_num in range(1, total_ch + 1):
        ch = reader.read_chapter(ch_num)
        if ch:
            meta, _ = ch
            chapters.append({
                "number": ch_num,
                "title": meta.get("title", f"第{ch_num}章"),
                "created": meta.get("created", ""),
            })

    # World and plot status
    world = reader.read_world_bible()
    main_plot = reader.read_main_plot()
    plot_pool = reader.read_plot_pool()

    return {
        "novel": novel_rel,
        "chapter_count": total_ch,
        "entity_count": len(entities),
        "entity_by_type": entity_types,
        "stub_count": stub_count,
        "commit_count": store.commit_count(),
        "schema_version": schema.schema_version if schema else 0,
        "has_world": bool(world),
        "has_main_plot": bool(main_plot),
        "has_plot_pool": bool(plot_pool),
        "chapters": chapters,
        "arcs": arcs,
        "volumes": sorted(volumes.values(), key=lambda v: v["volume"]),
    }


# ── Writing Operations (SSE) ─────────────────────────────────────────

@app.post("/api/plan")
async def plan_arc(body: PlanRequest, request: Request):
    from main import cmd_plan

    args = _make_namespace(
        novel=body.novel or None,
        direction=body.direction,
        num_chapters=body.num_chapters,
        name=body.name or None,
        volume=body.volume,
        entities=body.entities,
    )
    task_id = f"plan_{int(time.time() * 1000)}"
    loop = asyncio.get_event_loop()
    return await _sse_stream(task_id, cmd_plan, args, loop)


@app.post("/api/write")
async def write_arc(body: WriteRequest, request: Request):
    from main import cmd_write

    args = _make_namespace(
        novel=body.novel or None,
        arc=body.arc,
        words=body.words,
        force=body.force,
        yes=body.yes,
        anti_ai=body.anti_ai,
    )
    task_id = f"write_{int(time.time() * 1000)}"
    loop = asyncio.get_event_loop()
    return await _sse_stream(task_id, cmd_write, args, loop)


@app.post("/api/write-one")
async def write_one_chapter(body: WriteOneRequest, request: Request):
    from main import cmd_write_one

    args = _make_namespace(
        novel=body.novel or None,
        chapter=body.chapter,
        outline=body.outline or None,
        words=body.words,
        anti_ai=body.anti_ai,
    )
    task_id = f"write_one_{int(time.time() * 1000)}"
    loop = asyncio.get_event_loop()
    return await _sse_stream(task_id, cmd_write_one, args, loop)


@app.post("/api/polish")
async def polish_chapter(body: PolishRequest, request: Request):
    from main import cmd_polish

    args = _make_namespace(
        novel=body.novel or None,
        chapter=body.chapter,
        force=body.force,
        legacy=body.legacy,
    )
    task_id = f"polish_{int(time.time() * 1000)}"
    loop = asyncio.get_event_loop()
    return await _sse_stream(task_id, cmd_polish, args, loop)


@app.post("/api/distill")
async def distill_chapter(body: DistillRequest, request: Request):
    from main import cmd_distill

    args = _make_namespace(
        novel=body.novel or None,
        chapter=body.chapter,
    )
    task_id = f"distill_{int(time.time() * 1000)}"
    loop = asyncio.get_event_loop()
    return await _sse_stream(task_id, cmd_distill, args, loop)


# ── Audit ────────────────────────────────────────────────────────────

@app.get("/api/audit")
def get_audit_summary(novel: str = ""):
    cfg = load_config()
    novel_rel = novel or cfg["vault"].get("novel", "")
    if not novel_rel:
        raise HTTPException(400, "No novel specified")

    content_root = VAULT_PATH / novel_rel
    from reader import VaultReader
    reader = VaultReader(str(content_root))

    world = reader.read_world_bible()
    main = reader.read_main_plot()

    entities = []
    for etype, name in reader.all_entity_names():
        card = reader.read_entity(etype, name)
        if card:
            meta, body = card
            entities.append({
                "type": etype, "name": name,
                "importance": meta.get("importance", "?"),
                "status": meta.get("status", "active"),
                "preview": body[:200] if body else "",
            })

    arcs = []
    for arc_name in reader.list_arcs():
        arc = reader.read_arc(arc_name)
        if arc:
            meta, _ = arc
            arcs.append({"name": arc_name, "range": meta.get("chapter_range", ""), "status": meta.get("status", "")})

    return {
        "world": {"exists": bool(world), "preview": world[1][:500] if world else ""},
        "main_plot": {"exists": bool(main), "preview": main[1][:500] if main else ""},
        "entities": entities,
        "arcs": arcs,
    }


@app.post("/api/audit")
async def run_audit(body: AuditRequest, request: Request):
    from main import cmd_audit

    args = _make_namespace(
        novel=body.novel or None,
        revise=body.revise or None,
        target=body.target,
    )
    task_id = f"audit_{int(time.time() * 1000)}"
    loop = asyncio.get_event_loop()
    return await _sse_stream(task_id, cmd_audit, args, loop)


# ── Maintenance ──────────────────────────────────────────────────────

@app.post("/api/enrich")
async def run_enrich(request: Request):
    from main import cmd_enrich

    args = _make_namespace(novel=None)
    task_id = f"enrich_{int(time.time() * 1000)}"
    loop = asyncio.get_event_loop()
    return await _sse_stream(task_id, cmd_enrich, args, loop)


@app.post("/api/worldbuild")
async def run_worldbuild(request: Request):
    from main import cmd_worldbuild

    args = _make_namespace(novel=None)
    task_id = f"worldbuild_{int(time.time() * 1000)}"
    loop = asyncio.get_event_loop()
    return await _sse_stream(task_id, cmd_worldbuild, args, loop)


@app.post("/api/init-schema")
async def run_init_schema(request: Request):
    from main import cmd_init_schema

    args = _make_namespace(novel=None, force=False)
    task_id = f"init_schema_{int(time.time() * 1000)}"
    loop = asyncio.get_event_loop()
    return await _sse_stream(task_id, cmd_init_schema, args, loop)


@app.post("/api/rebuild-index")
def run_rebuild_index():
    from main import cmd_rebuild_index
    from argparse import Namespace

    args = Namespace(novel=None)
    cmd_rebuild_index(args)
    return {"ok": True}


# ── Content Reading ──────────────────────────────────────────────────

@app.get("/api/chapters")
def list_chapters(novel: str = ""):
    cfg = load_config()
    novel_rel = novel or cfg["vault"].get("novel", "")
    content_root = VAULT_PATH / novel_rel
    from reader import VaultReader
    reader = VaultReader(str(content_root))

    chapters = []
    total = reader.chapter_count()
    for ch_num in range(1, total + 1):
        ch = reader.read_chapter(ch_num)
        if ch:
            meta, body = ch
            chapters.append({
                "number": ch_num,
                "title": meta.get("title", f"第{ch_num}章"),
                "created": meta.get("created", ""),
                "preview": body[:300] if body else "",
                "word_count": len(body) if body else 0,
            })
    return {"chapters": chapters}


@app.get("/api/chapters/{chapter_num}")
def read_chapter(chapter_num: int, novel: str = ""):
    cfg = load_config()
    novel_rel = novel or cfg["vault"].get("novel", "")
    content_root = VAULT_PATH / novel_rel
    from reader import VaultReader
    reader = VaultReader(str(content_root))

    ch = reader.read_chapter(chapter_num)
    if not ch:
        raise HTTPException(404, f"Chapter {chapter_num} not found")
    meta, body = ch

    # Get summary if exists
    summary = None
    s = reader.read_summary(chapter_num)
    if s:
        _, sbody = s
        summary = sbody

    return {
        "number": chapter_num,
        "title": meta.get("title", ""),
        "created": meta.get("created", ""),
        "content": body,
        "summary": summary,
    }


@app.get("/api/entities")
def list_entities(novel: str = "", type: str = ""):
    cfg = load_config()
    novel_rel = novel or cfg["vault"].get("novel", "")
    content_root = VAULT_PATH / novel_rel
    from reader import VaultReader
    reader = VaultReader(str(content_root))

    entities = []
    for etype, name in reader.all_entity_names():
        if type and etype != type:
            continue
        card = reader.read_entity(etype, name)
        if card:
            meta, body = card
            state = reader.read_entity_state(etype, name)
            entities.append({
                "type": etype,
                "name": name,
                "importance": meta.get("importance", "supporting"),
                "status": meta.get("status", "active"),
                "aliases": meta.get("aliases", []),
                "updated": meta.get("updated", ""),
                "enriched_through": meta.get("enriched_through", 0),
                "preview": body[:200] if body else "",
                "fact_count": len(state.facts) if state else 0,
            })
    return {"entities": entities}


@app.get("/api/entities/{entity_type}/{entity_name}")
def read_entity(entity_type: str, entity_name: str, novel: str = ""):
    cfg = load_config()
    novel_rel = novel or cfg["vault"].get("novel", "")
    content_root = VAULT_PATH / novel_rel
    from reader import VaultReader
    reader = VaultReader(str(content_root))

    card = reader.read_entity(entity_type, entity_name)
    if not card:
        raise HTTPException(404, f"Entity not found: {entity_name}")
    meta, body = card

    state = reader.read_entity_state(entity_type, entity_name)
    facts = []
    if state:
        for f in state.facts:
            facts.append({
                "predicate": f.predicate,
                "object": f.object,
                "since_chapter": f.since_chapter,
                "source": f.source,
            })

    # Get chapters where this entity appears
    appeared = reader.summaries_for_entity(entity_name)

    return {
        "type": entity_type,
        "name": entity_name,
        "importance": meta.get("importance", ""),
        "status": meta.get("status", ""),
        "aliases": meta.get("aliases", []),
        "created": meta.get("created", ""),
        "updated": meta.get("updated", ""),
        "enriched_through": meta.get("enriched_through", 0),
        "content": body,
        "facts": facts,
        "appears_in": sorted(appeared) if appeared else [],
    }


@app.get("/api/arcs")
def list_arcs(novel: str = ""):
    cfg = load_config()
    novel_rel = novel or cfg["vault"].get("novel", "")
    content_root = VAULT_PATH / novel_rel
    from reader import VaultReader
    reader = VaultReader(str(content_root))

    arcs = []
    for arc_name in reader.list_arcs():
        arc = reader.read_arc(arc_name)
        if arc:
            meta, body = arc
            arcs.append({
                "name": arc_name,
                "title": meta.get("title", ""),
                "status": meta.get("status", ""),
                "volume": meta.get("volume", 1),
                "chapter_range": meta.get("chapter_range", ""),
                "key_entities": meta.get("key_entities", []),
                "content": body[:2000] if body else "",
            })
    return {"arcs": arcs}


@app.get("/api/arcs/{arc_name}")
def read_arc(arc_name: str, novel: str = ""):
    cfg = load_config()
    novel_rel = novel or cfg["vault"].get("novel", "")
    content_root = VAULT_PATH / novel_rel
    from reader import VaultReader
    reader = VaultReader(str(content_root))

    arc = reader.read_arc(arc_name)
    if not arc:
        raise HTTPException(404, f"Arc not found: {arc_name}")
    meta, body = arc
    return {
        "name": arc_name,
        "title": meta.get("title", ""),
        "status": meta.get("status", ""),
        "volume": meta.get("volume", 1),
        "chapter_range": meta.get("chapter_range", ""),
        "key_entities": meta.get("key_entities", []),
        "constraints": meta.get("constraints", ""),
        "content": body,
    }


@app.get("/api/world")
def read_world(novel: str = ""):
    cfg = load_config()
    novel_rel = novel or cfg["vault"].get("novel", "")
    content_root = VAULT_PATH / novel_rel
    from reader import VaultReader
    reader = VaultReader(str(content_root))

    world = reader.read_world_bible()
    if not world:
        return {"exists": False, "content": ""}
    meta, body = world
    return {"exists": True, "title": meta.get("title", "世界观"), "content": body, "meta": meta}


class WorldUpdate(BaseModel):
    novel: str = ""
    content: str


@app.put("/api/world")
def update_world(body: WorldUpdate):
    cfg = load_config()
    novel_rel = body.novel or cfg["vault"].get("novel", "")
    content_root = VAULT_PATH / novel_rel
    world_path = content_root / "plot" / "世界观.md"
    if not world_path.exists():
        raise HTTPException(404, "World bible not found")
    world_path.write_text(body.content, "utf-8")
    return {"ok": True}


@app.get("/api/main-plot")
def read_main_plot(novel: str = ""):
    cfg = load_config()
    novel_rel = novel or cfg["vault"].get("novel", "")
    content_root = VAULT_PATH / novel_rel
    from reader import VaultReader
    reader = VaultReader(str(content_root))

    main = reader.read_main_plot()
    if not main:
        return {"exists": False, "content": ""}
    meta, body = main
    return {"exists": True, "title": meta.get("title", "主线"), "content": body, "meta": meta}


@app.put("/api/main-plot")
def update_main_plot(body: WorldUpdate):
    cfg = load_config()
    novel_rel = body.novel or cfg["vault"].get("novel", "")
    content_root = VAULT_PATH / novel_rel
    plot_path = content_root / "plot" / "主线.md"
    if not plot_path.exists():
        raise HTTPException(404, "Main plot not found")
    plot_path.write_text(body.content, "utf-8")
    return {"ok": True}


@app.get("/api/plot-pool")
def read_plot_pool(novel: str = ""):
    cfg = load_config()
    novel_rel = novel or cfg["vault"].get("novel", "")
    content_root = VAULT_PATH / novel_rel
    from reader import VaultReader
    reader = VaultReader(str(content_root))

    pool = reader.read_plot_pool()
    if not pool:
        return {"exists": False, "active": "", "resolved": ""}
    _, active, resolved = pool if len(pool) == 3 else (None, pool[1], "")
    return {"exists": True, "active": active, "resolved": resolved}


# ── Health ────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "vault_path": str(VAULT_PATH),
        "active_novel": load_config()["vault"].get("novel", ""),
    }


# ── Main ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    print(f"Vault path: {VAULT_PATH}")
    print(f"Pipeline dir: {PIPELINE_DIR}")
    uvicorn.run(app, host="0.0.0.0", port=8742, log_level="info")
