"""Ledger first B2C posting.

When ``post_to_ledger_before_request`` is ticked on Mpesa Settings the
accounting document of a B2C reference row (Payment Entry, Journal Entry or
Loan Disbursement) is created *and submitted* before the request leaves for the
provider. Submitting the document is all that is needed for accounting: ERPNext
posts (and maintains) the ledger entries exactly as it does for any manually
created document.

The provider response then only stamps the M-Pesa confirmation fields on that
document. The ledger itself is never reversed, cancelled, amended or re-posted -
whether the payout ends up Paid or Failed.
"""

import frappe
from frappe import _
from frappe.utils import getdate

from frappe_mpsa_payments.accounting.journal_service import create_journal_entry
from frappe_mpsa_payments.accounting.loan_disbursement_service import (
    create_loan_disbursement,
)
from frappe_mpsa_payments.accounting.payment_entry_service import (
    create_payment_entry_for_doc,
)
from frappe_mpsa_payments.utils.doctype_names import (
    JOURNAL_ENTRY_DOCTYPE,
    LOAN_DISBURSEMENT_DOCTYPE,
    PAYMENT_ENTRY_DOCTYPE,
)

SALARY_SLIP_DOCTYPE = "Salary Slip"
LOAN_DOCTYPE = "Loan"

PAYMENT_ENTRY_REFERENCE_DOCTYPES = (
    "Employee Advance",
    "Expense Claim",
    "Purchase Invoice",
    "Purchase Order",
)

# Custom fields stamped on the accounting document
PAID_FIELD = "mpesa_payment_paid"
STATUS_FIELD = "b2c_payment_status"
MESSAGE_FIELD = "b2c_status_message"
REQUEST_FIELD = "b2c_disbursement_request"
DISBURSEMENT_FIELD = "b2c_payment_disbursement"

STATUS_INITIATED = "Initiated"
STATUS_CONFIRMED = "Confirmed"
STATUS_FAILED = "Failed"

# (reference number, reference date) fields per accounting doctype
TRANSACTION_REFERENCE_FIELDS = {
    PAYMENT_ENTRY_DOCTYPE: ("reference_no", "reference_date"),
    JOURNAL_ENTRY_DOCTYPE: ("cheque_no", "cheque_date"),
    LOAN_DISBURSEMENT_DOCTYPE: ("reference_number", "reference_date"),
}

LEDGER_DOCTYPES = tuple(TRANSACTION_REFERENCE_FIELDS)


def _meta_has_field(doctype: str, fieldname: str) -> bool:
    """`has_field` that tolerates optional apps (for example lending)."""
    try:
        return frappe.get_meta(doctype).has_field(fieldname)
    except frappe.DoesNotExistError:
        return False


def is_ledger_first(settings) -> bool:
    """Whether the provider settings ask for posting before the request."""
    if not settings:
        return False

    return bool(settings.get("post_to_ledger_before_request"))


def create_ledger_entry(b2c_disbursement, ref, submit: bool = True) -> tuple[str, str]:
    """Create and submit the accounting document of a reference row.

    Returns:
        tuple: (accounting doctype, accounting document name)
    """
    reference_doctype = ref.reference_doctype

    if reference_doctype == SALARY_SLIP_DOCTYPE:
        doc = create_journal_entry(b2c_disbursement, ref, submit, raise_errors=True)
    elif reference_doctype in PAYMENT_ENTRY_REFERENCE_DOCTYPES:
        doc = create_payment_entry_for_doc(
            b2c_disbursement, ref, submit, raise_errors=True
        )
    elif reference_doctype == LOAN_DOCTYPE:
        doc = create_loan_disbursement(
            b2c_disbursement, ref, submit, raise_errors=True, commit=False
        )
    else:
        frappe.throw(
            _("No B2C accounting entry is configured for {0}").format(reference_doctype)
        )

    if not doc:
        frappe.throw(
            _("Could not create the accounting entry for {0} {1}").format(
                reference_doctype, ref.reference_name
            )
        )

    stamp_disbursement_link(doc.doctype, doc.name, b2c_disbursement.name)
    mark_initiated(doc.doctype, doc.name)

    return doc.doctype, doc.name


def stamp_disbursement_link(
    ledger_doctype: str, ledger_name: str, disbursement_name: str
) -> None:
    """Link the accounting document back to the B2C Payment Disbursement."""
    if not _meta_has_field(ledger_doctype, DISBURSEMENT_FIELD):
        return

    frappe.db.set_value(
        ledger_doctype, ledger_name, DISBURSEMENT_FIELD, disbursement_name
    )


def mark_initiated(ledger_doctype: str, ledger_name: str, message: str = "") -> None:
    """Reset the M-Pesa confirmation fields while a request is in flight."""
    set_confirmation_fields(
        ledger_doctype,
        ledger_name,
        {PAID_FIELD: 0, STATUS_FIELD: STATUS_INITIATED, MESSAGE_FIELD: message},
    )


def mark_confirmed(ledger_doctype: str, ledger_name: str, request) -> None:
    """Tick the M-Pesa confirmation and stamp the transaction number and date."""
    values = {PAID_FIELD: 1, STATUS_FIELD: STATUS_CONFIRMED, MESSAGE_FIELD: ""}

    number_field, date_field = TRANSACTION_REFERENCE_FIELDS.get(
        ledger_doctype, (None, None)
    )

    if number_field and request.get("transaction_id"):
        values[number_field] = request.transaction_id

    if date_field and request.get("transaction_completed_datetime"):
        values[date_field] = getdate(request.transaction_completed_datetime)

    set_confirmation_fields(ledger_doctype, ledger_name, values)

    frappe.get_doc(ledger_doctype, ledger_name).add_comment(
        "Info",
        _("M-Pesa confirmed the payout with transaction {0}.").format(
            request.get("transaction_id") or "-"
        ),
    )


def mark_failed(ledger_doctype: str, ledger_name: str, request) -> None:
    """Keep the ledger as posted and only reflect the failed M-Pesa payout."""
    message = (
        request.get("error_message")
        or request.get("result_desc")
        or _("M-Pesa did not confirm this payout.")
    )

    set_confirmation_fields(
        ledger_doctype,
        ledger_name,
        {PAID_FIELD: 0, STATUS_FIELD: STATUS_FAILED, MESSAGE_FIELD: message},
    )


def set_confirmation_fields(
    ledger_doctype: str, ledger_name: str, values: dict
) -> None:
    """Write the M-Pesa confirmation fields without touching the ledger."""
    confirmation_fields = (PAID_FIELD, STATUS_FIELD, MESSAGE_FIELD)

    values = {
        field: value
        for field, value in values.items()
        if field in confirmation_fields or _meta_has_field(ledger_doctype, field)
    }

    frappe.db.set_value(ledger_doctype, ledger_name, values)


def resolve_ledger_entry(ref, request=None) -> tuple[str | None, str | None]:
    """Find the accounting document posted for a reference row."""
    ledger_doctype = ref.get("accounting_entry_type")
    ledger_name = ref.get("accounting_entry")

    if ledger_doctype and ledger_name and frappe.db.exists(ledger_doctype, ledger_name):
        return ledger_doctype, ledger_name

    request_name = request.get("name") if request else ref.get(REQUEST_FIELD)
    if not request_name:
        return None, None

    for candidate in LEDGER_DOCTYPES:
        if not _meta_has_field(candidate, REQUEST_FIELD):
            continue

        ledger_name = frappe.db.get_value(
            candidate, {REQUEST_FIELD: request_name}, "name"
        )
        if ledger_name:
            return candidate, ledger_name

    return None, None


def link_request_to_ledger(
    reference_doctype: str, reference_name: str, request_name: str
) -> None:
    """Link the freshly created B2C Request to the accounting document."""
    ref = frappe.get_doc(reference_doctype, reference_name)
    ledger_doctype, ledger_name = resolve_ledger_entry(ref)

    if ledger_doctype:
        set_request_link(ledger_doctype, ledger_name, request_name)


def set_request_link(
    ledger_doctype: str, ledger_name: str, request_name: str
) -> None:
    """Store the B2C Disbursement Request on the accounting document."""
    if not _meta_has_field(ledger_doctype, REQUEST_FIELD):
        return

    frappe.db.set_value(ledger_doctype, ledger_name, REQUEST_FIELD, request_name)


def sync_ledger_with_request(ref, request) -> None:
    """Stamp the provider outcome on the accounting document, if one exists."""
    ledger_doctype, ledger_name = resolve_ledger_entry(ref, request)

    if not ledger_doctype:
        return

    if request.status == "Paid":
        mark_confirmed(ledger_doctype, ledger_name, request)
    elif request.status == "Failed":
        mark_failed(ledger_doctype, ledger_name, request)
