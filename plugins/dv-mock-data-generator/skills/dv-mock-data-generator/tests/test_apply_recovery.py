from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from apply_testdata_workbook import (
    is_create_multiple_unsupported,
    is_multiple_unsupported,
    normalize_created_ids,
    resumed_record_map,
    workbook_hash,
)


class ApplyRecoveryTests(unittest.TestCase):
    def test_only_specific_create_multiple_failures_enable_fallback(self) -> None:
        self.assertTrue(
            is_create_multiple_unsupported(
                RuntimeError(
                    "The 'CreateMultiple' method does not support entities of this type. "
                    "MessageProcessor.Empty"
                )
            )
        )
        self.assertFalse(
            is_create_multiple_unsupported(
                RuntimeError("A plugin rejected the record because a required value is missing.")
            )
        )
        self.assertTrue(
            is_multiple_unsupported(
                RuntimeError("UpdateMultiple does not support this entity."),
                "UpdateMultiple",
            )
        )

    def test_created_id_normalization_supports_probe_and_bulk_results(self) -> None:
        self.assertEqual(["one"], normalize_created_ids("one"))
        self.assertEqual(["one", "two"], normalize_created_ids(["one", "two"]))

    def test_workbook_hash_changes_with_the_approved_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preview.xlsx"
            path.write_bytes(b"first")
            first = workbook_hash(path)
            path.write_bytes(b"second")
            second = workbook_hash(path)

        self.assertNotEqual(first, second)

    def test_partial_resume_accepts_only_unique_approved_row_keys(self) -> None:
        self.assertEqual(
            {"TD-account-0001": "guid-1"},
            resumed_record_map(
                "account",
                [{"row_key": "TD-account-0001", "id": "guid-1"}],
                {"TD-account-0001", "TD-account-0002"},
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "unknown row key"):
            resumed_record_map(
                "account",
                [{"row_key": "TD-account-9999", "id": "guid-1"}],
                {"TD-account-0001"},
            )
        with self.assertRaisesRegex(RuntimeError, "duplicate row key"):
            resumed_record_map(
                "account",
                [
                    {"row_key": "TD-account-0001", "id": "guid-1"},
                    {"row_key": "TD-account-0001", "id": "guid-2"},
                ],
                {"TD-account-0001"},
            )


if __name__ == "__main__":
    unittest.main()
