import json
from pathlib import Path
from typing import Dict, List, Any


def load_signature_rules(config_path: Path) -> List[Dict[str, Any]]:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return list(data.get("rules", []))


def generate_recommendations(
    observations: List[str], rules: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    text = "\n".join(observations)
    recommendations: List[Dict[str, Any]] = []

    for rule in rules:
        matches = rule.get("match_any", [])
        if any(token in text for token in matches):
            recommendations.append(
                {
                    "rule_id": rule.get("id", "unknown"),
                    "severity": rule.get("severity", "unknown"),
                    "confidence": float(rule.get("confidence", 0.5)),
                    "recommendation": rule.get("recommendation", ""),
                    "focus_step": rule.get("focus_step", ""),
                    "doc_target": rule.get("doc_target", ""),
                }
            )

    return recommendations
