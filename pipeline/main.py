"""Novel Pipeline —— Obsidian + LLM 全自动网文写作系统。

Usage:
  python main.py plan --direction "..." --num-chapters 30
  python main.py write --arc <arc_name>
  python main.py write-one --chapter <N>
  python main.py distill --chapter <N>
  python main.py status
"""

import argparse
import logging

from commands.audit import cmd_audit
from commands.distill import cmd_distill
from commands.enrich import cmd_enrich
from commands.init import cmd_init
from commands.manage import cmd_init_schema, cmd_list, cmd_rebuild_index, cmd_rename, cmd_switch
from commands.plan import cmd_plan
from commands.polish import cmd_polish
from commands.rollback import cmd_rollback
from commands.status import cmd_status
from commands.worldbuild import cmd_worldbuild
from commands.write import cmd_write, cmd_write_one

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pipeline")


def main():
    parser = argparse.ArgumentParser(description="Novel Pipeline")
    sub = parser.add_subparsers(dest="command")

    def _add_novel_arg(p):
        p.add_argument("--novel", "-N", help="小说名称（默认: config.yaml 中的活跃小说）")

    p_plan = sub.add_parser("plan", help="生成篇章大纲（卷规划）")
    _add_novel_arg(p_plan)
    p_plan.add_argument("--direction", "-d", required=True, help="这一卷的大方向")
    p_plan.add_argument("--num-chapters", "-n", type=int, default=0,
                        help="章节数（0=让 LLM 自行判断合适的长度）")
    p_plan.add_argument("--name", help="篇章名称（可选）")
    p_plan.add_argument("--volume", "-v", type=int, default=0,
                        help="卷号（0=自动检测下一卷）")
    p_plan.add_argument("--entities", "-e", action="store_true", default=True,
                        help="同时生成实体卡（默认开启）")

    p_write = sub.add_parser("write", help="按篇章大纲逐章写作")
    _add_novel_arg(p_write)
    p_write.add_argument("--arc", "-a", required=True, help="篇章名称")
    p_write.add_argument("--words", "-w", type=int, default=3000)
    p_write.add_argument("--force", "-f", action="store_true", help="强制重写已存在章节")
    p_write.add_argument("--yes", "-y", action="store_true", help="自动连续写，不询问")
    p_write.add_argument("--anti-ai", action="store_true", help="自动对每章做去 AI 味改写")

    p_one = sub.add_parser("write-one", help="写单独一章")
    _add_novel_arg(p_one)
    p_one.add_argument("--chapter", "-c", type=int, required=True)
    p_one.add_argument("--outline", "-o", help="本章概要（可选）")
    p_one.add_argument("--words", "-w", type=int, default=3000)
    p_one.add_argument("--anti-ai", action="store_true", help="写完后自动去 AI 味改写")

    p_distill = sub.add_parser("distill", help="重新蒸馏章节")
    _add_novel_arg(p_distill)
    p_distill.add_argument("--chapter", "-c", type=int, required=True)

    p_status = sub.add_parser("status", help="查看写作状态")
    _add_novel_arg(p_status)
    p_enrich = sub.add_parser("enrich", help="补全所有 stub 实体卡")
    _add_novel_arg(p_enrich)
    p_enrich.add_argument("--review-schema", action="store_true", help="同时用 LLM 审查 schema enum 值是否需要扩充")
    p_worldbuild = sub.add_parser("worldbuild", help="基于主线生成世界观设定")
    _add_novel_arg(p_worldbuild)

    p_init = sub.add_parser("init", help="一键初始化新小说项目")
    p_init.add_argument("name", nargs="?", help="小说名称（不填则 LLM 自动生成）")
    p_init.add_argument("--genre", "-g", default="xuanhuan", help="题材 (xuanhuan/xianxia/urban/scifi)")
    p_init.add_argument("--desc", "-d", required=True, help="一句话描述故事")
    p_init.add_argument("--chapters", "-n", type=int, default=30, help="第一卷章节数")
    p_init.add_argument("--force", "-f", action="store_true", help="覆盖已有文件")

    p_rename = sub.add_parser("rename", help="重命名小说")
    p_rename.add_argument("name", nargs="?", help="要重命名的小说（默认: 当前活跃小说）")
    p_rename.add_argument("--to", "-t", required=True, help="新名称")

    p_schema = sub.add_parser("init-schema", help="生成/更新 novel_schema.json")
    _add_novel_arg(p_schema)
    p_schema.add_argument("--force", "-f", action="store_true", help="强制重新生成")

    p_rebuild = sub.add_parser("rebuild-index", help="从实体卡重建 entity_index")
    _add_novel_arg(p_rebuild)

    sub.add_parser("list", help="列出所有小说")
    sub.add_parser("switch", help="切换活跃小说").add_argument("name", help="小说名称")

    p_audit = sub.add_parser("audit", help="审核并修改已生成的内容（世界观/主线/实体/大纲）")
    _add_novel_arg(p_audit)
    p_audit.add_argument("--revise", "-r", help="修改请求（如'主角性格太弱，加强'）")
    p_audit.add_argument("--target", "-t", default="all", help="修改目标: world/plot/entities/outline/all")

    p_rollback = sub.add_parser("rollback", help="回滚指定章节的状态变更")
    _add_novel_arg(p_rollback)
    p_rollback.add_argument("chapter", type=int, help="要回滚的章节号")
    p_rollback.add_argument("--force", "-f", action="store_true", help="跳过确认")

    p_polish = sub.add_parser("polish", help="对已有章节做去 AI 味改写")
    _add_novel_arg(p_polish)
    p_polish.add_argument("chapter", type=int, help="要改写的章节号")
    p_polish.add_argument("--force", "-f", action="store_true", help="跳过实体丢失检查，强制覆盖")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "plan":
        cmd_plan(args)
    elif args.command == "write":
        cmd_write(args)
    elif args.command == "write-one":
        cmd_write_one(args)
    elif args.command == "distill":
        cmd_distill(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "enrich":
        cmd_enrich(args)
    elif args.command == "worldbuild":
        cmd_worldbuild(args)
    elif args.command == "init-schema":
        cmd_init_schema(args)
    elif args.command == "rebuild-index":
        cmd_rebuild_index(args)
    elif args.command == "rename":
        cmd_rename(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "switch":
        cmd_switch(args)
    elif args.command == "audit":
        cmd_audit(args)
    elif args.command == "rollback":
        cmd_rollback(args)
    elif args.command == "polish":
        cmd_polish(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
