# Dependency License Check Action

This repository provides a reusable GitHub Action that can be dropped into other repositories to check dependency license metadata.

The action currently supports:

- Python repositories (`pyproject.toml`, `setup.py`, `setup.cfg`, or `requirements.txt`)
- Rust repositories (`Cargo.toml`)
- JavaScript repositories that use npm (`package.json`)

## Inputs

- `path` - optional repository subdirectory to inspect, defaults to `.`.
- `ecosystem` - optional `auto`, `python`, `rust`, or `javascript`, defaults to `auto`.
- `python-version` - optional Python version, defaults to `3.11`.
- `node-version` - optional Node.js version for JavaScript projects, defaults to `20`.
- `fail-on-missing-license` - fail when a dependency does not expose license metadata, defaults to `true`.
- `allowed-licenses` - optional comma-separated or newline-separated allow list.
- `disallowed-licenses` - optional comma-separated or newline-separated deny list.
- `report-path` - optional output path for the generated JSON report, defaults to `dependency-license-report.json`.

## Outputs

- `ecosystem` - detected or selected ecosystem.
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
      - uses: achapkowski/gdalwheelaction@main
        with:
          ecosystem: auto
          allowed-licenses: |
            MIT
            Apache Software License
          disallowed-licenses: GPL
```

## Notes

- Python projects are inspected in a temporary virtual environment so the repository checkout is not modified.
- JavaScript projects are currently inspected with npm by installing dependencies into `node_modules` inside the checked-out repository.
- Rust projects are inspected through `cargo metadata`, which reads dependency license information from Cargo metadata.
