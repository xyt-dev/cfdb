const test = require("node:test");
const assert = require("node:assert/strict");
const {
	normalizeApiPayload,
	prepareBody,
	prepareSourceUrl,
} = require("../reader_payload.js");

test("v2 html bypasses markdown parsing and heading normalization", () => {
	const payload = normalizeApiPayload({
		format: "html",
		schema: 2,
		html: "<h4>Official Level</h4>",
		url: "https://codeforces.com/blog/entry/1",
		status: "ready",
	});
	const rendered = prepareBody(
		payload,
		() => {
			throw new Error("markdown renderer called");
		},
		() => {
			throw new Error("heading normalizer called");
		},
	);
	assert.equal(rendered, "<h4>Official Level</h4>");
	assert.equal(payload.schema, 2);
});

test("legacy markdown still uses both markdown stages", () => {
	const payload = normalizeApiPayload({
		format: "markdown",
		md: "## 1A - A",
		url: "u",
	});
	const rendered = prepareBody(
		payload,
		(md) => `<h2>${md}</h2>`,
		(html) => `<hr>${html}`,
	);
	assert.equal(rendered, "<hr><h2>## 1A - A</h2>");
});

test("known absent normalizes to an empty payload", () => {
	assert.deepEqual(
		normalizeApiPayload({
			format: null,
			html: null,
			status: "known_absent",
			known: true,
		}),
		{
			format: null,
			body: null,
			url: null,
			status: "known_absent",
			known: true,
			schema: null,
		},
	);
});

test("legacy payloads without a format still normalize as markdown", () => {
	assert.deepEqual(normalizeApiPayload({ md: "legacy", known: false }), {
		format: "markdown",
		body: "legacy",
		url: null,
		status: "ready",
		known: false,
		schema: null,
	});
});

test("invalid structured payloads fail closed", () => {
	const payload = normalizeApiPayload({
		format: "html",
		html: null,
		status: "invalid_structure",
		known: false,
		schema: 2,
	});
	assert.deepEqual(payload, {
		format: null,
		body: null,
		url: null,
		status: "invalid_structure",
		known: false,
		schema: 2,
	});
	assert.equal(
		prepareBody(
			payload,
			() => {
				throw new Error("markdown renderer called");
			},
			() => {
				throw new Error("heading normalizer called");
			},
		),
		null,
	);
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
