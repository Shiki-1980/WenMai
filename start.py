"""WenMai 一键启动 —— 同时启动后端 (FastAPI) 和前端 (Vite) 开发服务器。

Usage:
  python start.py              # 启动前后端（开发模式，前端热重载）
  python start.py --backend-only  # 仅启动后端
  python start.py --frontend-only # 仅启动前端
  python start.py --prod          # 生产模式：构建前端静态文件，后端直接服务
  python start.py --no-browser    # 不自动打开浏览器

首次运行会自动安装依赖。
"""

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "web" / "backend"
FRONTEND_DIR = ROOT / "web" / "frontend"

BACKEND_PORT = 8742
FRONTEND_PORT = 5173


def _find_npm() -> str:
    """查找 npm 可执行文件路径，处理 Windows conda 环境 PATH 不完整的情况。"""
    import shutil

    # 用 shutil.which 在 PATH 中查找
    for cmd in ["npm.cmd", "npm"]:
        found = shutil.which(cmd)
        if found:
            return found

    # PATH 中没有，尝试常见安装位置
    for base in [r"C:\Program Files\nodejs", r"C:\Program Files (x86)\nodejs"]:
        for cmd in ["npm.cmd", "npm"]:
            candidate = str(Path(base) / cmd)
            if Path(candidate).exists():
                return candidate

    return "npm"


def _find_npx() -> str:
    """查找 npx 可执行文件路径。"""
    import shutil

    for cmd in ["npx.cmd", "npx"]:
        found = shutil.which(cmd)
        if found:
            return found

    # 从 npm 路径推断 npx
    npm_path = _find_npm()
    npm_dir = str(Path(npm_path).parent)
    for name in ["npx.cmd", "npx"]:
        candidate = str(Path(npm_dir) / name)
        if Path(candidate).exists():
            return candidate

    return "npx"


def check_deps():
    """检查并安装依赖。"""
    req_file = BACKEND_DIR / "requirements.txt"
    if req_file.exists():
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
        except ImportError:
            print("[依赖] 安装 Python 后端依赖...")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(req_file)],
                cwd=str(BACKEND_DIR), check=True,
            )

    if not (FRONTEND_DIR / "node_modules").exists():
        npm = _find_npm()
        print(f"[依赖] 安装 npm 前端依赖... (npm={npm})")
        subprocess.run([npm, "install"], cwd=str(FRONTEND_DIR), check=True)


def _stream_output(proc, prefix: str, stop_event: threading.Event):
    """在独立线程中读取子进程输出并打印。"""
    try:
        for line in proc.stdout:
            if stop_event.is_set():
                break
            line = line.rstrip()
            if line:
                print(f"  [{prefix}] {line}")
    except (ValueError, OSError):
        pass  # 进程已关闭


def start_backend(stop_event: threading.Event) -> subprocess.Popen | None:
    """启动 FastAPI 后端。"""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    try:
        proc = subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        print("[后端] 错误: 找不到 server.py，请确认 web/backend/ 目录存在")
        return None

    t = threading.Thread(
        target=_stream_output, args=(proc, "backend", stop_event), daemon=True,
    )
    t.start()
    return proc


def start_frontend(stop_event: threading.Event) -> subprocess.Popen | None:
    """启动 Vite 前端开发服务器。"""
    try:
        npx = _find_npx()
        proc = subprocess.Popen(
            [npx, "vite", "--host"],
            cwd=str(FRONTEND_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        print("[前端] 错误: 找不到 npx，请确认 Node.js 已安装")
        return None

    t = threading.Thread(
        target=_stream_output, args=(proc, "frontend", stop_event), daemon=True,
    )
    t.start()
    return proc


def build_frontend():
    """生产模式：构建前端。"""
    print("[前端] 构建生产版本...")
    subprocess.run(["npm", "run", "build"], cwd=str(FRONTEND_DIR), check=True)
    print("  构建完成 → web/frontend/dist/")
    print("  提示: 生产模式下，让 FastAPI 挂载 dist/ 目录作为静态文件即可。")


def main():
    parser = argparse.ArgumentParser(description="WenMai 一键启动")
    parser.add_argument("--backend-only", action="store_true", help="仅启动后端")
    parser.add_argument("--frontend-only", action="store_true", help="仅启动前端")
    parser.add_argument("--prod", action="store_true", help="生产模式（构建前端静态文件）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    print("=" * 50)
    print("  WenMai (文脉)")
    print("=" * 50)

    check_deps()

    if args.prod:
        build_frontend()
        print("\n生产模式：请配置 web/backend/server.py 挂载静态文件，")
        print("然后运行 python start.py --backend-only")
        return

    stop_event = threading.Event()
    procs: list[subprocess.Popen] = []

    def cleanup(sig=None, frame=None):
        if stop_event.is_set():
            return
        print("\n正在关闭...")
        stop_event.set()
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        time.sleep(0.5)
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        if not args.frontend_only:
            print(f"\n[后端] FastAPI → http://localhost:{BACKEND_PORT}")
            print(f"       API 文档 → http://localhost:{BACKEND_PORT}/docs")
            backend = start_backend(stop_event)
            if backend:
                procs.append(backend)

        if not args.backend_only:
            print(f"\n[前端] Vite → http://localhost:{FRONTEND_PORT}")
            frontend = start_frontend(stop_event)
            if frontend:
                procs.append(frontend)

        if not procs:
            print("错误: 没有成功启动任何服务")
            return

        print(f"\n{'=' * 50}")
        url = f"http://localhost:{FRONTEND_PORT}" if not args.backend_only else f"http://localhost:{BACKEND_PORT}"
        print(f"  打开浏览器: {url}")
        print(f"  按 Ctrl+C 停止所有服务")
        print(f"{'=' * 50}\n")

        if not args.no_browser and not args.backend_only:
            time.sleep(2)  # 等前端启动就绪
            webbrowser.open(url)

        # 主线程等待，直到被 Ctrl+C 中断
        while any(p.poll() is None for p in procs):
            time.sleep(1)

        # 如果有进程意外退出
        for p in procs:
            if p.poll() is not None:
                print(f"[错误] 某个服务意外退出 (code={p.returncode})")
        cleanup()

    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
