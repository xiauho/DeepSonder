"""Reproducible A/B/C writing trial; never opens a user manuscript.

A = same task without style policy (ablation, not a historical release).
B = shared current style policy. C = B plus one review and all valid patches.
Human judgments remain blank; machine metrics are not AI-detection scores.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path
import random
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.config import get_config_path, normalize_config
from core.dsh_client import DSHClient
from core.project import NovelProject
from core.prose_review import run_prose_task, apply_patches
from core.writing_style import render_style

STYLE = "第三人称有限视角。对话保留省略和犹疑；动作应有清楚的先后关系。避免在结尾解释主题。"

def prompts(case):
    system = "你是小说写作助手。遵守已给定事实、视角与任务要求。只输出正文。"
    user = f"任务：{case['mode']}，约 {case['target_chars']} 字。{case['instruction']}\n原文：{case['source']}"
    return system, user

def configured_client():
    path = get_config_path()
    value = normalize_config(json.loads(path.read_text(encoding="utf-8")) if path.exists() else {})
    client = DSHClient(dsh_command=value["dsh_command"], launcher_args=value["dsh_launcher_args"],
        extra_args=value["dsh_extra_args"], timeout=min(value["dsh_timeout"], 180),
        file_prompt_budget=value["dsh_file_prompt_budget"], task_file_max_bytes=value["dsh_task_file_max_bytes"],
        input_token_budget=value["ai_input_token_budget"], runtime_reserve_tokens=value["ai_runtime_reserve_tokens"],
        model_context_window_tokens=value["ai_model_context_window_tokens"], context_strategy=value["ai_context_strategy"])
    client.use_isolated_workspace()
    return client

def run(output, *, limit=24, dry_run=False, client=None):
    if not 1 <= limit <= 24: raise ValueError("limit 必须在 1–24 之间")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)  # Never overwrite a trial.
    corpus_bytes = (ROOT / "evals/prose_cases.json").read_bytes()
    cases = json.loads(corpus_bytes)["cases"][:limit]
    manifest = {"schema_version":1, "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        "style": STYLE, "dry_run": dry_run, "cases": len(cases), "status":"running",
        "comparison":"A=no style; B=current style; C=B+one review, all valid patches applied in evaluation only",
        "human_assessment":"pending", "metrics":[], "errors":[]}
    def save():
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    save()
    if dry_run:
        (output / "prompts.json").write_text(json.dumps([{**c, "system":prompts(c)[0], "user":prompts(c)[1], "style":render_style(STYLE)} for c in cases],ensure_ascii=False,indent=2),encoding="utf-8")
        manifest["status"] = "dry_run"; save(); return manifest
    owned = client is None
    keys=[]; ratings=[]
    try:
        client = client or configured_client()
        with tempfile.TemporaryDirectory(prefix="deepsonder-writing-eval-") as directory:
            project = NovelProject.create(Path(directory)/"novel", "独立文风评测")
            project.style_guide_path.parent.mkdir(parents=True, exist_ok=True)
            project.style_guide_path.write_text(STYLE, encoding="utf-8")
            for case in cases:
                system, user = prompts(case); outputs={}; elapsed={}
                # Alternate call order to reduce fixed-order effects.
                order = ("A", "B") if int(case["id"][-2:]) % 2 else ("B", "A")
                for variant in order:
                    start=time.monotonic()
                    outputs[variant] = client.generate(system, user + ("\n"+render_style(STYLE) if variant=="B" else ""))
                    elapsed[variant]=time.monotonic()-start
                    (output/f"{case['id']}-{variant}.txt").write_text(outputs[variant],encoding="utf-8")
                source="# 评测\n\n"+outputs["B"]
                start=time.monotonic()
                review=run_prose_task(project, "chapter_01", client, source=source, start=len("# 评测\n\n"), end=len(source), kind="style_review")
                outputs["C"]=apply_patches(source,review.patches)[len("# 评测\n\n"):]
                elapsed["C"]=elapsed["B"]+time.monotonic()-start
                (output/f"{case['id']}-C.txt").write_text(outputs["C"],encoding="utf-8")
                (output/f"{case['id']}-review.json").write_text(json.dumps([p.__dict__ for p in review.patches],ensure_ascii=False,indent=2),encoding="utf-8")
                order=list("ABC"); random.SystemRandom().shuffle(order)
                blind=output/"blind"; blind.mkdir(exist_ok=True)
                for index, variant in enumerate(order, 1):
                    label=f"{case['id']}-{index}"
                    (blind/f"{label}.txt").write_text(f"任务：{case['mode']}，约 {case['target_chars']} 字。{case['instruction']}\n原文：{case['source']}\n\n候选正文：\n"+outputs[variant],encoding="utf-8")
                    keys.append({"label":label,"variant":variant})
                    ratings.append({"label":label, "preference_rank":"", "edit_minutes":"", "fact_errors":"", "character_voice_1_5":"", "continuity_1_5":"", "notes":""})
                    manifest["metrics"].append({"case":case["id"],"variant":variant,"chars":len(outputs[variant]),"seconds":round(elapsed[variant],3),"logical_model_calls":2 if variant=="C" else 1})
                save()
        manifest["status"]="complete_pending_human_review"
    except Exception as exc:
        manifest["status"]="failed"
        # Do not copy potentially secret provider stderr into review artifacts.
        manifest["errors"].append({"type":type(exc).__name__,"message":"模型运行失败；请在应用中验证 AI 连接后创建新的评测目录。"})
    finally:
        if owned and client is not None: client.cleanup()
        (output/"blind-key.json").write_text(json.dumps(keys,indent=2),encoding="utf-8")
        with (output/"ratings.csv").open("w",encoding="utf-8-sig",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=["label","preference_rank","edit_minutes","fact_errors","character_voice_1_5","continuity_1_5","notes"])
            writer.writeheader();writer.writerows(ratings)
        save()
    return manifest

if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--limit",type=int,default=24)
    parser.add_argument("--dry-run",action="store_true")
    args=parser.parse_args()
    result=run(args.output,limit=args.limit,dry_run=args.dry_run)
    print(json.dumps({"status":result["status"],"cases":result["cases"],"errors":result["errors"]},ensure_ascii=False))
    sys.exit(1 if result["status"]=="failed" else 0)
