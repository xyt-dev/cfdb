((root, factory) => {
	const api = factory();
	if (typeof module === "object" && module.exports) module.exports = api;
	else root.CFDBReaderPayload = api;
})(typeof globalThis === "undefined" ? this : globalThis, () => {
	const contentKinds = new Set(["statement", "editorial"]);
	const nonReadyStatuses = new Set([
		"known_absent",
		"v2_not_initialized",
		"invalid_structure",
		"invalid_ref",
	]);

	function normalizeApiPayload(data) {
		const source = data && typeof data === "object" ? data : {};
		if (!contentKinds.has(source.contentKind)) return null;
		const status = source.status;
		const common = {
			contentKind: source.contentKind,
			url: typeof source.url === "string" ? source.url : null,
			status,
			known: source.known === true,
			schema: source.schema == null ? null : source.schema,
		};
		if (
			status === "ready" &&
			source.format === "html" &&
			typeof source.html === "string"
		) {
			return {
				format: "html",
				...common,
				body: source.html,
				known: source.known !== false,
			};
		}
		if (nonReadyStatuses.has(status)) {
			return {
				format: null,
				...common,
				body: null,
			};
		}
		return null;
	}

	function prepareSourceUrl(value) {
		if (typeof value !== "string" || !value) return null;
		let parsed;
		try {
			parsed = new URL(value);
		} catch (error) {
			return null;
		}
		if (
			parsed.origin !== "https://codeforces.com" ||
			parsed.username ||
			parsed.password
		) {
			return null;
		}
		return parsed.href
			.replace(/&/g, "&amp;")
			.replace(/"/g, "&quot;")
			.replace(/'/g, "&#39;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;");
	}

	function prepareBody(payload) {
		if (!payload || payload.format !== "html") return null;
		return payload.body;
	}

	return { normalizeApiPayload, prepareBody, prepareSourceUrl };
});
