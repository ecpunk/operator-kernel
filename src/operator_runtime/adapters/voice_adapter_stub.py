import json
from datetime import datetime, timezone


def _clean_utterance(raw: str) -> str:
    return " ".join(str(raw).split()).strip()


def build_payload(utterance: str, device_id: str = "voice-stub") -> dict:
    return {
        "adapter": "voice",
        "channel": "voice",
        "utterance": _clean_utterance(utterance),
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "device_id": device_id,
        "metadata": {"source": "voice_adapter_stub"},
    }


if __name__ == "__main__":
    print(json.dumps(build_payload("Plex playback failing on LAN"), indent=2))
