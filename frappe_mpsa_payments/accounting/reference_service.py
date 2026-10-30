"""Temporary transaction references for accounting documents.

When "Post To Ledger Before Request" is enabled the accounting document is
created and submitted before the payout leaves the account, so the provider has
not returned the transaction id and completion date yet. ERPNext however
requires both for bank transactions:

* Payment Entry -> ``reference_no`` / ``reference_date``
* Journal Entry (Bank Entry) -> ``cheque_no`` / ``cheque_date``

A temporary reference is generated so the document can still be posted. It is
replaced with the provider values when the confirmation arrives (see
``b2c_ledger_service.mark_confirmed``), so it only remains on payouts that never
confirmed.
"""

from frappe.utils import nowdate, random_string

# Prefix of the temporary reference stamped on entries posted before the
# provider confirms the payout.
PENDING_REFERENCE_PREFIX = "PENDING"


def get_transaction_reference(b2c_disbursement_ref):
    """Return the (reference_no, reference_date) to use on the ledger document.

    Args:
        b2c_disbursement_ref: B2C Payment Disbursement Reference row

    Returns:
        tuple: (reference_no, reference_date) - the provider values when the
        row is already confirmed, otherwise a temporary reference.
    """
    reference_no = b2c_disbursement_ref.get("reference_no")
    reference_date = b2c_disbursement_ref.get("reference_date")

    if reference_no and reference_date:
        return reference_no, reference_date

    return (
        reference_no or f"{PENDING_REFERENCE_PREFIX}-{random_string(10).upper()}",
        reference_date or nowdate(),
    )
