/*
 * M-Pesa B2C status indicators.
 *
 * When "Post To Ledger Before Request" is ticked on Mpesa Settings the
 * accounting document (Payment Entry / Journal Entry / Loan Disbursement) is
 * created and submitted before the money is sent - ERPNext takes care of the
 * ledger as it does for any submitted document. The M-Pesa confirmation is
 * stamped on that document afterwards, so these helpers only *display* that
 * state; nothing here touches the ledger.
 */
frappe.provide("mpsa_b2c");

mpsa_b2c.status_colors = {
	Initiated: "orange",
	Confirmed: "green",
	Failed: "red",
};

mpsa_b2c.render_status = function (frm) {
	if (!frm.doc || !frm.doc.b2c_disbursement_request) {
		return;
	}

	const $stats = frm.dashboard && frm.dashboard.stats_area_row;
	if ($stats) {
		$stats.find(".mpsa-b2c-indicator").remove();
	}

	const paid = frm.doc.mpesa_payment_paid;
	const status = frm.doc.b2c_payment_status;

	$(frm.dashboard.add_indicator(paid ? __("M-Pesa Paid") : __("M-Pesa Not Paid"), paid ? "green" : "orange")).addClass(
		"mpsa-b2c-indicator"
	);

	if (status) {
		$(frm.dashboard.add_indicator(
			__("M-Pesa Status: {0}", [__(status)]),
			mpsa_b2c.status_colors[status] || "gray"
		)).addClass("mpsa-b2c-indicator");
	}

	if (frm.doc.b2c_status_message) {
		frm.set_intro(__("M-Pesa: {0}", [frm.doc.b2c_status_message]), "red");
	}
};

mpsa_b2c.setup = function (frm) {
	mpsa_b2c.render_status(frm);
};
