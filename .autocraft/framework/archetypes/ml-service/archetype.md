# Archetype: ml-service

## Summary
A system that trains and/or serves a machine-learning model (classification, prediction, recommendation, vision), as opposed to calling a ready-made LLM.

## In plain words
"I want the computer to learn from examples and predict something." The risky parts are the data, knowing whether the model is *actually good*, and noticing when it quietly gets worse.

## Typical products
Fraud/risk scores, recommendations, demand forecasts, image classifiers, anomaly detection.

## Questions the requirements-analyst must ask
1. What decision/prediction, and what does a wrong answer cost? (default: ask)
2. Do you have labeled data? How much, how clean, who owns it? (default: ask; if none, plan a labeling step)
3. Is a simple rule or existing model good enough? (default: try a baseline first)
4. Real-time predictions or batch? Latency needs? (default: batch/daily)
5. Fairness/privacy concerns? (default: yes if it affects people)

## Architecture must cover
Problem framing and **baseline**, data sourcing/versioning/splits (no leakage), feature pipeline shared between training and serving, training pipeline (reproducible, seeded, tracked experiments), **evaluation metrics and acceptance thresholds**, model registry and versioning, serving (batch/online), monitoring for **drift** and performance, human override/fallback, retraining triggers, bias/fairness checks, cost.

## Testing strategy
- **Unit:** feature code and data validation.
- **Data tests:** schema, ranges, no train/test leakage.
- **Model tests:** metrics above threshold on a held-out set, per-segment performance, behavior on edge cases, determinism with seed.
- **Integration:** serving endpoint/batch job returns the right shape; training→register→serve flow on tiny data.
- **Regression:** new model must not be worse than the current one on the benchmark.

## Walking skeleton (task 1)
Baseline model trained on a small sample by a reproducible command, evaluated, registered, and served by a stub endpoint/batch job; CI runs it on sample data.

## CI & tooling
Lint, type-check, unit + data tests, tiny end-to-end training run, model-metrics gate, dependency audit; experiment tracker (MLflow-style).

## Where it runs (simple defaults)
Training: notebook/job on managed compute (or a laptop for small data). Serving: container on a managed platform, or batch job writing predictions to a table.

## Security focus
Training-data privacy and consent, model/data access control, poisoning and adversarial inputs, unsafe deserialization of model files (pickle), PII in features and logs.

## Release checklist additions
Metrics beat baseline and meet thresholds; model card (purpose, data, limits, known biases); monitoring + drift alerts; fallback if model fails; rollback = previous model version; human review path for high-impact decisions.

## Common pitfalls (reviewers look for these)
Data leakage; evaluating on training data; no baseline; training/serving feature skew; non-reproducible runs; no monitoring after launch; unversioned data/models; pickle from untrusted sources.

## Suggested stacks
Python (scikit-learn, PyTorch, XGBoost) → `python`.
