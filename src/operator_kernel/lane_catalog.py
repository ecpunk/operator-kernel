import json
import re
from pathlib import Path
from typing import Dict, Any


def _is_whole_word_match(task_lowered: str, keyword_lowered: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(keyword_lowered)}(?![a-z0-9])"
    return re.search(pattern, task_lowered) is not None


def _first_match_index(task_lowered: str, keyword_lowered: str, whole_word: bool) -> int:
    if whole_word:
        pattern = rf"(?<![a-z0-9]){re.escape(keyword_lowered)}(?![a-z0-9])"
        match = re.search(pattern, task_lowered)
        if match:
            return int(match.start())
    idx = task_lowered.find(keyword_lowered)
    return idx if idx >= 0 else 10**9


def resolve_action_id(task: str, catalog_path: Path) -> str:
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    lowered = task.lower()
    policy = data.get("resolution_policy", {})
    prefer_whole_word = bool(policy.get("prefer_whole_word", True))
    prefer_earliest_mention = bool(policy.get("prefer_earliest_mention", True))
    prefer_longest_keyword = bool(policy.get("prefer_longest_keyword", True))
    use_mapping_priority = bool(policy.get("use_mapping_priority", True))

    candidates = []
    for idx, mapping in enumerate(data.get("mappings", [])):
        action_id = str(mapping.get("action_id", "")).strip()
        if not action_id:
            continue

        mapping_priority = int(mapping.get("priority", 0)) if use_mapping_priority else 0
        for keyword in mapping.get("keywords", []):
            key = str(keyword).strip().lower()
            if not key:
                continue
            if key in lowered:
                whole_word = 1 if _is_whole_word_match(lowered, key) else 0
                mention_index = _first_match_index(lowered, key, whole_word=bool(whole_word))
                key_len = len(key)
                score = (
                    mapping_priority,
                    whole_word if prefer_whole_word else 0,
                    -mention_index if prefer_earliest_mention else 0,
                    key_len if prefer_longest_keyword else 0,
                    -idx,
                )
                candidates.append((score, action_id))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    return ""


def load_lane_spec(action_id: str, lanes_dir: Path) -> Dict[str, Any]:
    spec_path = lanes_dir / f"{action_id}.json"
    if not spec_path.exists():
        raise FileNotFoundError(f"lane spec not found: {spec_path}")
    return json.loads(spec_path.read_text(encoding="utf-8"))
