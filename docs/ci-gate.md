# Pull request CI gate

Every pull request targeting `main` runs all eleven existing reusable verification workflows. The final job, **Mallbyte CI Gate**, succeeds only if every dependency reports success; failure, cancellation and skipped jobs fail closed. There are no path filters on the gate, so documentation-only changes receive the same check. This deliberately trades extra CI time for predictable branch protection.

The component workflows retain their existing push, manual, scheduled and reusable triggers; the standalone pull-request triggers are replaced by the central gate to avoid duplicate runs. The production release workflow remains unchanged and cannot be triggered by this gate. No deployment secrets are passed to the reusable workflows.

## Enable branch protection

1. Open a PR targeting main and confirm that all eleven jobs and the final gate succeed on its latest commit.
2. Merge the CI gate PR after manual review.
3. In Settings > Rules > Rulesets > Protect Main Branch, enable **Require status checks to pass**. Select the exact emitted **Mallbyte CI Gate** check from GitHub Actions (not the individual path-filtered jobs). Require branches to be up to date before merging for strict protection.
4. Verify on a new PR. Do not enable the check before the workflow is available on main and GitHub recognizes its check name.

Existing PRs may need to update their branch or receive a new commit before the gate is emitted. Check all existing PRs before enabling the required status. The ruleset should target only the default branch; no bypass is necessary for a solo maintainer with zero required reviews.

## Troubleshooting

Inspect failed reusable jobs in the PR Actions run. The final gate prints each dependency result and never treats skipped or cancelled jobs as success. Fix the cause and rerun failed jobs or push a new commit. A superseded run is cancelled by the concurrency policy. Keep the gate dependency list and expected job count synchronized when adding or removing verification workflows.

This workflow validates pull requests only. It does not deploy, seed a database, or modify any host configuration. Existing main-branch push and release verification workflows remain available.
