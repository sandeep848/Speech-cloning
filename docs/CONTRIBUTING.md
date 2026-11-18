# Working safely across folders

Use feature branches and pull requests. Never push directly to the protected integration/release branch. The `issues-and-fixes` branch starts at main commit `3fc34cba90fa446f59e1a47e7bbf1da238bc2d41`.

## Ownership and file placement

| Responsibility | Location |
| --- | --- |
| UI components | frontend/src/components/ |
| Browser API transport | frontend/src/api/ |
| Existing application composition | frontend/src/main.tsx |
| HTTP routes and legacy orchestration | backend/backend.py (migration entry point) |
| Authentication decisions | backend/app/security/ |
| Database connection management | backend/app/db/ |
| Versioned public signatures | backend/app/contracts/ |
| Model-specific implementations | backend/models/qwen3/, cosyvoice3/, xtts_v2/ |
| Evaluation implementation | backend/evaluator.py (migration entry point) |
| Backend regressions | backend/tests/ |
| Browser regressions | frontend/tests/ |
| Workflows and owners | .github/ |
| Infrastructure | deploy/ |
| Developer automation | scripts/ |
| Technical documentation | docs/ |

Existing entry points remain supported. Do not move legacy modules without an import and startup migration. The structure check covers UI placement and dependency boundaries of extracted modules; it cannot infer every file's business responsibility.

## Public functions

Change implementations and add private helpers freely, provided behavioral tests pass. Preserve public function names, parameter order, names, defaults, annotations, sync/async behavior and request fields. `scripts/check_contracts.py` validates 68 existing public definitions; this is a compatibility guard, not a security sandbox. Python cannot stop someone editing a signature in their own checkout. Reviewed branch rules provide enforcement at merge time.

An intentional breaking change needs a migration, caller updates, tests, owner approval and an explicit baseline update (`python scripts/check_contracts.py --write`). Do not regenerate the baseline just to make a failed check green. Response behavior also requires integration tests; signature checks alone cannot guarantee it.

Keep compatibility parameters even if currently unused. Model adapters consume re-exported pacing helpers: apparent unused imports can be public dependencies. Do not delete a helper based only on static scanner output.

## Checks

The coordinator invokes separate backend, frontend, models, contracts and security workflows on every push, PR and merge-group event. All components run deliberately: a frontend-only edit can still break integration. This avoids path-filtered required checks remaining pending or shared changes silently missing checks. Folder owners can run their component commands independently. There is no need for a workflow per leaf folder.

Run `python -m pytest backend/tests -q`, `python scripts/check_contracts.py`, `python scripts/check_structure.py`, and in frontend run `npm ci`, `npm run build`, `npm run test:responsive`, `npm audit --audit-level=moderate`.

## GitHub settings required

CODEOWNERS and workflows alone do not restrict pushes. A repository administrator must configure a ruleset for the integration branch and main: require PRs, code-owner reviews, dismiss stale approvals, require approval after the latest push, block force pushes/deletion, require the **Final checks** status, and limit bypass permissions. Select the exact status reported by the first successful run. No repository settings were changed by this branch.

Protect `.github/`, `scripts/`, contract baselines, security, database and deployment files with owner review. Currently the owner is @sandeep848 for every directory; add actual collaborators as they join. A single owner cannot independently approve their own PR; add another maintainer if code-owner review is mandatory.

Use Git commits and PRs as the author/reviewer/timestamp audit trail. Actions preserves test artifacts and per-commit provenance. These are subject to GitHub retention and administrator access, not an immutable compliance audit system. Never commit credentials or user audio.
