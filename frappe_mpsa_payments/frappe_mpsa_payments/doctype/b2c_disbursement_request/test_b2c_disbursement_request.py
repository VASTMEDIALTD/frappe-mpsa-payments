# Copyright (c) 2024, Navari Limited and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from frappe_mpsa_payments.connectors.base_connector import (
    b2c_status_to_integration_status,
)
from frappe_mpsa_payments.frappe_mpsa_payments.patches.b2c_transaction_reference_backfill import (
    _mpesa_values,
)


class TestB2CDisbursementRequest(FrappeTestCase):
    def test_b2c_status_maps_to_valid_integration_request_status(self):
        """A paid payout must not be written as "Paid" on an Integration Request."""
        self.assertEqual(b2c_status_to_integration_status("Paid"), "Completed")
        self.assertEqual(b2c_status_to_integration_status("Failed"), "Failed")
        self.assertEqual(b2c_status_to_integration_status(None), "Failed")

        options = frappe.get_meta("Integration Request").get_field("status").options
        options = (options or "").splitlines()

        self.assertIn("Completed", options)
        self.assertIn("Failed", options)
        self.assertNotIn("Paid", options)

    def test_successful_mpesa_payload_is_recovered(self):
        values = _mpesa_values(
            {
                "ResultCode": 0,
                "ResultDesc": "The service request is processed successfully.",
                "ConversationID": "AG_20261030_010011450",
                "TransactionID": "UJU410DRI1",
                "ResultParameters": {
                    "ResultParameter": [
                        {"Key": "TransactionReceipt", "Value": "UJU410DRI1"},
                        {
                            "Key": "TransactionCompletedDateTime",
                            "Value": "30.10.2026 12:09:46",
                        },
                    ]
                },
            }
        )

        self.assertEqual(values["transaction_id"], "UJU410DRI1")
        self.assertEqual(values["result_code"], "0")
        self.assertEqual(values["conversation_id"], "AG_20261030_010011450")
        self.assertEqual(values["transaction_completed_datetime"].year, 2026)

    def test_failed_mpesa_payload_is_not_recovered(self):
        self.assertEqual(
            _mpesa_values({"ResultCode": 1, "TransactionID": "UJU410DRMJ"}), {}
        )
