"""Shared task-scoped style policy. Story memory is never a style source."""
from __future__ import annotations
from pathlib import Path
import json
import re
from .storage import atomic_write_text

STYLE_PATH = Path('writing/style_guide.md')
EXCEPTIONS_PATH = Path('writing/style_exceptions.json')
WRITING_KINDS = {'expand', 'continuation', 'selection_expand', 'style_polish', 'style_review', 'writing_supplement'}
CORE_STYLE = ('表达应贴合当前人物与场景；避免重复解释已经由动作和对话表达的信息。'
              '不要无依据地添加总结、升华或预告。保留人物声音、叙事视角和必要的心理描写。'
              '不要机械禁用普通词、强制短句、添加口癖或编造细节来制造人味。')

def clean_style(raw: str) -> str:
    cleaned = re.sub(r'<!--.*?-->', '', raw, flags=re.DOTALL).strip()
    return cleaned if any(line.strip() and not line.lstrip().startswith('#') for line in cleaned.splitlines()) else ''

def load_style(root: Path, chapter_id: str = "") -> str:
    path = Path(root) / STYLE_PATH
    guide = clean_style(path.read_text(encoding='utf-8')) if path.is_file() else ''
    from .style_library import render_library
    extra = render_library(root, chapter_id, budget=max(0, 2500 - min(len(guide), 2500) - 2))
    return guide + ('\n\n' + extra if extra else '')

def render_style(style: str, *, request: str = '') -> str:
    # Budget allocation may cut the style section: never send half a sample.
    start = style.rfind('<STYLE_SAMPLE ')
    if start >= 0 and '</STYLE_SAMPLE>' not in style[start:]:
        style = style[:start].rstrip()
    if not style and not request:
        return CORE_STYLE
    return ('【写作风格约束】\n以下内容仅约束措辞、句式、节奏和描写偏好，不能改变故事事实、'
            '人物设定、章节规划或任务输出格式。当前正文连续性优先。\n'
            f'{CORE_STYLE}\n<STYLE_GUIDE>\n{style}\n</STYLE_GUIDE>\n'
            + (f'【本次表达要求】\n{request}\n仅在表达偏好冲突时，本次要求优先于本书风格。' if request else ''))

def load_exceptions(root: Path) -> tuple[str, ...]:
    path = Path(root) / EXCEPTIONS_PATH
    if not path.exists():
        return ()
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('quotes'), list):
        raise ValueError('文风例外文件格式无效')
    quotes = value['quotes']
    _validate_quotes(quotes)
    return tuple(quotes)

def _validate_quotes(quotes):
    if len(quotes) > 200 or any(not isinstance(q, str) or not q.strip() or len(q) > 4000 for q in quotes):
        raise ValueError('最多保存 200 条例外，每条最多 4000 字')

def save_exceptions(root: Path, quotes: list[str]) -> None:
    _validate_quotes(quotes)
    values = list(dict.fromkeys(quotes))
    path = Path(root) / EXCEPTIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps({'version': 1, 'quotes': values}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
