"""Shared DSh task-file rendering and admission estimates; no filesystem access."""
from __future__ import annotations

import hashlib

from .token_budget import DEFAULT_TOKEN_ESTIMATOR, MESSAGE_FRAMING_TOKEN_ALLOWANCE

DSH_FILE_TASK_SCHEMA = "NOVALIST_TASK_FILE_V1"
DSH_FILE_ACK_PREFIX = "NOVALIST_FILE_ACK:"
DSH_FILE_READ_FAILED = "NOVALIST_FILE_TASK_READ_FAILED"


def combine_prompts(system_prompt: str, user_prompt: str) -> str:
    system_prompt = (system_prompt or "").strip()
    user_prompt = (user_prompt or "").strip()
    if not system_prompt:
        return (
            "NOVALIST_TASK_START\n"
            "请立即执行下面这一个任务，并在本次回复中给出最终结果。"
            "不要停留在准备状态，也不要询问用户下一步。\n\n"
            "[输出包装说明]\n"
            "任务中的‘只输出 JSON’、‘只输出正文’或类似限制，只约束传输回执后的"
            "业务结果；必须先按任务文件传输协议输出第一行 ACK。\n\n"
            f"[用户任务]\n{user_prompt}\n"
            "NOVALIST_TASK_END"
        )
    return (
        "NOVALIST_TASK_START\n"
        "请立即执行下面这一个任务，并在本次回复中给出最终结果。"
        "不要停留在准备状态，也不要询问用户下一步。\n\n"
        "[输出包装说明]\n"
        "任务中的‘只输出 JSON’、‘只输出正文’或类似限制，只约束传输回执后的"
        "业务结果；必须先按任务文件传输协议输出第一行 ACK。\n\n"
        f"[系统约束]\n{system_prompt}\n\n"
        f"[用户任务]\n{user_prompt}\n"
        "NOVALIST_TASK_END"
    )


def inject_middle_challenge(payload: str, nonce_middle: str) -> str:
    """Place an out-of-band read challenge near the payload midpoint."""
    value = str(payload)
    midpoint = len(value) // 2
    before = value.rfind("\n", 0, midpoint)
    after = value.find("\n", midpoint)
    candidates = [index for index in (before, after) if index >= 0]
    split_at = (
        min(candidates, key=lambda index: abs(index - midpoint))
        if candidates
        else midpoint
    )
    checkpoint = (
        "\nNOVALIST_TRANSPORT_CHECKPOINT: "
        f"task_nonce_middle={nonce_middle}\n"
    )
    return value[:split_at] + checkpoint + value[split_at:]


def task_file_envelope(prompt: str, task_id: str, nonce_head: str, nonce_middle: str, nonce_tail: str) -> tuple[str, str]:
    payload = str(prompt)
    challenged_payload = inject_middle_challenge(payload, nonce_middle)
    payload_bytes = challenged_payload.encode("utf-8")
    payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    envelope = (
        f"{DSH_FILE_TASK_SCHEMA}\n"
        f"task_id: {task_id}\n"
        "encoding: UTF-8\n"
        f"payload_chars: {len(challenged_payload)}\n"
        f"payload_bytes: {len(payload_bytes)}\n"
        f"payload_sha256: {payload_sha256}\n"
        f"task_nonce_head: {nonce_head}\n\n"
        "[传输协议]\n"
        "- 必须完整读取本文件后再执行任务。\n"
        "- 任务正文中部和文件末尾还有随机校验值。最终回复第一行必须严格使用格式：\n"
        f"  {DSH_FILE_ACK_PREFIX}<task_nonce_head>:<task_nonce_middle>:<task_nonce_tail>\n"
        "- 业务任务中的‘只输出 JSON’、‘只输出正文’或类似要求，只约束回执后的"
        "业务结果；传输回执始终是第一行，业务结果始终从第二行开始。\n"
        "- NOVALIST_TRANSPORT_CHECKPOINT 仅用于传输校验，不属于任务正文。\n"
        "- 从第二行开始输出任务要求的结果，不要重复或解释传输协议。\n"
        f"- 如果无法完整读取文件，只回复 {DSH_FILE_READ_FAILED}。\n\n"
        f"{challenged_payload}\n"
        "NOVALIST_TASK_FILE_FOOTER\n"
        "再次确认：无论业务输出格式如何，第一行先输出三段 nonce 回执，"
        "第二行起再输出业务结果。\n"
        f"task_nonce_tail: {nonce_tail}"
    )
    return envelope, payload_sha256


def estimate_task_input_tokens(system_prompt: str, user_prompt: str, estimator=DEFAULT_TOKEN_ESTIMATOR) -> int:
    """Include the actual envelope, nonce checkpoint and message framing.

    Hex identities have fixed lengths, so their values do not affect the
    conservative estimator. Reuse the renderer used by the actual writer.
    """
    combined = combine_prompts(system_prompt, user_prompt)
    envelope, _ = task_file_envelope(combined, "0" * 32, "0" * 32, "0" * 32, "0" * 32)
    return max(estimator.estimate_pair(system_prompt, user_prompt),
               estimator.estimate(envelope) + MESSAGE_FRAMING_TOKEN_ALLOWANCE)
