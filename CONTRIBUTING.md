# Contributing

## Branches
- `develop` is the integration branch. Every change goes in through a PR from a
  `feature/LT-<n>-<short-name>` branch, and CI must be green before merging.
- `main` only receives merges from `develop`, through a PR with green CI.

## Releasing
1. Bump `version` in `pyproject.toml` on `develop`.
2. Merge `develop` → `main` with a PR and wait for green CI.
3. Tag **only commits on `main`**, and only after CI passes on that commit:
   `git tag -a vX.Y.Z -m "logtldr X.Y.Z" && git push origin vX.Y.Z`
4. Wait for the tag's CI run to go green, then publish to PyPI.
5. Create the GitHub release with notes: `gh release create vX.Y.Z --notes-file RELEASE_NOTES.md`

Never move or reuse a tag after a version is on PyPI. Release a new patch version instead.