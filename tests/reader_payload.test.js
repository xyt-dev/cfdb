const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {
	normalizeApiPayload,
	prepareBody,
	prepareSourceUrl,
} = require("../reader_payload.js");

test("statement and editorial payloads require structured html", () => {
	assert.equal(
		normalizeApiPayload({ format: "markdown", md: "legacy" }),
		null,
	);
	for (const contentKind of ["statement", "editorial"]) {
		const payload = normalizeApiPayload({
			format: "html",
			contentKind,
			schema: 2,
			html: `<article>${contentKind}</article>`,
			url: "https://codeforces.com/blog/entry/1",
			status: "ready",
		});
		assert.deepEqual(payload, {
			format: "html",
			contentKind,
			body: `<article>${contentKind}</article>`,
			url: "https://codeforces.com/blog/entry/1",
			status: "ready",
			known: true,
			schema: 2,
		});
		assert.equal(prepareBody(payload), `<article>${contentKind}</article>`);
	}
});

test("ready HTML requires explicit status and preserves empty bodies", () => {
	assert.equal(
		normalizeApiPayload({
			format: "html",
			contentKind: "statement",
			html: "<p>x</p>",
		}),
		null,
	);
	assert.equal(
		normalizeApiPayload({
			format: "html",
			contentKind: "statement",
			html: "<p>x</p>",
			status: "",
		}),
		null,
	);
	const payload = normalizeApiPayload({
		format: "html",
		contentKind: "statement",
		html: "",
		status: "ready",
	});
	assert.equal(payload.body, "");
	assert.equal(prepareBody(payload), "");
});

test("browser keeps initialization status explicit and accepts empty ready bodies", () => {
	const source = fs.readFileSync(
		path.join(__dirname, "..", "index.html"),
		"utf8",
	);
	assert.equal((source.match(/payload\.body == null/g) || []).length, 2);
	assert.equal(
		(
			source.match(
				/payload && payload\.status === "v2_not_initialized"/g,
			) || []
		).length,
		2,
	);
});

test("v2_not_initialized stays distinct", () => {
	assert.deepEqual(
		normalizeApiPayload({
			contentKind: "statement",
			status: "v2_not_initialized",
		}),
		{
			format: null,
			contentKind: "statement",
			body: null,
			url: null,
			status: "v2_not_initialized",
			known: false,
			schema: null,
		},
	);
});

test("known absent remains a typed empty payload", () => {
	assert.deepEqual(
		normalizeApiPayload({
			format: null,
			contentKind: "editorial",
			html: null,
			status: "known_absent",
			known: true,
		}),
		{
			format: null,
			contentKind: "editorial",
			body: null,
			url: null,
			status: "known_absent",
			known: true,
			schema: null,
		},
	);
});

test("invalid structured payloads fail closed but keep typed status", () => {
	const payload = normalizeApiPayload({
		format: "html",
		contentKind: "statement",
		html: null,
		status: "invalid_structure",
		known: false,
		schema: 2,
	});
	assert.deepEqual(payload, {
		format: null,
		contentKind: "statement",
		body: null,
		url: null,
		status: "invalid_structure",
		known: false,
		schema: 2,
	});
	assert.equal(prepareBody(payload), null);
});

test("unknown kinds and untyped payloads are rejected", () => {
	assert.equal(
		normalizeApiPayload({
			format: "html",
			contentKind: "unknown",
			html: "<p>x</p>",
			status: "ready",
		}),
		null,
	);
	assert.equal(
		normalizeApiPayload({ format: "html", html: "<p>x</p>" }),
		null,
	);
	assert.equal(normalizeApiPayload({ md: "legacy" }), null);
});

test("source URLs are strictly validated and escaped for an HTML attribute", () => {
	assert.equal(
		prepareSourceUrl(
			'https://codeforces.com/blog/entry/1?lang=en&note="quoted"',
		),
		"https://codeforces.com/blog/entry/1?lang=en&amp;note=%22quoted%22",
	);
	assert.equal(
		prepareSourceUrl("https://codeforces.com/contest/1700/problem/A"),
		"https://codeforces.com/contest/1700/problem/A",
	);
});

test("source URLs reject hostile or non-Codeforces destinations", () => {
	for (const url of [
		"javascript:alert(1)",
		"data:text/html,<script>alert(1)</script>",
		"http://codeforces.com/blog/entry/1",
		"https://codeforces.com.evil.example/blog/entry/1",
		"https://evil.example@codeforces.com/blog/entry/1",
		"/vendor/tex-svg-full.js",
	]) {
		assert.equal(prepareSourceUrl(url), null, url);
	}
});
