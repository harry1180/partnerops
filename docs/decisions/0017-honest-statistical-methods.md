# ADR-0017: Honest statistical methods for anomalies and forecasts

Date: 2026-09-18
Status: accepted
Phase: 4

## Context

The charter requires anomaly detection and forecasting but explicitly says:
"begin with understandable statistical methods and configurable thresholds.
Do not claim advanced machine learning where it has not been implemented."
Demo fixtures contain a planted 14x spike (Cobalt CloudFront, August) and
3 months of monthly-grain spend per (customer × service).

Naïve options fail the honesty bar: a plain σ-based z-score is poisoned by
the spike itself; "ML forecasting" would be a lie; flagging anything with a
big z on 2-month baselines fires noise (observed live: a -7.8% CloudFront
wobble scored z=-6 because the MAD denominator was tiny).

## Decision

1. Anomaly = robust month-over-month z-score: median + MAD of prior months
   (single-spike resistant), `0.6745*(obs-median)/max(MAD, floor)`, firing
   only when ALL gates pass: |z| ≥ threshold (default 4), |delta| ≥
   abs floor (default $500), AND |delta| ≥ rel floor (default 50% of
   baseline). Series with < 2 prior months are skipped, not guessed.
2. Every anomaly row persists its `method` string and full evidence
   (observed, median, MAD, prior count, thresholds) — the UI shows what was
   computed, and re-running with new config updates open rows and
   auto-resolves rows whose signal disappeared.
3. Forecast = average month-over-month growth, linear, labeled
   `avg_mom_growth_linear`; budget projection = straight-line burn with
   `projected == actual` for closed periods. Method strings are returned in
   the API, never hidden.
4. Grain honesty: the demo is monthly (fixtures emit one row per
   account/service/month), so detection runs monthly. The method is
   replaceable by a daily variant when real CUR feeds land; nothing claims
   intraday capability today.
5. Recommendations carry `basis` evidence + `confidence` label
   (low/medium/high); savings estimates cite the discount factor actually
   observed in the same book of business. Realized savings are measured
   (before/after spend around an accepted decision) and stay NULL until
   post-decision data exists — the smoke asserts the honest zero.

## Consequences

Every number on these pages is reproducible from stored evidence; users can
contest any flag by reading its basis. Thresholds are per-call config with
sane defaults, so tuning never means re-interpreting stored rows.
