from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


UNKNOWN_LICENSE_VALUES = {"", "n/a", "none", "see license", "unknown"}


@dataclass
class LicenseResult:
    name: str
    version: str
    license: str
    license_classifiers: list[str]
    status: str
    issues: list[str]


def normalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def parse_bool(value: str) -> bool:
    return normalize_text(value) not in {"", "0", "false", "no", "off"}


def parse_rules(raw: str) -> list[str]:
    if not raw:
        return []

    rules: list[str] = []
    for entry in raw.replace(",", "\n").splitlines():
        normalized = normalize_text(entry)
        if normalized:
            rules.append(normalized)
    return rules


def unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = normalize_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(value.strip())
    return ordered


def extract_license_information(distribution: metadata.Distribution) -> tuple[list[str], list[str]]:
    meta = distribution.metadata
    classifiers = [
        classifier.strip()
        for classifier in meta.get_all("Classifier", [])
        if classifier.strip().startswith("License ::")
    ]
    explicit_license = meta.get("License", "").strip()

    candidates: list[str] = []
    if normalize_text(explicit_license) not in UNKNOWN_LICENSE_VALUES:
        candidates.append(explicit_license)

    for classifier in classifiers:
        leaf = classifier.split("::")[-1].strip()
        if leaf:
            candidates.append(leaf)

    return unique(candidates), unique(classifiers)


def matches_any_rule(licenses: Sequence[str], rules: Sequence[str]) -> bool:
    normalized_licenses = [normalize_text(value) for value in licenses if normalize_text(value)]
    for license_value in normalized_licenses:
        for rule in rules:
            if rule in license_value or license_value in rule:
                return True
    return False


def evaluate_distribution(
    distribution: metadata.Distribution,
    *,
    allowed_licenses: Sequence[str],
    disallowed_licenses: Sequence[str],
    fail_on_missing_license: bool,
) -> LicenseResult:
    name = distribution.metadata.get("Name") or distribution.name or "unknown"
    version = distribution.version or distribution.metadata.get("Version", "")
    licenses, classifiers = extract_license_information(distribution)
    issues: list[str] = []

    if not licenses and fail_on_missing_license:
        issues.append("Missing license metadata")

    if disallowed_licenses and matches_any_rule(licenses + classifiers, disallowed_licenses):
        issues.append("Matches a disallowed license rule")

    if allowed_licenses and not matches_any_rule(licenses + classifiers, allowed_licenses):
        issues.append("Does not match any allowed license rule")

    display_license = ", ".join(licenses) if licenses else ""

    return LicenseResult(
        name=name,
        version=version,
        license=display_license,
        license_classifiers=classifiers,
        status="failed" if issues else "passed",
        issues=issues,
    )


def read_baseline(path: str) -> set[str]:
    with open(path, encoding="utf-8") as handle:
        return {normalize_name(line) for line in handle.read().splitlines() if line.strip()}


def collect_new_distributions(baseline: set[str]) -> list[metadata.Distribution]:
    discovered: dict[str, metadata.Distribution] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name") or distribution.name or ""
        if not name:
            continue

        normalized_name = normalize_name(name)
        if normalized_name in baseline:
            continue

        discovered[normalized_name] = distribution

    return [discovered[name] for name in sorted(discovered)]


def write_outputs(*, dependency_count: int, failed: bool, report_path: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return

    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"dependency-count={dependency_count}\n")
        handle.write(f"failed={'true' if failed else 'false'}\n")
        handle.write(f"report-path={report_path}\n")


def write_summary(results: Sequence[LicenseResult], *, failed: bool, report_path: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("## Dependency license report\n\n")
        handle.write(f"- Dependencies inspected: **{len(results)}**\n")
        handle.write(f"- Result: **{'failed' if failed else 'passed'}**\n")
        handle.write(f"- JSON report: `{report_path}`\n\n")
        if results:
            handle.write("| Package | Version | License | Status |\n")
            handle.write("| --- | --- | --- | --- |\n")
            for result in results:
                license_value = result.license or "missing"
                handle.write(
                    f"| {result.name} | {result.version} | {license_value} | {result.status} |\n"
                )
            handle.write("\n")

        failed_results = [result for result in results if result.status == "failed"]
        if failed_results:
            handle.write("### Failures\n\n")
            for result in failed_results:
                handle.write(f"- `{result.name}`: {', '.join(result.issues)}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check license metadata for Python package dependencies installed into the current environment."
    )
    parser.add_argument("--baseline", required=True, help="File containing the normalized package names present before install.")
    parser.add_argument("--report-path", required=True, help="Where to write the JSON report.")
    parser.add_argument("--allowed-licenses", default="", help="Comma-separated or newline-separated list of allowed licenses.")
    parser.add_argument("--disallowed-licenses", default="", help="Comma-separated or newline-separated list of disallowed licenses.")
    parser.add_argument("--fail-on-missing-license", default="true", help="Whether missing license metadata should fail the check.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    baseline = read_baseline(args.baseline)
    new_distributions = collect_new_distributions(baseline)
    allowed_licenses = parse_rules(args.allowed_licenses)
    disallowed_licenses = parse_rules(args.disallowed_licenses)
    fail_on_missing_license = parse_bool(args.fail_on_missing_license)

    results = [
        evaluate_distribution(
            distribution,
            allowed_licenses=allowed_licenses,
            disallowed_licenses=disallowed_licenses,
            fail_on_missing_license=fail_on_missing_license,
        )
        for distribution in new_distributions
    ]

    failed = any(result.status == "failed" for result in results)
    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps([asdict(result) for result in results], indent=2) + "\n",
        encoding="utf-8",
    )

    write_outputs(dependency_count=len(results), failed=failed, report_path=str(report_path))
    write_summary(results, failed=failed, report_path=str(report_path))

    if failed:
        for result in results:
            if result.status == "failed":
                print(f"{result.name}: {', '.join(result.issues)}", file=sys.stderr)
        return 1

    print(f"Checked {len(results)} dependencies; no license policy violations found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
