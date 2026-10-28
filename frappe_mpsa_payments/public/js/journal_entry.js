frappe.ui.form.on("Journal Entry", {
	refresh(frm) {
		if (window.mpsa_b2c) {
			mpsa_b2c.setup(frm);
		}
	},
});
