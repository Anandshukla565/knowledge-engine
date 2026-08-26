# Knowledge Engine Architecture Mirror

This folder is a standalone local runtime arranged around the product
architecture. The original folders remain intact and are not used by the
production wheel.

## Runtime flow

```text
Structured JSON
    -> intake confirmation / JSON adapter
    -> canonical FloorPlan model
    -> deterministic validation pipeline
    -> findings and scorecard
    -> architect report + SVG
    -> deterministic suggestions
```

Optional generation flow:

```text
Explicit requirements -> provisional planner -> deterministic validation
                      -> architect-usability gate -> review outputs
```

The deterministic validator remains authoritative. AI output is an input draft or
review aid, not an approval authority.

## Runtime boundary

- The production wheel contains the local validator, suggestions, SVG/Markdown
  reporting, a provisional planner, loopback API, and deterministic agent tools.
- It excludes legacy CLIs, legacy tests, placeholder adapters, SQLite mutation
  tooling, PDF/DXF/Revit modules, backups, rule folders, and secrets.
- No rules are approved or read for official scoring. `vastu_score` remains null.

## Capability status

Implemented and excluded boundaries are listed in `SOURCE_MAP.md`.

## Current product boundary

This runtime supports architect-facing JSON validation, deterministic suggestions,
provisional draft planning, and a loopback-only JSON API. It does not support
PDF/DXF/Revit intake, web UI, PDF rendering, layout optimization, legal
compliance, or official Vastu scoring.

## Proposed runtime layout

```text
knowledge_engine/
  apps/          application entry points
  domain/        shared floor-plan, finding, suggestion, rule, and project models
  intake/        input adapters and confirmation boundary
  planning/      provisional plan-request models and planner boundary
  validation/    deterministic geometry, requirements, practical, zone, and stacking checks
  knowledge/     read-only rule/source/trust access boundary
  suggestions/   deterministic review-aid generation
  reports/       architect-facing report and rendering boundary
  ai/            bounded model/client and tool boundary
  infrastructure/ persistence, storage, audit, and job seams
  tests/         mirror-boundary tests
```

The `planning/` boundary produces a provisional draft from explicit requirements.
Before a draft can be considered architect-usable it must pass geometry plus the
local planning gate for room-size heuristics, circulation, road-side parking
access, door data, and ventilation data. A blocked draft still writes its
assessment evidence; it is not a ready plan.

The local planner CLI can be run from the repository root:

```text
python -B -m knowledge_engine.apps.cli.planner --width 30 --depth 40 \
  --facing east --road-side east --bhk 3 --bathrooms 3 \
  --parking --pooja --out knowledge_engine/outputs/demo_30x40_east_3bhk
```

It returns `0` only for an architect-usable draft. Exit `3` means geometry may
have passed but the architect-usability gate blocked the draft; inspect
`planner_assessment.json`. Exit `2` means the requested program could not be
generated safely.

The top-level runtime dispatcher is:

```text
python -B -m knowledge_engine check
python -B -m knowledge_engine validate --input plan.json --out outputs/validated
python -B -m knowledge_engine suggest --validation-report outputs/validated/validation_report.json --scorecard outputs/validated/scorecard.json --out outputs/suggestions
python -B -m knowledge_engine plan --width 30 --depth 40 --facing east --bhk 3 --out outputs/plan
python -B -m knowledge_engine api --host 127.0.0.1 --port 8765
```

The dispatcher and API are local-only. The API accepts inline JSON only, has no
file-path endpoint, does not write SQLite, and cannot enable official scoring.

For installation outside the repository checkout, use
[README_END_USER.md](README_END_USER.md) and the local `pyproject.toml`. The
runtime package intentionally excludes SQLite, rule folders, backups, secrets,
legacy CLIs, placeholder modules, and administrative mutation scripts.
