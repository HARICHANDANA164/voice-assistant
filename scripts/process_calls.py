#!/usr/bin/env python3
"""Zero-cost transcript pipeline for Clara assignment.

Pipeline A: demo transcript -> v1 memo + v1 agent spec + tracker item
Pipeline B: onboarding transcript/form -> v2 memo + v2 agent spec + changelog
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_TIMEZONE = "America/Chicago"


@dataclass
class CallDocument:
    account_id: str
    source_type: str  # demo|onboarding
    content: str
    source_path: Path


def load_text_or_json(path: Path) -> str:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key in ("transcript", "text", "content", "notes"):
                if key in data and data[key]:
                    return str(data[key])
        return json.dumps(data, ensure_ascii=False)
    return path.read_text(encoding="utf-8")


def normalize_account_id(filename: str) -> str:
    base = Path(filename).stem.lower()
    for marker in ("__demo", "__onboarding", "_demo", "_onboarding", "-demo", "-onboarding"):
        if marker in base:
            base = base.split(marker)[0]
            break
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base or "unknown-account"


def ingest_documents(root: Path) -> list[CallDocument]:
    docs: list[CallDocument] = []
    for source_type in ("demo", "onboarding"):
        source_dir = root / source_type
        if not source_dir.exists():
            continue
        for path in sorted(source_dir.iterdir()):
            if path.is_file() and path.suffix.lower() in {".txt", ".md", ".json"}:
                docs.append(
                    CallDocument(
                        account_id=normalize_account_id(path.name),
                        source_type=source_type,
                        content=load_text_or_json(path),
                        source_path=path,
                    )
                )
    return docs


def _find_first(text: str, patterns: list[str], flags: int = re.IGNORECASE) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return match.group(1).strip(" .")
    return None


def _split_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    chunks = re.split(r"[,;/]|\band\b", raw, flags=re.IGNORECASE)
    return [c.strip(" .") for c in chunks if c.strip()]


def extract_structured_fields(text: str, account_id: str) -> dict[str, Any]:
    company_name = _find_first(text, [r"(?:company|business|client)\s*(?:name)?\s*[:\-]\s*([^\n]+)"])
    if not company_name:
        company_name = _find_first(text, [r"this is\s+([A-Z][A-Za-z0-9&\-\s]{2,})"], flags=0)

    timezone_value = _find_first(text, [r"(?:timezone|time zone)\s*[:\-]\s*([^\n]+)"]) or DEFAULT_TIMEZONE
    hours_raw = _find_first(text, [r"(?:business|office)\s*hours\s*[:\-]\s*([^\n]+)"])

    business_hours = {
        "days": "Mon-Fri",
        "start": "08:00",
        "end": "17:00",
        "timezone": timezone_value,
        "source": hours_raw or "default",
    }
    if hours_raw:
        span = re.search(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(?:to|\-|–)\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", hours_raw, re.I)
        if span:
            business_hours["start"] = span.group(1)
            business_hours["end"] = span.group(2)

    office_address = _find_first(text, [r"(?:address|office)\s*[:\-]\s*([^\n]+)"])

    services = _split_list(_find_first(text, [r"(?:services|service lines?)\s*[:\-]\s*([^\n]+)"]))

    emergency_triggers = _split_list(
        _find_first(
            text,
            [
                r"(?:emergenc(?:y|ies)|urgent triggers?)\s*[:\-]\s*([^\n]+)",
                r"emergency means\s*([^\n]+)",
            ],
        )
    )

    routing_primary = _find_first(text, [r"(?:primary contact|route to|dispatch to)\s*[:\-]\s*([^\n]+)"])
    routing_fallback = _find_first(text, [r"(?:fallback|backup contact)\s*[:\-]\s*([^\n]+)"])

    non_emergency = _find_first(text, [r"(?:non\s*emergency routing|standard routing)\s*[:\-]\s*([^\n]+)"])

    integration_constraints = _split_list(
        _find_first(text, [r"(?:integration constraints?|never do)\s*[:\-]\s*([^\n]+)"])
    )

    transfer_timeout = _find_first(text, [r"(?:transfer timeout|timeout)\s*[:\-]\s*(\d+\s*seconds?)"])
    retries = _find_first(text, [r"(?:retries?|attempts?)\s*[:\-]\s*(\d+)"])

    unknowns: list[str] = []
    for k, v in {
        "company_name": company_name,
        "office_address": office_address,
        "services_supported": services,
        "emergency_definition": emergency_triggers,
    }.items():
        if not v:
            unknowns.append(f"Missing {k}")

    memo = {
        "account_id": account_id,
        "company_name": company_name or "",
        "business_hours": {
            "days": business_hours["days"],
            "start": business_hours["start"],
            "end": business_hours["end"],
            "timezone": business_hours["timezone"],
        },
        "office_address": office_address or "",
        "services_supported": services,
        "emergency_definition": emergency_triggers,
        "emergency_routing_rules": {
            "order": [x for x in [routing_primary, routing_fallback] if x],
            "fallback": routing_fallback or "Take message and page on-call technician",
        },
        "non_emergency_routing_rules": non_emergency
        or "Collect caller details and schedule callback during business hours.",
        "call_transfer_rules": {
            "timeout": transfer_timeout or "30 seconds",
            "retries": int(retries) if retries and retries.isdigit() else 2,
            "transfer_fail_message": "I'm unable to connect you right now; I'll send this as urgent and arrange a quick callback.",
        },
        "integration_constraints": integration_constraints,
        "after_hours_flow_summary": "Greet, identify urgency, collect name/number/address for emergencies, transfer to on-call, fallback to urgent callback.",
        "office_hours_flow_summary": "Greet, capture reason for call, collect minimal contact info, transfer/route, confirm next steps, close politely.",
        "questions_or_unknowns": unknowns,
        "notes": f"Auto-extracted from transcript on {datetime.now(timezone.utc).isoformat()}",
    }
    return memo


def build_agent_spec(memo: dict[str, Any], version: str) -> dict[str, Any]:
    company = memo.get("company_name") or memo["account_id"]
    emergency_order = ", ".join(memo["emergency_routing_rules"]["order"]) or "on-call rotation"
    bh = memo["business_hours"]

    system_prompt = (
        f"You are the voice assistant for {company}.\n"
        "Goals: resolve and route calls with minimal questions, prioritize emergencies, and maintain a calm tone.\n"
        f"Business hours: {bh['days']} {bh['start']} to {bh['end']} ({bh['timezone']}).\n"
        "Office-hours flow: greeting, purpose, collect caller name and callback number, route/transfer appropriately, if transfer fails provide fallback assurance, confirm next steps, ask 'anything else?', close.\n"
        "After-hours flow: greeting, purpose, confirm if emergency, for emergencies collect name, callback number, and service address immediately, transfer to on-call, if transfer fails promise urgent callback and summarize.\n"
        f"Emergency routing order: {emergency_order}.\n"
        "Never mention internal tools/function calls to callers. Ask only required dispatch details."
    )

    return {
        "agent_name": f"{company} - Dispatch Assistant",
        "voice_style": "Warm, concise, reassuring",
        "system_prompt": system_prompt,
        "key_variables": {
            "timezone": bh["timezone"],
            "business_hours": bh,
            "office_address": memo.get("office_address", ""),
            "emergency_routing": memo["emergency_routing_rules"],
        },
        "tool_invocation_placeholders": [
            "lookup_customer_account",
            "create_dispatch_ticket",
            "handoff_to_on_call",
        ],
        "call_transfer_protocol": {
            "steps": [
                "Announce transfer",
                "Attempt transfer to primary target",
                "Retry according to retries",
                "If still unavailable, execute fallback protocol",
            ]
        },
        "fallback_protocol_if_transfer_fails": memo["call_transfer_rules"]["transfer_fail_message"],
        "version": version,
    }


def dict_diff(old: Any, new: Any, path: str = "") -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    if isinstance(old, dict) and isinstance(new, dict):
        keys = set(old) | set(new)
        for key in sorted(keys):
            next_path = f"{path}.{key}" if path else key
            if key not in old:
                diffs.append({"field": next_path, "old": None, "new": new[key], "reason": "added in onboarding"})
            elif key not in new:
                diffs.append({"field": next_path, "old": old[key], "new": None, "reason": "removed in onboarding"})
            else:
                diffs.extend(dict_diff(old[key], new[key], next_path))
        return diffs
    if old != new:
        diffs.append({"field": path, "old": old, "new": new, "reason": "updated from onboarding input"})
    return diffs


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def stable_hash(data: Any) -> str:
    packed = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def upsert_tracker_item(tracker_path: Path, account_id: str, version: str, status: str) -> None:
    tracker = load_json(tracker_path, {"items": []})
    found = None
    for item in tracker["items"]:
        if item["account_id"] == account_id:
            found = item
            break
    if not found:
        found = {"account_id": account_id, "latest_version": version, "status": status, "history": []}
        tracker["items"].append(found)
    found["latest_version"] = version
    found["status"] = status
    found["history"].append({"version": version, "status": status, "at": datetime.now(timezone.utc).isoformat()})
    save_json(tracker_path, tracker)


def process_demo(doc: CallDocument, output_root: Path, tracker_path: Path) -> None:
    memo = extract_structured_fields(doc.content, doc.account_id)
    agent = build_agent_spec(memo, "v1")
    out_dir = output_root / "accounts" / doc.account_id / "v1"
    save_json(out_dir / "account_memo.json", memo)
    save_json(out_dir / "retell_agent_spec.json", agent)
    save_json(out_dir / "metadata.json", {
        "source_file": str(doc.source_path),
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "source_type": doc.source_type,
        "content_hash": stable_hash(doc.content),
    })
    upsert_tracker_item(tracker_path, doc.account_id, "v1", "demo_processed")


def process_onboarding(doc: CallDocument, output_root: Path, tracker_path: Path) -> None:
    v1_memo_path = output_root / "accounts" / doc.account_id / "v1" / "account_memo.json"
    if not v1_memo_path.exists():
        # safe fallback: bootstrap from onboarding only
        base_memo = extract_structured_fields(doc.content, doc.account_id)
    else:
        base_memo = load_json(v1_memo_path, {})

    updates = extract_structured_fields(doc.content, doc.account_id)
    merged = copy.deepcopy(base_memo)
    for key, value in updates.items():
        if key in {"account_id", "notes"}:
            continue
        if isinstance(value, list) and value:
            merged[key] = sorted(set((merged.get(key) or []) + value))
        elif isinstance(value, dict):
            merged.setdefault(key, {})
            for subk, subv in value.items():
                if subv:
                    merged[key][subk] = subv
        elif value:
            merged[key] = value

    merged["notes"] = f"Updated from onboarding on {datetime.now(timezone.utc).isoformat()}"
    merged["questions_or_unknowns"] = sorted(set(merged.get("questions_or_unknowns", [])))

    diffs = dict_diff(base_memo, merged)
    out_dir = output_root / "accounts" / doc.account_id / "v2"
    save_json(out_dir / "account_memo.json", merged)
    save_json(out_dir / "retell_agent_spec.json", build_agent_spec(merged, "v2"))
    save_json(out_dir / "changes.json", diffs)
    md_lines = [f"# Changes for {doc.account_id} (v1 -> v2)", ""]
    if not diffs:
        md_lines.append("No material changes detected.")
    else:
        for d in diffs:
            md_lines.append(f"- `{d['field']}`: `{d['old']}` -> `{d['new']}` ({d['reason']})")
    (out_dir / "changes.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    upsert_tracker_item(tracker_path, doc.account_id, "v2", "onboarding_processed")


def run(root: Path, output_root: Path, tracker_path: Path) -> None:
    docs = ingest_documents(root)
    demos = [d for d in docs if d.source_type == "demo"]
    onboardings = [d for d in docs if d.source_type == "onboarding"]

    for doc in demos:
        process_demo(doc, output_root, tracker_path)
    for doc in onboardings:
        process_onboarding(doc, output_root, tracker_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Clara zero-cost automation pipeline")
    parser.add_argument("--input-root", default="dataset", help="Folder containing demo/ and onboarding/")
    parser.add_argument("--output-root", default="outputs", help="Output root directory")
    parser.add_argument("--tracker-path", default="tracker/tasks.json", help="JSON tracker path")
    args = parser.parse_args()

    run(Path(args.input_root), Path(args.output_root), Path(args.tracker_path))


if __name__ == "__main__":
    main()
