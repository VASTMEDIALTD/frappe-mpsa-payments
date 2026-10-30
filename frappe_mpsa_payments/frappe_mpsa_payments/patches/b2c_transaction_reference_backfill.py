"""Replay B2C callbacks that were rolled back before this release.

``handle_callback`` used to copy the B2C status ("Paid") straight into
``Integration Request.status``. That field only accepts Queued, Authorized,
Completed, Cancelled and Failed, so every *successful* callback raised a
ValidationError and the whole transaction was rolled back:

* the B2C Request kept an empty ``transaction_id``, ``result_code`` and
  ``result_desc``,
* its B2C Payment Disbursement Reference stayed "Initiated",
* the Payment Entry / Journal Entry / Loan Disbursement kept the temporary
  ``PENDING-...`` reference and ``b2c_payment_status = Initiated``.

The provider payload is still stored on the callback Integration Request, so the
real transaction values are replayed here. The patch is idempotent - requests
and references that already carry the confirmed values are left untouched.
"""

import json

import frappe
from frappe.utils import get_datetime, getdate

from frappe_mpsa_payments.services.b2c_ledger_service import (
    TRANSACTION_REFERENCE_FIELDS,
    resolve_ledger_entry,
    sync_ledger_with_request,
)
from frappe_mpsa_payments.utils.doctype_names import B2C_REQUEST_DOCTYPE

REFERENCE_DOCTYPE = "B2C Payment Disbursement Reference"
DISBURSEMENT_DOCTYPE = "B2C Payment Disbursement"
INTEGRATION_REQUEST_DOCTYPE = "Integration Request"

CALLBACK_DESCRIPTIONS = ("Mpesa B2C Callback", "Stanbic B2C Callback")


def execute(dry_run: bool = False) -> None:
    """Backfill confirmed provider values on every paid B2C request."""
    repaired = 0

    for row in frappe.get_all(
        B2C_REQUEST_DOCTYPE,
        filters={"status": "Paid"},
        fields=["name"],
        order_by="creation asc",
    ):
        try:
            repaired += 1 if _repair_request(row.name, dry_run) else 0
        except Exception:
            frappe.db.rollback()
            frappe.log_error(
                frappe.get_traceback(),
                f"B2C transaction reference backfill failed for {row.name}",
            )

    if not dry_run:
        frappe.db.commit()

    if repaired:
        frappe.log(f"B2C transaction reference backfill repaired {repaired} request(s)")


def _repair_request(request_name: str, dry_run: bool) -> bool:
    request = frappe.get_doc(B2C_REQUEST_DOCTYPE, request_name)

    if not request.get("transaction_id"):
        values = _transaction_values(request_name)
        if not values:
            return False

        if dry_run:
            frappe.log(f"[B2C backfill] {request_name} would get {values}")
        else:
            frappe.db.set_value(B2C_REQUEST_DOCTYPE, request_name, values)
            request.reload()

    reference = _reference_doc(request)
    if not reference:
        return False

    if dry_run:
        frappe.log(
            f"[B2C backfill] {request_name} / {reference.name}: "
            f"reference_no -> {request.get('transaction_id')}"
        )
        return True

    if not _stamp_reference(reference, request):
        return False

    _refresh_disbursement(request.get("b2c_payment_disbursement"))
    return True


def _reference_doc(request):
    """Return the B2C Payment Disbursement Reference row of a request."""
    name = request.get("b2c_payment_disbursement_reference")

    if name and frappe.db.exists(REFERENCE_DOCTYPE, name):
        return frappe.get_doc(REFERENCE_DOCTYPE, name)

    return None


def _stamp_reference(reference, request) -> bool:
    """Mark the reference row paid and replace the temporary ledger reference."""
    changed = False

    if reference.get("payment_status") != "Paid" or reference.get(
        "reference_no"
    ) != request.get("transaction_id"):
        values = {
            "payment_status": "Paid",
            "reference_no": request.get("transaction_id"),
        }

        completed_on = request.get("transaction_completed_datetime")
        if completed_on:
            values["reference_date"] = getdate(completed_on)

        frappe.db.set_value(reference.doctype, reference.name, values)
        reference.reload()
        changed = True

    if not _ledger_matches(reference, request):
        sync_ledger_with_request(reference, request)
        changed = True

    return changed


def _ledger_matches(reference, request) -> bool:
    """Whether the accounting document already holds the provider reference."""
    ledger_doctype, ledger_name = resolve_ledger_entry(reference, request)
    if not ledger_doctype:
        return True

    number_field, _date_field = TRANSACTION_REFERENCE_FIELDS.get(
        ledger_doctype, (None, None)
    )
    if not number_field:
        return True

    current = frappe.db.get_value(ledger_doctype, ledger_name, number_field)
    return current == request.get("transaction_id")


def _refresh_disbursement(disbursement_name: str | None) -> None:
    """Recompute the parent disbursement status from its reference rows."""
    if not disbursement_name or not frappe.db.exists(
        DISBURSEMENT_DOCTYPE, disbursement_name
    ):
        return

    try:
        frappe.get_doc(
            DISBURSEMENT_DOCTYPE, disbursement_name
        ).update_disbursement_status()
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"B2C status refresh failed for {disbursement_name}",
        )


def _transaction_values(request_name: str) -> dict:
    """Read the confirmed transaction details from the stored callback payload."""
    for row in frappe.get_all(
        INTEGRATION_REQUEST_DOCTYPE,
        filters={
            "reference_doctype": B2C_REQUEST_DOCTYPE,
            "reference_docname": request_name,
            "request_description": ("in", CALLBACK_DESCRIPTIONS),
        },
        fields=["data"],
        order_by="creation desc",
    ):
        payload = _load_payload(row.get("data"))
        values = _mpesa_values(payload) or _stanbic_values(payload)

        if values.get("transaction_id"):
            return values

    return {}


def _load_payload(data) -> dict:
    if not data:
        return {}

    try:
        payload = json.loads(data)
    except (TypeError, ValueError):
        return {}

    return payload if isinstance(payload, dict) else {}


def _mpesa_values(payload: dict) -> dict:
    """Extract the confirmation fields from a Daraja B2C result payload."""
    if str(payload.get("ResultCode")) != "0":
        return {}

    parameters = {
        item.get("Key"): item.get("Value")
        for item in (payload.get("ResultParameters") or {}).get("ResultParameter", [])
    }

    values = {
        "transaction_id": payload.get("TransactionID")
        or parameters.get("TransactionReceipt"),
        "result_code": str(payload.get("ResultCode")),
        "result_desc": payload.get("ResultDesc"),
        "conversation_id": payload.get("ConversationID"),
        "receiver_public_name": parameters.get("ReceiverPartyPublicName"),
        "recipient_is_registered_customer": parameters.get(
            "B2CRecipientIsRegisteredCustomer"
        ),
    }

    completed_on = parameters.get("TransactionCompletedDateTime")
    if completed_on:
        values["transaction_completed_datetime"] = get_datetime(completed_on)

    return values


def _stanbic_values(payload: dict) -> dict:
    """Extract the confirmation fields from a Stanbic callback payload."""
    if payload.get("bankStatus") != "ACCEPTED":
        return {}

    return {
        "transaction_id": payload.get("bankReferenceId"),
        "response_code": payload.get("reasonCode", ""),
        "response_description": payload.get("reasonText", ""),
        "next_execution_date": payload.get("nextExecutionDate"),
    }
