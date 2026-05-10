---
name: release
description: Cut a new ghstack release to PyPI
user_invocable: true
---

# Release ghstack

## Steps

1. **Determine the new version number.** Check the current version in `pyproject.toml` and ask the user what the new version should be (patch, minor, or major bump).

2. **Bump the version** in `pyproject.toml` (`version = "X.Y.Z"`).

3. **Update `uv.lock`** by running `uv lock` so the lockfile reflects the new version.

4. **Commit** on `main` with message "Release X.Y.Z".

5. **Push main** and **tag the release**:
   ```
   git push origin main
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

6. The `publish.yml` GitHub Actions workflow triggers automatically on `v*` tags. It verifies the tag matches `pyproject.toml`, builds with `uv build`, and publishes to PyPI via trusted publishing.
