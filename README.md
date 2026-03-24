# Dependency License Check Action

This repository now provides a GitHub Action that installs a Python package and checks the license metadata exposed by every newly installed dependency.

## Inputs

- `package` - required pip package specifier or local path to install.
- `python-version` - optional Python version, defaults to `3.11`.
- `fail-on-missing-license` - fail when a dependency does not expose license metadata, defaults to `true`.
- `allowed-licenses` - optional comma-separated or newline-separated allow list.
- `disallowed-licenses` - optional comma-separated or newline-separated deny list.
- `report-path` - optional output path for the generated JSON report, defaults to `dependency-license-report.json`.

## Outputs

- `dependency-count` - number of inspected distributions.
- `failed` - whether any dependency violated the configured policy.
- `report-path` - path to the generated JSON report.

## Example

```yaml
jobs:
  check-licenses:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./ 
        with:
          package: .
          allowed-licenses: |
            MIT
            Apache Software License
          disallowed-licenses: GPL
```
