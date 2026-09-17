from __future__ import annotations

import argparse
from pathlib import Path

from integrated_agent.runtimes.agent import WorkspaceFileService


ROOT = Path(__file__).parent


def main() -> None:
    parser = argparse.ArgumentParser(
        description="在不连接企业微信的情况下验证通用Agent文件技能"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=ROOT / "workspace",
    )
    args = parser.parse_args()
    result = WorkspaceFileService(args.workspace).process(args.source)
    print(result.text)
    if result.artifact_path is not None:
        print(f"\n生成产物：{result.artifact_path}")


if __name__ == "__main__":
    main()
