import frappe

from frappe_mpsa_payments.accounting.journal_service import create_journal_entry
from frappe_mpsa_payments.accounting.loan_disbursement_service import (
    create_loan_disbursement,
)
from frappe_mpsa_payments.accounting.payment_entry_service import (
    create_payment_entry_for_doc,
)
from frappe_mpsa_payments.services.b2c_ledger_service import (
    PAYMENT_ENTRY_REFERENCE_DOCTYPES,
    is_ledger_first,
    mark_confirmed,
    set_request_link,
    stamp_disbursement_link,
    sync_ledger_with_request,
)
from frappe_mpsa_payments.utils.doctype_names import (
    B2C_REQUEST_DOCTYPE,
    MPESA_SETTINGS_DOCTYPE,
    STANBIC_SETTINGS_DOCTYPE,
)


def handle_successful_payment(disbursement_name: str, reference_name: str) -> None:
    """
    After reference row is updated, create accounting entries:
    - Salary Slip → Journal Entry
    - Others → Payment Entry
    - Loan → Loan Disbursement

    When "Post To Ledger Before Request" is ticked on the provider settings the
    entries already exist, so only the M-Pesa confirmation is stamped on them.
    """
    ref = frappe.get_doc("B2C Payment Disbursement Reference", reference_name)

    b2c_req = frappe.get_doc(B2C_REQUEST_DOCTYPE, ref.b2c_disbursement_request)
    b2c_disb = frappe.get_doc(ref.parenttype, disbursement_name)

    if b2c_req.mpesa_settings:
        settings_doctype = MPESA_SETTINGS_DOCTYPE
        settings_name = b2c_req.mpesa_settings
    elif b2c_req.stanbic_settings:
        settings_doctype = STANBIC_SETTINGS_DOCTYPE
        settings_name = b2c_req.stanbic_settings
    else:
        frappe.log_error(
            f"No provider settings found on B2C Request {b2c_req.name}",
            "B2C Postprocess",
        )
        return

    settings = frappe.get_doc(settings_doctype, settings_name)

    if is_ledger_first(settings):
        sync_ledger_with_request(ref, b2c_req)
        return

    if ref.get("accounting_entry"):
        frappe.log_error(
            f"{ref.accounting_entry_type} {ref.accounting_entry} already posted "
            f"for B2C Request {b2c_req.name} - skipping duplicate posting",
            "B2C Postprocess Duplicate",
        )
        sync_ledger_with_request(ref, b2c_req)
        return

    submit = settings.submit_b2c_accounting_entries

    doctype = ref.reference_doctype
    if doctype == "Salary Slip":
        ledger_doc = create_journal_entry(b2c_disb, ref, submit)
    elif doctype in PAYMENT_ENTRY_REFERENCE_DOCTYPES:
        ledger_doc = create_payment_entry_for_doc(b2c_disb, ref, submit)
    elif doctype == "Loan":
        ledger_doc = create_loan_disbursement(b2c_disb, ref, submit)
    else:
        frappe.log_error(
            f"No post-process for reference {doctype}", "B2C Postprocess Error"
        )
        return

    if not ledger_doc:
        return

    frappe.db.set_value(
        ref.doctype,
        ref.name,
        {
            "accounting_entry_type": ledger_doc.doctype,
            "accounting_entry": ledger_doc.name,
        },
    )
    stamp_disbursement_link(ledger_doc.doctype, ledger_doc.name, b2c_disb.name)
    set_request_link(ledger_doc.doctype, ledger_doc.name, b2c_req.name)
    mark_confirmed(ledger_doc.doctype, ledger_doc.name, b2c_req)

