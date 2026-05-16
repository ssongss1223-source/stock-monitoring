"""Stop hook: 세션 종료 시 docs/checkpoint.md Last Updated 타임스탬프 자동 갱신."""
import subprocess
from datetime import datetime
from pathlib import Path

CHECKPOINT = Path(__file__).parent.parent / "docs" / "checkpoint.md"


def _git_info() -> str:
    try:
        log = subprocess.check_output(
            ["git", "log", "--oneline", "-3"],
            cwd=Path(__file__).parent.parent,
            text=True,
            timeout=5,
        ).strip()
        return log
    except Exception:
        return ""


def main():
    if not CHECKPOINT.exists():
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M KST")
    git_info = _git_info()

    text = CHECKPOINT.read_text(encoding="utf-8")
    lines = text.splitlines()

    new_lines = []
    for line in lines:
        if line.startswith("- 20") and "KST" in line:
            new_lines.append(f"- {now}")
        else:
            new_lines.append(line)

    CHECKPOINT.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    if git_info:
        print(f"[auto-checkpoint] {now}\n{git_info}")


if __name__ == "__main__":
    main()
