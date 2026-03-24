from __future__ import annotations

import unittest
from email import message_from_string
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.check_dependency_licenses import (
    DependencyRecord,
    collect_rust_dependency_records_from_metadata,
    detect_ecosystem,
    evaluate_record,
    parse_rules,
    scan_javascript_node_modules,
)

class FakePythonRecord(DependencyRecord):
    def __init__(self, metadata_text: str, version: str = "1.0.0") -> None:
        metadata = message_from_string(metadata_text)
        licenses = []
        explicit_license = metadata.get("License", "").strip()
        if explicit_license:
            licenses.append(explicit_license)
        classifiers = metadata.get_all("Classifier", [])
        for classifier in classifiers:
            if classifier.startswith("License ::"):
                licenses.append(classifier.split("::")[-1].strip())

        super().__init__(
            ecosystem="python",
            name=metadata.get("Name", "unknown"),
            version=version,
            licenses=licenses,
            license_details=classifiers,
        )


class DependencyLicenseTests(unittest.TestCase):
    def test_explicit_license_is_reported(self) -> None:
        record = FakePythonRecord(
            "Name: sample-package\n"
            "Version: 1.2.3\n"
            "License: MIT\n"
        )

        result = evaluate_record(
            record,
            allowed_licenses=[],
            disallowed_licenses=[],
            fail_on_missing_license=True,
        )

        self.assertEqual(result.license, "MIT")
        self.assertEqual(result.status, "passed")

    def test_license_classifier_is_used_when_license_field_missing(self) -> None:
        record = FakePythonRecord(
            "Name: sample-package\n"
            "Version: 1.2.3\n"
            "Classifier: License :: OSI Approved :: Apache Software License\n"
        )

        result = evaluate_record(
            record,
            allowed_licenses=["apache"],
            disallowed_licenses=[],
            fail_on_missing_license=True,
        )

        self.assertEqual(result.license, "Apache Software License")
        self.assertEqual(result.status, "passed")

    def test_missing_license_can_fail(self) -> None:
        record = FakePythonRecord(
            "Name: sample-package\n"
            "Version: 1.2.3\n"
        )

        result = evaluate_record(
            record,
            allowed_licenses=[],
            disallowed_licenses=[],
            fail_on_missing_license=True,
        )

        self.assertEqual(result.status, "failed")
        self.assertIn("Missing license metadata", result.issues)

    def test_disallowed_license_fails(self) -> None:
        record = FakePythonRecord(
            "Name: sample-package\n"
            "Version: 1.2.3\n"
            "License: GPL-3.0-only\n"
        )

        result = evaluate_record(
            record,
            allowed_licenses=[],
            disallowed_licenses=["gpl"],
            fail_on_missing_license=False,
        )

        self.assertEqual(result.status, "failed")
        self.assertIn("Matches a disallowed license rule", result.issues)

    def test_parse_rules_supports_commas_and_newlines(self) -> None:
        self.assertEqual(parse_rules("MIT,\nApache-2.0"), ["mit", "apache-2.0"])

    def test_detect_ecosystem_can_find_each_supported_manifest(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            (project_path / "Cargo.toml").write_text("[package]\nname = 'demo'\nversion = '0.1.0'\n", encoding="utf-8")
            self.assertEqual(detect_ecosystem(project_path), "rust")

    def test_detect_ecosystem_requires_explicit_choice_for_multi_manifest_repos(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            (project_path / "pyproject.toml").write_text("[project]\nname = 'demo'\nversion = '0.1.0'\n", encoding="utf-8")
            (project_path / "package.json").write_text('{"name":"demo","version":"1.0.0"}\n', encoding="utf-8")

            with self.assertRaises(ValueError):
                detect_ecosystem(project_path)

    def test_scan_javascript_node_modules_reads_installed_packages(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            package_dir = project_path / "node_modules" / "left-pad"
            package_dir.mkdir(parents=True)
            (package_dir / "package.json").write_text(
                '{"name":"left-pad","version":"1.3.0","license":"WTFPL"}\n',
                encoding="utf-8",
            )

            records = scan_javascript_node_modules(project_path / "node_modules")

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].name, "left-pad")
            self.assertEqual(records[0].licenses, ["WTFPL"])

    def test_collect_rust_dependency_records_from_metadata_follows_resolved_graph(self) -> None:
        metadata_payload = {
            "workspace_members": ["root 0.1.0 (path+file:///workspace)"],
            "packages": [
                {"id": "root 0.1.0 (path+file:///workspace)", "name": "root", "version": "0.1.0", "license": "MIT"},
                {"id": "dep-a 1.0.0 (registry+https://example.invalid)", "name": "dep-a", "version": "1.0.0", "license": "Apache-2.0"},
                {"id": "dep-b 2.0.0 (registry+https://example.invalid)", "name": "dep-b", "version": "2.0.0", "license_file": "LICENSE"},
            ],
            "resolve": {
                "nodes": [
                    {
                        "id": "root 0.1.0 (path+file:///workspace)",
                        "deps": [{"pkg": "dep-a 1.0.0 (registry+https://example.invalid)"}],
                    },
                    {
                        "id": "dep-a 1.0.0 (registry+https://example.invalid)",
                        "deps": [{"pkg": "dep-b 2.0.0 (registry+https://example.invalid)"}],
                    },
                    {
                        "id": "dep-b 2.0.0 (registry+https://example.invalid)",
                        "deps": [],
                    },
                ]
            },
        }

        records = collect_rust_dependency_records_from_metadata(metadata_payload)

        self.assertEqual([record.name for record in records], ["dep-a", "dep-b"])
        self.assertEqual(records[1].license_details, ["license-file:LICENSE"])


if __name__ == "__main__":
    unittest.main()
