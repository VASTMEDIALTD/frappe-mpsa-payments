frappe.ui.form.on("Loan Disbursement", {
	refresh(frm) {
		if (window.mpsa_b2c) {
			mpsa_b2c.setup(frm);
		}
	},
});
