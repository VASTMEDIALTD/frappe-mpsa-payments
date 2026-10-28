import frappe


def create_loan_disbursement(
    b2c_disbursement,
    b2c_disbursement_ref,
    submit,
    raise_errors=False,
    commit=True,
):
    """Create the Loan Disbursement for a B2C reference row.

    Args:
        b2c_disbursement: B2C Payment Disbursement document
        b2c_disbursement_ref: B2C Payment Disbursement Reference document
        submit: whether to submit the Loan Disbursement
        raise_errors: re-raise creation errors so the caller can abort
        commit: commit after creation - skipped when posting inside a
            transaction that must stay atomic (post to ledger before request)

    Returns:
        Loan Disbursement document or None when creation failed silently
    """
    from lending.loan_management.doctype.loan.loan import make_loan_disbursement

    try:
        loan = frappe.get_doc("Loan", b2c_disbursement_ref.reference_name)

        disbursement_entry = make_loan_disbursement(
            loan=loan.name,
            company=loan.company,
            applicant_type=loan.applicant_type,
            applicant=loan.applicant,
            pending_amount=b2c_disbursement_ref.allocated_amount,
        )

        disbursement_entry.reference_date = b2c_disbursement_ref.reference_date
        disbursement_entry.reference_number = b2c_disbursement_ref.reference_no
        disbursement_entry.b2c_payment_disbursement = b2c_disbursement.name

        disbursement_entry.insert(ignore_permissions=True)

        if submit:
            disbursement_entry.submit()

        if commit:
            frappe.db.commit()

        return disbursement_entry

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Loan Disbursement Creation Failed")
        if raise_errors:
            raise
