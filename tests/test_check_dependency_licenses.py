from __future__ import annotations

import unittest
from email import message_from_string

from scripts.check_dependency_licenses import evaluate_distribution, parse_rules


class FakeDistribution:
    def __init__(self, metadata_text: str, version: str = "1.0.0") -> None:
        self.metadata = message_from_string(metadata_text)
        self.version = version
        self.name = self.metadata.get("Name", "unknown")


class DependencyLicenseTests(unittest.TestCase):
    def test_explicit_license_is_reported(self) -> None:
        distribution = FakeDistribution(
            "Name: sample-package\n"
            "Version: 1.2.3\n"
            "License: MIT\n"
        )

        result = evaluate_distribution(
            distribution,
            allowed_licenses=[],
            disallowed_licenses=[],
            fail_on_missing_license=True,
        )

        self.assertEqual(result.license, "MIT")
        self.assertEqual(result.status, "passed")

    def test_license_classifier_is_used_when_license_field_missing(self) -> None:
        distribution = FakeDistribution(
            "Name: sample-package\n"
            "Version: 1.2.3\n"
            "Classifier: License :: OSI Approved :: Apache Software License\n"
        )

        result = evaluate_distribution(
            distribution,
            allowed_licenses=["apache"],
            disallowed_licenses=[],
            fail_on_missing_license=True,
        )

        self.assertEqual(result.license, "Apache Software License")
        self.assertEqual(result.status, "passed")

    def test_missing_license_can_fail(self) -> None:
        distribution = FakeDistribution(
            "Name: sample-package\n"
            "Version: 1.2.3\n"
        )

        result = evaluate_distribution(
            distribution,
            allowed_licenses=[],
            disallowed_licenses=[],
            fail_on_missing_license=True,
        )

        self.assertEqual(result.status, "failed")
        self.assertIn("Missing license metadata", result.issues)

    def test_disallowed_license_fails(self) -> None:
        distribution = FakeDistribution(
            "Name: sample-package\n"
            "Version: 1.2.3\n"
            "License: GPL-3.0-only\n"
        )

        result = evaluate_distribution(
            distribution,
            allowed_licenses=[],
            disallowed_licenses=["gpl"],
            fail_on_missing_license=False,
        )

        self.assertEqual(result.status, "failed")
        self.assertIn("Matches a disallowed license rule", result.issues)

    def test_parse_rules_supports_commas_and_newlines(self) -> None:
        self.assertEqual(parse_rules("MIT,\nApache-2.0"), ["mit", "apache-2.0"])


if __name__ == "__main__":
    unittest.main()
