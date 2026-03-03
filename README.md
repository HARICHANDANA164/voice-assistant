# Clara Answers Intern Assignment - Zero-Cost Automation Pipeline

This repository provides a **fully local, zero-cost, reproducible** automation pipeline for:

1. **Demo call transcript -> Preliminary Retell Agent Draft (v1)**
2. **Onboarding transcript/form -> Updated Agent Draft (v2 + changelog)**

No paid APIs are required. The implementation is rule-based and runs offline with Python standard library only.

---

## Architecture

```text
Input dataset/
  demo/*.txt|json
  onboarding/*.txt|json
      |
      v
scripts/process_calls.py
  - normalize account_id
  - extract structured memo fields (rule-based)
  - generate Retell Agent Draft Spec
  - write versioned artifacts (v1/v2)
  - compute diffs/changelog
  - update task tracker
      |
      v
outputs/accounts/<account_id>/v1 and v2
tracker/tasks.json
```

### Pipeline A (Demo -> v1)
- Extract `account_memo.json`
- Generate `retell_agent_spec.json` version `v1`
- Save metadata (`source_file`, hash, timestamp)
- Create/update task item in `tracker/tasks.json`

### Pipeline B (Onboarding -> v2)
- Load v1 memo (if present)
- Extract onboarding updates
- Merge updates into v2 memo
- Regenerate v2 Retell Agent Draft Spec
- Generate `changes.json` and `changes.md`
- Update tracker status to onboarding processed

---

## Repository Structure

- `scripts/process_calls.py` - main automation engine
- `workflows/n8n_clara_pipeline.json` - n8n workflow export (Webhook -> ExecuteCommand)
- `outputs/accounts/<account_id>/v1` and `v2` - generated artifacts
- `tracker/tasks.json` - free task-tracker substitute (Asana alternative)
- `dataset/demo` + `dataset/onboarding` - input transcripts (sample included)
- `docker-compose.yml` - local n8n launcher (optional)

---

## Required Output Mapping

### 1) Account Memo JSON
Generated at:
- `outputs/accounts/<account_id>/v1/account_memo.json`
- `outputs/accounts/<account_id>/v2/account_memo.json`

Contains required fields:
- `account_id`
- `company_name`
- `business_hours` (days, start, end, timezone)
- `office_address`
- `services_supported`
- `emergency_definition`
- `emergency_routing_rules`
- `non_emergency_routing_rules`
- `call_transfer_rules`
- `integration_constraints`
- `after_hours_flow_summary`
- `office_hours_flow_summary`
- `questions_or_unknowns`
- `notes`

### 2) Retell Agent Draft Spec
Generated at:
- `outputs/accounts/<account_id>/v1/retell_agent_spec.json`
- `outputs/accounts/<account_id>/v2/retell_agent_spec.json`

Contains:
- `agent_name`
- `voice_style`
- `system_prompt`
- `key_variables`
- `tool_invocation_placeholders`
- `call_transfer_protocol`
- `fallback_protocol_if_transfer_fails`
- `version`

### 3) Versioning + Diff
Generated for onboarding updates:
- `outputs/accounts/<account_id>/v2/changes.json`
- `outputs/accounts/<account_id>/v2/changes.md`

### 4) Orchestrator Export
- n8n export: `workflows/n8n_clara_pipeline.json`

### 5) README
- This file.

---

## Run Locally

### Prerequisites
- Python 3.10+

### Batch run (all demo + onboarding)
```bash
python3 scripts/process_calls.py --input-root dataset --output-root outputs --tracker-path tracker/tasks.json
```

### Idempotency behavior
- Re-running does not create duplicate versions; it rewrites deterministic file paths.
- Tracker is upserted by `account_id`.

---

## n8n Setup (Preferred Orchestrator)

### Option A: Docker compose
```bash
docker compose up -d
```
Open n8n at `http://localhost:5678`.

### Import workflow
1. In n8n, import `workflows/n8n_clara_pipeline.json`.
2. Activate workflow.
3. Trigger webhook `POST /webhook/clara-pipeline` with optional JSON:
   ```json
   {
     "input_root": "dataset",
     "output_root": "outputs",
     "tracker_path": "tracker/tasks.json"
   }
   ```

---

## Retell Setup Notes (Free-tier safe)

If Retell free tier allows API creation in your account:
- Map `retell_agent_spec.json` fields to Retell API request.

If Retell API is not available on free tier:
- Use manual import from `retell_agent_spec.json` into the Retell UI:
  1. Create a new agent.
  2. Copy `agent_name`, `system_prompt`, and transfer/fallback settings.
  3. Configure business-hour and emergency routing variables from `key_variables`.

This repository intentionally avoids paid API assumptions.

---

## Input Expectations

Use this naming format for easy pairing:
- `dataset/demo/<account_id>__demo.txt`
- `dataset/onboarding/<account_id>__onboarding.txt`

Accepted formats:
- `.txt`, `.md`, `.json`
- For JSON, transcript text can live under `transcript`, `text`, `content`, or `notes`.

---

## Known Limitations

- Rule-based extraction is less robust than a tuned LLM extractor.
- Transcript formatting quality strongly affects extraction quality.
- Audio transcription is not included; this pipeline expects transcript input.

---

## Production Improvements

- Add local Whisper transcription step for audio ingestion.
- Add confidence scoring and human review queue.
- Add richer entity resolution for phone trees and multi-branch routing.
- Add a lightweight UI for side-by-side v1/v2 diff review.
- Add automated tests over a larger transcript fixture set.

