"""Author-controlled story direction, separate from facts and event plans."""
import json
from pathlib import Path

STORY_PLAN_PATH = "outline/story_plan.json"
FIELDS = (
    ("core", "故事核心", "例如：主角寻找失踪的亲人，却受到家族秘密的阻碍。"),
    ("growth", "人物变化", "例如：从只相信自己，到学会信任同伴。"),
    ("direction", "整体走向", "例如：从追查失踪案走向揭开旧城真相；结局尚未确定。"),
    ("boundaries", "创作边界", "例如：保持单一视角，不增加突然解决困境的外来力量。"),
)


def empty_plan() -> dict:
    return {"version": 1, "enabled": False, **{key: "" for key, _, _ in FIELDS}}


def parse_plan(text: str) -> dict:
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError("故事规划文件无法读取，请检查文件内容。") from exc
    if (not isinstance(data, dict) or data.get("version") != 1
            or type(data.get("enabled")) is not bool
            or any(not isinstance(data.get(key), str) for key, _, _ in FIELDS)):
        raise ValueError("故事规划格式不正确，无法加载或保存。")
    return {key: data[key] for key in empty_plan()}


def serialize_plan(data: dict) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    parse_plan(text)
    return text


def load_plan_context(root: Path) -> str:
    path = root / STORY_PLAN_PATH
    if not path.exists():
        return ""
    data = parse_plan(path.read_text(encoding="utf-8"))
    if not data["enabled"]:
        return ""
    return "\n\n".join(f"{label}：\n{data[key].strip()}" for key, label, _ in FIELDS if data[key].strip())
