((root, factory) => {
	const api = factory();
	if (typeof module === "object" && module.exports) module.exports = api;
	else root.CFDBReaderPayload = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
	function normalizeApiPayload(data) {
		const source = data || {};
		if (source.format === "html" && typeof source.html === "string") {
			return {
				format: "html",
				body: source.html,
				url: source.url || null,
				status: source.status || "ready",
				known: source.known !== false,
				schema: source.schema == null ? null : source.schema,
			};
		}
		if (
			(source.format === "markdown" || (!source.format && source.md)) &&
			typeof source.md === "string"
		) {
			return {
				format: "markdown",
				body: source.md,
				url: source.url || null,
				status: source.status || "ready",
				known: source.known !== false,
				schema: source.schema == null ? null : source.schema,
			};
		}
		return {
			format: null,
			body: null,
			url: source.url || null,
			status: source.status || "unknown",
			known: source.known === true,
			schema: source.schema == null ? null : source.schema,
		};
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

	function prepareBody(payload, markdownRenderer, markdownNormalizer) {
		if (payload.format === "html") return payload.body;
		if (payload.format === "markdown") {
			return markdownNormalizer(markdownRenderer(payload.body));
		}
		return null;
	}

	return { normalizeApiPayload, prepareBody, prepareSourceUrl };
});
