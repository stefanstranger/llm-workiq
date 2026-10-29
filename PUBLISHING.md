---
title: Publishing llm-workiq
description: Configure PyPI credentials and publish releases of llm-workiq.
---

## Configure publishing

The [Publish to PyPI workflow](.github/workflows/publish.yml) runs on pushes
to `main`, including merged pull requests, or manually through GitHub Actions
with `main` selected. It skips versions already published on PyPI.

Configure the `pypi` GitHub environment with an environment secret named
`PYPI_API_TOKEN` containing a PyPI API token scoped to the `llm-workiq`
project. Environment approval rules can protect publishing.

## Release a version

1. Update `version` in [pyproject.toml](pyproject.toml).
2. Run `uv lock` and commit the updated [lockfile](uv.lock) alongside the
   version change. Use a package index accessible to GitHub-hosted runners.
3. Run the [development checks](README.md#development).
4. Merge into `main`. The workflow runs tests and Ruff checks, builds the
   wheel and source distribution, and validates package metadata before
   uploading to PyPI.
5. Check the workflow result and the
   [PyPI project page](https://pypi.org/project/llm-workiq/).

Published versions cannot be overwritten. To retry an unpublished version,
run the workflow manually from `main` after fixing the failure.
