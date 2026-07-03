import argparse
import json
from pathlib import Path

from operator_kernel.adapter_contract import normalize_adapter_payload
from operator_kernel.lane_catalog import resolve_action_id
from adapters.voice_adapter_stub import build_payload as build_voice_payload
from adapters.mobile_adapter_stub import build_payload as build_mobile_payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Quick side-by-side voice/mobile normalization and action parity probe."
    )
    parser.add_argument("--voice", required=True, help="Voice utterance")
    parser.add_argument(
        "--mobile",
        default="",
        help="Optional mobile utterance; defaults to voice utterance when omitted",
    )
    args = parser.parse_args()

    mobile_text = args.mobile if args.mobile else args.voice
    root = Path(__file__).resolve().parents[2]
    catalog = root / "config" / "lane_catalog.json"

    voice_norm = normalize_adapter_payload(build_voice_payload(args.voice))
    mobile_norm = normalize_adapter_payload(build_mobile_payload(mobile_text))

    voice_action = resolve_action_id(str(voice_norm.get("task", "")), catalog)
    mobile_action = resolve_action_id(str(mobile_norm.get("task", "")), catalog)

    print("Adapter Parity Probe")
    print(f"- voice_action: {voice_action or 'none'}")
    print(f"- mobile_action: {mobile_action or 'none'}")
    print(f"- parity: {'pass' if voice_action == mobile_action else 'fail'}")
    print("- voice_normalized:")
    print(json.dumps(voice_norm, indent=2))
    print("- mobile_normalized:")
    print(json.dumps(mobile_norm, indent=2))


if __name__ == "__main__":
    main()
