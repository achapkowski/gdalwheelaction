from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence


UNKNOWN_LICENSE_VALUES = {"", "n/a", "none", "see license", "unknown"}
SUPPORTED_ECOSYSTEMS = ("python", "rust", "javascript")
PYTHON_SNAPSHOT_SCRIPT = """
import importlib.metadata as metadata
import json

items = []
for distribution in metadata.distributions():
    meta = distribution.metadata
    name = meta.get("Name") or distribution.name or ""
    if not name:
        continue

    classifiers = [
        classifier.strip()
        for classifier in meta.get_all("Classifier", [])
        if classifier.strip().startswith("License ::")
    ]
    items.append(
        {
            "name": name,
            "version": distribution.version or meta.get("Version", ""),
            "license": meta.get("License", "").strip(),
            "classifiers": classifiers,
        }
    )

print(json.dumps(items))
""".strip()


@dataclass
class DependencyRecord:
    ecosystem: str
    name: str
    version: str
    licenses: list[str] = field(default_factory=list)
    license_details: list[str] = field(default_factory=list)


@dataclass
class LicenseResult:
    ecosystem: str
    name: str
    version: str
    license: str
    license_details: list[str]
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


def parse_license_value(raw_value: object) -> list[str]:
    if isinstance(raw_value, str):
        normalized = raw_value.strip()
        return [normalized] if normalize_text(normalized) not in UNKNOWN_LICENSE_VALUES else []

    if isinstance(raw_value, dict):
        if "type" in raw_value and isinstance(raw_value["type"], str):
            return parse_license_value(raw_value["type"])
        return [json.dumps(raw_value, sort_keys=True)]

    if isinstance(raw_value, list):
        values: list[str] = []
        for entry in raw_value:
            values.extend(parse_license_value(entry))
        return unique(values)

    return []


def extract_python_license_information(distribution: metadata.Distribution) -> tuple[list[str], list[str]]:
    meta = distribution.metadata
    classifiers = [
        classifier.strip()
        for classifier in meta.get_all("Classifier", [])
        if classifier.strip().startswith("License ::")
    ]
    explicit_license = meta.get("License", "").strip()

    candidates = parse_license_value(explicit_license)
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


def evaluate_record(
    record: DependencyRecord,
    *,
    allowed_licenses: Sequence[str],
    disallowed_licenses: Sequence[str],
    fail_on_missing_license: bool,
) -> LicenseResult:
    issues: list[str] = []

    if not record.licenses and fail_on_missing_license:
        issues.append("Missing license metadata")

    if disallowed_licenses and matches_any_rule(record.licenses + record.license_details, disallowed_licenses):
        issues.append("Matches a disallowed license rule")

    if allowed_licenses and not matches_any_rule(record.licenses + record.license_details, allowed_licenses):
        issues.append("Does not match any allowed license rule")

    display_license = ", ".join(record.licenses) if record.licenses else ""

    return LicenseResult(
        ecosystem=record.ecosystem,
        name=record.name,
        version=record.version,
        license=display_license,
        license_details=record.license_details,
        status="failed" if issues else "passed",
        issues=issues,
    )


def detect_ecosystem(project_path: Path) -> str:
    matches = []
    if any((project_path / candidate).exists() for candidate in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")):
        matches.append("python")
    if (project_path / "Cargo.toml").exists():
        matches.append("rust")
    if (project_path / "package.json").exists():
        matches.append("javascript")

    if not matches:
        raise ValueError(
            f"Could not detect a supported ecosystem in {project_path}. Expected one of: pyproject.toml/setup.py/setup.cfg/requirements.txt, Cargo.toml, package.json."
        )

    if len(matches) > 1:
        raise ValueError(
            f"Found multiple supported ecosystems in {project_path}: {', '.join(matches)}. Set the ecosystem input explicitly."
        )

    return matches[0]


def run_command(command: Sequence[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def python_distribution_snapshot(python_executable: Path) -> list[dict[str, object]]:
    output = subprocess.run(
        [
            str(python_executable),
            "-c",
            PYTHON_SNAPSHOT_SCRIPT,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(output.stdout)


def venv_python_path(venv_path: Path) -> Path:
    if os.name == "nt":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def collect_python_dependency_records(project_path: Path) -> list[DependencyRecord]:
    install_target: list[str]
    exclude_requested = False
    if any((project_path / candidate).exists() for candidate in ("pyproject.toml", "setup.py", "setup.cfg")):
        install_target = [str(project_path)]
        exclude_requested = True
    elif (project_path / "requirements.txt").exists():
        install_target = ["-r", str(project_path / "requirements.txt")]
    else:
        raise ValueError(
            f"Could not find a Python package or requirements file in {project_path}."
        )

    with tempfile.TemporaryDirectory(prefix="dependency-license-python-") as temp_dir:
        venv_path = Path(temp_dir) / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True)
        python_executable = venv_python_path(venv_path)

        subprocess.run([str(python_executable), "-m", "pip", "install", "--upgrade", "pip"], check=True)
        baseline = {
            normalize_name(item["name"])
            for item in python_distribution_snapshot(python_executable)
            if item.get("name")
        }
        install_report_path = Path(temp_dir) / "pip-install-report.json"
        subprocess.run(
            [
                str(python_executable),
                "-m",
                "pip",
                "install",
                "--report",
                str(install_report_path),
                *install_target,
            ],
            cwd=project_path,
            check=True,
        )
        requested = set()
        if exclude_requested and install_report_path.exists():
            install_report = json.loads(install_report_path.read_text(encoding="utf-8"))
            requested = {
                normalize_name(entry.get("metadata", {}).get("name", ""))
                for entry in install_report.get("install", [])
                if entry.get("requested") and entry.get("metadata", {}).get("name")
            }

        records: list[DependencyRecord] = []
        for item in python_distribution_snapshot(python_executable):
            name = str(item.get("name", "")).strip()
            if not name or normalize_name(name) in baseline:
                continue
            if exclude_requested and normalize_name(name) in requested:
                continue

            explicit_license = str(item.get("license", "")).strip()
            classifiers = [str(classifier) for classifier in item.get("classifiers", [])]
            licenses = parse_license_value(explicit_license)
            for classifier in classifiers:
                leaf = classifier.split("::")[-1].strip()
                if leaf:
                    licenses.append(leaf)

            records.append(
                DependencyRecord(
                    ecosystem="python",
                    name=name,
                    version=str(item.get("version", "")),
                    licenses=unique(licenses),
                    license_details=unique(classifiers),
                )
            )

        return sorted(records, key=lambda record: normalize_name(record.name))


def collect_javascript_dependency_records(project_path: Path) -> list[DependencyRecord]:
    package_json_path = project_path / "package.json"
    if not package_json_path.exists():
        raise ValueError(f"Could not find package.json in {project_path}.")

    install_command = ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"]
    if not (project_path / "package-lock.json").exists():
        install_command = ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]
    subprocess.run(install_command, cwd=project_path, check=True)

    node_modules = project_path / "node_modules"
    if not node_modules.exists():
        return []

    return scan_javascript_node_modules(node_modules)


def scan_javascript_node_modules(node_modules: Path) -> list[DependencyRecord]:
    records: dict[tuple[str, str], DependencyRecord] = {}
    for package_file in node_modules.rglob("package.json"):
        if ".bin" in package_file.parts:
            continue

        data = json.loads(package_file.read_text(encoding="utf-8"))
        name = str(data.get("name", "")).strip()
        version = str(data.get("version", "")).strip()
        if not name or not version:
            continue

        licenses = unique(
            parse_license_value(data.get("license")) + parse_license_value(data.get("licenses"))
        )
        key = (name, version)
        records[key] = DependencyRecord(
            ecosystem="javascript",
            name=name,
            version=version,
            licenses=licenses,
            license_details=[],
        )

    return sorted(records.values(), key=lambda record: (normalize_name(record.name), record.version))


def collect_rust_dependency_records_from_metadata(metadata_payload: dict[str, object]) -> list[DependencyRecord]:
    packages = {
        package["id"]: package
        for package in metadata_payload.get("packages", [])
        if isinstance(package, dict) and "id" in package
    }
    resolve = metadata_payload.get("resolve") or {}
    nodes = {
        node["id"]: node
        for node in resolve.get("nodes", [])
        if isinstance(node, dict) and "id" in node
    }
    workspace_members = set(metadata_payload.get("workspace_members", []))

    reachable: set[str] = set()
    stack: list[str] = []
    for member in workspace_members:
        member_node = nodes.get(member, {})
        for dependency in member_node.get("deps", []):
            package_id = dependency.get("pkg")
            if isinstance(package_id, str):
                stack.append(package_id)

    while stack:
        package_id = stack.pop()
        if package_id in reachable or package_id in workspace_members:
            continue

        reachable.add(package_id)
        for dependency in nodes.get(package_id, {}).get("deps", []):
            next_package = dependency.get("pkg")
            if isinstance(next_package, str):
                stack.append(next_package)

    records: list[DependencyRecord] = []
    for package_id in sorted(reachable):
        package = packages.get(package_id, {})
        name = str(package.get("name", "")).strip()
        version = str(package.get("version", "")).strip()
        if not name or not version:
            continue

        licenses = parse_license_value(package.get("license"))
        license_file_value = package.get("license_file")
        license_file = (
            license_file_value.strip()
            if isinstance(license_file_value, str)
            else ""
        )
        details = [f"license-file:{license_file}"] if license_file else []

        records.append(
            DependencyRecord(
                ecosystem="rust",
                name=name,
                version=version,
                licenses=licenses,
                license_details=details,
            )
        )

    return records


def collect_rust_dependency_records(project_path: Path) -> list[DependencyRecord]:
    cargo_toml = project_path / "Cargo.toml"
    if not cargo_toml.exists():
        raise ValueError(f"Could not find Cargo.toml in {project_path}.")

    command = [
        "cargo",
        "metadata",
        "--format-version",
        "1",
        "--manifest-path",
        str(cargo_toml),
    ]
    if (project_path / "Cargo.lock").exists():
        command.append("--locked")

    payload = json.loads(run_command(command, cwd=project_path))
    return collect_rust_dependency_records_from_metadata(payload)


def collect_dependency_records(project_path: Path, ecosystem: str) -> list[DependencyRecord]:
    if ecosystem == "python":
        return collect_python_dependency_records(project_path)
    if ecosystem == "javascript":
        return collect_javascript_dependency_records(project_path)
    if ecosystem == "rust":
        return collect_rust_dependency_records(project_path)
    raise ValueError(f"Unsupported ecosystem: {ecosystem}")


def write_outputs(*, ecosystem: str, dependency_count: int, failed: bool, report_path: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return

    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"ecosystem={ecosystem}\n")
        handle.write(f"dependency-count={dependency_count}\n")
        handle.write(f"failed={'true' if failed else 'false'}\n")
        handle.write(f"report-path={report_path}\n")


def write_summary(results: Sequence[LicenseResult], *, ecosystem: str, failed: bool, report_path: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("## Dependency license report\n\n")
        handle.write(f"- Ecosystem: **{ecosystem}**\n")
        handle.write(f"- Dependencies inspected: **{len(results)}**\n")
        handle.write(f"- Result: **{'failed' if failed else 'passed'}**\n")
        handle.write(f"- JSON report: `{report_path}`\n\n")
        if results:
            handle.write("| Ecosystem | Package | Version | License | Status |\n")
            handle.write("| --- | --- | --- | --- | --- |\n")
            for result in results:
                license_value = result.license or "missing"
                handle.write(
                    f"| {result.ecosystem} | {result.name} | {result.version} | {license_value} | {result.status} |\n"
                )
            handle.write("\n")

        failed_results = [result for result in results if result.status == "failed"]
        if failed_results:
            handle.write("### Failures\n\n")
            for result in failed_results:
                handle.write(f"- `{result.name}`: {', '.join(result.issues)}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check dependency license metadata for Python, Rust, and JavaScript projects."
    )
    parser.add_argument("--path", default=".", help="Repository directory that contains the package manifest.")
    parser.add_argument(
        "--ecosystem",
        default="auto",
        choices=("auto",) + SUPPORTED_ECOSYSTEMS,
        help="Supported ecosystem to inspect or auto to detect it from the repository.",
    )
    parser.add_argument("--report-path", required=True, help="Where to write the JSON report.")
    parser.add_argument("--allowed-licenses", default="", help="Comma-separated or newline-separated list of allowed licenses.")
    parser.add_argument("--disallowed-licenses", default="", help="Comma-separated or newline-separated list of disallowed licenses.")
    parser.add_argument("--fail-on-missing-license", default="true", help="Whether missing license metadata should fail the check.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    project_path = Path(args.path).resolve()
    ecosystem = detect_ecosystem(project_path) if args.ecosystem == "auto" else args.ecosystem
    allowed_licenses = parse_rules(args.allowed_licenses)
    disallowed_licenses = parse_rules(args.disallowed_licenses)
    fail_on_missing_license = parse_bool(args.fail_on_missing_license)

    results = [
        evaluate_record(
            record,
            allowed_licenses=allowed_licenses,
            disallowed_licenses=disallowed_licenses,
            fail_on_missing_license=fail_on_missing_license,
        )
        for record in collect_dependency_records(project_path, ecosystem)
    ]

    failed = any(result.status == "failed" for result in results)
    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps([asdict(result) for result in results], indent=2) + "\n",
        encoding="utf-8",
    )

    write_outputs(
        ecosystem=ecosystem,
        dependency_count=len(results),
        failed=failed,
        report_path=str(report_path),
    )
    write_summary(results, ecosystem=ecosystem, failed=failed, report_path=str(report_path))

    if failed:
        for result in results:
            if result.status == "failed":
                print(
                    f"{result.ecosystem}:{result.name}: {', '.join(result.issues)}",
                    file=sys.stderr,
                )
        return 1

    print(
        f"Checked {len(results)} {ecosystem} dependencies; no license policy violations found."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
