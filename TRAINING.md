# Continuous training loop — Qwen3-8B receptionist

**Separation of concerns (never violated):**

```text
Fine-tuning  -> behavior / style / patterns        (how the agent talks)
Dialogue policy JSON -> scripts and wording        (what the agent says, editable)
Database     -> live business state                (prices, hours, rules — NEVER in weights)
Tools        -> real-world actions                 (bookings, consent, dedup — in code)
```

The fine-tune must never memorize prices, hours, or rules: they change in
`data/clinic.json` and must reach the model through the system prompt, not
through frozen weights.

## The loop (monthly, or after every ~200 new conversations)

```text
1. EXPORT    .venv/Scripts/python.exe scripts/export_finetune.py
             -> training/dataset_policy-vN.jsonl  (filtered, PII-masked, policy-stamped)
2. TRAIN     training/colab_qlora_qwen3_8b.ipynb on free Colab T4 (~1-3 h for a few hundred examples)
             -> LoRA adapter pushed to a private HF repo per policy version
3. EVAL      serve the adapter (Ollama GGUF or an OpenAI-compatible host),
             point .env at it, run scripts/eval_battery.py
             GATE: 0 FAIL — including Hindi/Hinglish asks and the consent flow
4. PROMOTE   keep the .env override only if the gate passes; Gemini stays the
             two-line rollback. Name adapters policy-vN so behavior and data match.
```

## Why this design

- **Data you already collect**: every conversation + tool call (args, result,
  success, latency) is persisted by the store — the export script only filters
  and formats. Eval battery runs count toward the dataset too.
- **Filter, don't teach failure**: only conversations with successful tool calls
  and no error replies are exported. The fine-tune learns the good paths.
- **Policy versioning**: the system prompt inside each training example carries
  `dialogue policy vN`, so adapters stay paired with the exact wording they
  learned from. Change the policy -> bump `version` -> re-export -> retrain.
- **Promotion is a config change**: the agent stack is provider-agnostic —
  `LLM_BASE_URL` + `LLM_MODEL` are the whole deployment of a new brain.

## Serving the fine-tuned model (free options)

| Option | How | Notes |
|---|---|---|
| Ollama (local, CPU) | export GGUF in the notebook, `ollama create receptionist`, point `.env` at `http://localhost:11434/v1` | slow but fully offline; fine for evals |
| Groq free tier | host Qwen, point `.env` at Groq | fast, rate-limited |
| HF Inference endpoints | paid per hour | production-grade, only when needed |

## Success criteria before ANY promotion

- Eval battery: **0 FAIL** (18 scenarios today — expand toward 60 before relying on it)
- Hindi/Hinglish asks spoken correctly (policy scripts followed)
- Propose/confirm flow intact: the backend gates hold regardless of the model,
  but the model must not fight them (battery's S3b/S10 measure exactly this)
