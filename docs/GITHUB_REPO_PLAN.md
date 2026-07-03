# GitHub Repository Publishing Plan

Status: Ready to execute when you choose
Last updated: 2026-03-07

## Goal

Publish Operator Kernel as a clean GitHub repository without leaking local-only artifacts, secrets, or environment-specific runtime noise.

## Recommended Repository Name

operator-kernel

## Recommended Visibility

Start private. Switch to public after review if desired.

## Pre-Publish Checklist

1) Secrets and credentials review
- Confirm no credentials are committed.
- Verify dashboard or runtime files do not include secrets.
- Ensure any local env files remain untracked.

2) Artifact hygiene
- Exclude generated artifacts that do not belong in source control.
- Keep only intentional example artifacts if needed.

3) Ignore rules
- Confirm .gitignore covers transient outputs: artifacts snapshots, logs, caches, and runtime temp files.

4) Docs baseline
- Keep these core docs clear and current:
  - README.md
  - ROADMAP.md
  - 10-CONCEPT/OPERATOR_KERNEL_CONCEPT.md
  - docs/END_USER_GUIDE.md
  - docs/END_USER_QUICKSTART.md

5) License and contribution files
- Add LICENSE
- Add CONTRIBUTING.md (optional now, recommended soon)
- Add CODEOWNERS (optional)

## Suggested Initial Repo Layout

Top-level tracked content should prioritize:
- src/
- config/
- systemd/
- 10-CONCEPT/
- docs/
- README.md
- ROADMAP.md
- TESTING_PHASE_PLAN.md

Generated runtime outputs should usually stay untracked.

## Practical Publish Steps

1) Initialize and commit locally if needed:
- git init
- git add .
- git commit -m "Initial Operator Kernel import"

2) Create remote repository on GitHub (private).

3) Add remote and push:
- git remote add origin git@github.com:<org-or-user>/operator-kernel.git
- git branch -M main
- git push -u origin main

4) Enable branch protection for main.

5) Add CI checks for:
- stage3_bootstrap_tests --strict
- broad_spectrum_tests_batch2
- adapter_contract_tests --strict

## Recommended First Milestone After Publish

Milestone name: Stage 3 Stability And Modality Parity

Include:
- complete real voice/mobile adapter ingress path
- retain deterministic parity tests
- complete soak evidence closeout
- freeze v1.0 end-user documentation wording

## Notes For This Workspace

This workspace appears to include many sibling projects under /opt/stack/services. Use a dedicated repository rooted at /opt/stack/services/projects/operator-kernel to avoid cross-project pollution.
