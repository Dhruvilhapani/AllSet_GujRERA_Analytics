# Analytical Risk Score — methodology

> **Analytical Risk Score — not an official RERA rating.** It is a transparent heuristic computed by this pipeline
> from public GujRERA data. It does not express any legal finding about any promoter or project.

All weights and parameters live in `config/risk_scoring.yaml`; change them and re-run `python cli.py analyze`.

## Components (each 0 = no concern … 100 = maximum concern)

| Component | Formula (defaults) | Inputs / source |
|---|---|---|
| **Delay** | min(100, days past latest approved completion ÷ 730 × 100). For completed projects the days are measured at the Q-E filing date. | `current_completion_date`, `completion_filing_date`, run date |
| **Regulatory** | min(100, 20 × suo-motu proceedings + 30 × (orders with penalty + challans)) | `regulatory_actions`, complaint orders with penalty flag |
| **Complaints** | min(100, 20 × resolved-or-unknown + 30 × pending complaints) | `complaints` linked to the project |
| **Completion** (schedule gap) | min(100, max(0, timeline elapsed % − physical progress %) ÷ 50 × 100); 0 when completed | QPR progress, start / approved completion dates |
| **Extensions** | min(100, 34 × approved extensions) | `extension_count` |
| **Stagnation** | 100 if stalled; else (months since last QPR − 3) ÷ (9 − 3) × 100, clipped; 0 when completed | QPR history / calendar |
| **Inventory** | unsold % (× 0.5 while physical progress < 75 %) | block booking chart — **weight 0 by default**, reported separately |

Default weights: delay 25 %, regulatory 20 %, complaints 20 %, completion 15 %, extensions 10 %, stagnation 10 %.

## Score

`risk_score = Σ(weight × component) ÷ Σ(weights of components with data)`

Components without data (e.g. no QPR yet, or project detail not crawled) are **excluded**, not treated as zero;
`score_coverage` = share of the total weight that had data. Compare scores only when coverage is similar.

## Developer score

Units-weighted mean of the developer's project components (equal weights when units are unknown), combined with
the same weights. `flag_counts` shows how many of the developer's projects carry each flag.

## Evidence-backed flags (high-risk project list)

| Flag | Condition (defaults) | Evidence string shows |
|---|---|---|
| `severe_delay` | not completed and ≥ 365 days past latest approved completion | days late, the date, absence of Q-E filing |
| `repeated_extension` | ≥ 2 extensions | count, original → current date |
| `low_construction_progress` | progress < 25 % with ≥ 50 % of timeline elapsed | progress %, quarter, elapsed % |
| `high_complaints` | ≥ 3 linked complaints | count, pending count |
| `regulatory_action` | any suo-motu proceeding, penalty order or challan | counts and challan amount |
| `stalled_progress` | stalled flag | latest progress, months since QPR, velocity |
| `high_unsold_inventory` | ≥ 50 % unbooked at ≥ 75 % progress (or completed) | unbooked / total units, progress |

## Caveats

* Booked/unbooked figures are self-reported by promoters through QPRs.
* Complaint and suo-motu counts depend on project linkage (registration serial / project application list);
  unlinked matters are not attributed to anyone.
* Suo-motu proceedings for non-filing of QPR/annual reports (NCQR/NCAR) are compliance matters, not findings on
  construction quality or fraud.
* Delay is measured against approved dates; legitimately approved extensions still count in the extension
  component but not as delay.
