const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {
	normalizeApiPayload,
	prepareBody,
	prepareSourceUrl,
} = require("../reader_payload.js");

test("statement and editorial payloads require structured html", () => {
	assert.equal(normalizeApiPayload({ format: "markdown", md: "legacy" }), null);
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

test("browser keeps pending status explicit and accepts empty ready bodies", () => {
	const source = fs.readFileSync(
		path.join(__dirname, "..", "index.html"),
		"utf8",
	);
	assert.equal((source.match(/payload\.body == null/g) || []).length, 2);
	assert.equal(
		(source.match(/payload && payload\.status === "pending"/g) || []).length,
		2,
	);
	assert.equal(source.includes("v2_not_initialized"), false);
});

test("pending and transient failures remain typed", () => {
	for (const status of ["pending", "transient_failure"]) {
		assert.deepEqual(normalizeApiPayload({ contentKind: "statement", status }), {
			format: null,
			contentKind: "statement",
			body: null,
			url: null,
			status,
			known: false,
			schema: null,
		});
	}
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
	assert.equal(normalizeApiPayload({ format: "html", html: "<p>x</p>" }), null);
	assert.equal(normalizeApiPayload({ md: "legacy" }), null);
});

test("source URLs are strictly validated and escaped for an HTML attribute", () => {
	assert.equal(
		prepareSourceUrl('https://codeforces.com/blog/entry/1?lang=en&note="quoted"'),
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

const qsharp1001Code = `namespace Solution {
    open Microsoft.Quantum.Primitive;
    open Microsoft.Quantum.Canon;

    operation Solve (q : Qubit, sign : Int) : ()
    {
        body
        {
            // your code here
        }
    }
}`;

function loadQsharpBrowserContext() {
	const context = vm.createContext({ console });
	const core = fs.readFileSync(
		path.join(__dirname, "..", "vendor", "highlight.min.js"),
		"utf8",
	);
	const grammar = fs.readFileSync(
		path.join(__dirname, "..", "vendor", "highlight-qsharp.js"),
		"utf8",
	);
	vm.runInContext(core, context, { filename: "highlight.min.js" });
	vm.runInContext(grammar, context, { filename: "highlight-qsharp.js" });
	return context;
}

function codeElement(id, className, code, inSample = false) {
	return {
		id,
		className,
		textContent: code,
		innerHTML: code,
		children: [],
		dataset: {},
		parentNode: { className: "" },
		classList: { add: () => {} },
		closest: () => (inSample ? { className: "cf-sample-input" } : null),
	};
}

test("local Q# grammar highlights 1001A through CommonJS", () => {
	const hljs = require("../vendor/highlight.min.js");
	const qsharp = require("../vendor/highlight-qsharp.js");
	hljs.registerLanguage("qsharp", qsharp);

	const highlighted = hljs.highlight(qsharp1001Code, {
		language: "qsharp",
	}).value;

	assert.match(highlighted, /hljs-keyword[^>]*>namespace/);
	assert.match(highlighted, /hljs-keyword[^>]*>operation/);
	assert.match(highlighted, /hljs-type[^>]*>Qubit/);
	const source = fs.readFileSync(
		path.join(__dirname, "..", "index.html"),
		"utf8",
	);
	const corePositions = [
		...source.matchAll(/\/vendor\/highlight\.min\.js/g),
	].map((match) => match.index);
	const grammarPositions = [
		...source.matchAll(/\/vendor\/highlight-qsharp\.js/g),
	].map((match) => match.index);
	assert.equal(corePositions.length, 2);
	assert.equal(grammarPositions.length, 2);
	for (let index = 0; index < corePositions.length; index += 1) {
		assert.ok(grammarPositions[index] > corePositions[index]);
	}
});

test("local Q# grammar registers in isolated browser globals", () => {
	for (const contextName of ["outer", "iframe"]) {
		const context = loadQsharpBrowserContext();
		assert.ok(context.hljs.getLanguage("qsharp"), contextName);
		const element = codeElement(contextName, "language-qsharp", qsharp1001Code);

		context.hljs.highlightElement(element);

		assert.match(element.innerHTML, /hljs-keyword[^>]*>operation/, contextName);
		assert.match(element.innerHTML, /hljs-type[^>]*>Qubit/, contextName);
		assert.equal(element.dataset.highlighted, "yes", contextName);
	}
});

test("iframe syntax highlighting colors Q# but skips statement samples", () => {
	const source = fs.readFileSync(
		path.join(__dirname, "..", "index.html"),
		"utf8",
	);
	const start = source.indexOf("/* 代码块语法高亮");
	const end = source.indexOf("/* 图片加载失败", start);
	assert.ok(start >= 0 && end > start);
	const script = source.slice(start, end);
	const sample = codeElement("sample", "language-qsharp", qsharp1001Code, true);
	const editorial = codeElement("editorial", "language-qsharp", qsharp1001Code);
	const plain = codeElement("plain", "", "1 2\n3 4\n");
	const context = loadQsharpBrowserContext();
	context.document = {
		querySelectorAll: (selector) => {
			assert.equal(selector, "pre code");
			return [sample, editorial, plain];
		},
	};

	vm.runInContext(script, context, { filename: "reader-highlight.js" });

	assert.equal(sample.innerHTML, qsharp1001Code);
	assert.match(editorial.innerHTML, /hljs-keyword[^>]*>operation/);
	assert.equal(editorial.dataset.highlighted, "yes");
	assert.equal(plain.innerHTML, "1 2\n3 4\n");
});

test("rebuild control uses a themed floating dialog left of Default", () => {
	const source = fs.readFileSync(
		path.join(__dirname, "..", "index.html"),
		"utf8",
	);
	const rebuildPosition = source.indexOf('id="rebuild-btn"');
	const defaultPosition = source.indexOf('onclick="resetFilters()"');
	assert.ok(rebuildPosition >= 0);
	assert.ok(defaultPosition > rebuildPosition);
	assert.match(source, /class="reset-btn mini rebuild-btn"/);
	assert.match(source, /<dialog[\s\S]*id="rebuild-dialog"/);
	assert.match(source, /\.rebuild-dialog::backdrop/);
	assert.match(
		source,
		/\.rebuild-dialog[^{]*\{[^}]*background: var\(--surface\)/,
	);
	assert.equal((source.match(/rebuildConfirm:/g) || []).length, 2);
	assert.equal((source.match(/rebuildAlreadyRunning:/g) || []).length, 2);

	const start = source.indexOf("async function fetchRebuildPreview");
	const end = source.indexOf("/* 恢复默认筛选 */", start);
	assert.ok(start >= 0 && end > start);
	const script = source.slice(start, end);
	assert.doesNotMatch(script, /\bconfirm\(/);
	assert.doesNotMatch(script, /\balert\(/);
});

test("themed rebuild dialog defaults confirmation focus to Cancel", async () => {
	const source = fs.readFileSync(
		path.join(__dirname, "..", "index.html"),
		"utf8",
	);
	const start = source.indexOf("function showRebuildDialog");
	const end = source.indexOf("async function requestRebuild", start);
	assert.ok(start >= 0 && end > start);
	let closeListener = null;
	let shown = false;
	let cancelFocused = false;
	let triggerFocused = false;
	const elements = {
		"rebuild-dialog": {
			returnValue: "",
			addEventListener: (name, listener, options) => {
				assert.equal(name, "close");
				assert.equal(options.once, true);
				closeListener = listener;
			},
			showModal: () => {
				shown = true;
			},
		},
		"rebuild-dialog-title": { textContent: "" },
		"rebuild-dialog-message": { textContent: "" },
		"rebuild-preview": { hidden: true },
		"rebuild-dialog-cancel": {
			hidden: true,
			focus: () => {
				cancelFocused = true;
			},
		},
		"rebuild-dialog-confirm": { hidden: true, disabled: false },
		"rebuild-dialog-close": { hidden: true, focus: () => {} },
	};
	const context = vm.createContext({
		$: (id) => elements[id],
	});
	vm.runInContext(source.slice(start, end), context, {
		filename: "rebuild-dialog.js",
	});
	const result = context.showRebuildDialog({
		title: "Rebuild missing content",
		message: "Retry now?",
		mode: "confirm",
		trigger: {
			disabled: false,
			focus: () => {
				triggerFocused = true;
			},
		},
	});

	assert.equal(shown, true);
	assert.equal(cancelFocused, true);
	assert.equal(
		elements["rebuild-dialog-title"].textContent,
		"Rebuild missing content",
	);
	assert.equal(elements["rebuild-dialog-message"].textContent, "Retry now?");
	assert.equal(elements["rebuild-dialog-cancel"].hidden, false);
	assert.equal(elements["rebuild-dialog-confirm"].hidden, false);
	assert.equal(elements["rebuild-dialog-close"].hidden, true);
	elements["rebuild-dialog"].returnValue = "confirm";
	closeListener();
	assert.equal(await result, true);
	assert.equal(triggerFocused, true);

	triggerFocused = false;
	const escaped = context.showRebuildDialog({
		title: "Rebuild missing content",
		message: "Retry now?",
		mode: "confirm",
		trigger: {
			disabled: false,
			focus: () => {
				triggerFocused = true;
			},
		},
	});
	elements["rebuild-dialog"].returnValue = "";
	closeListener();
	assert.equal(await escaped, false);
	assert.equal(triggerFocused, true);
});

test("rebuild preview separates clickable tabs and clears after enqueue", () => {
	const source = fs.readFileSync(
		path.join(__dirname, "..", "index.html"),
		"utf8",
	);
	const start = source.indexOf("function selectRebuildPreviewTab");
	const end = source.indexOf("/* 恢复默认筛选 */", start);
	assert.ok(start >= 0 && end > start);

	function listNode() {
		return {
			hidden: false,
			children: [],
			replaceChildren(...children) {
				this.children = children;
			},
			appendChild(child) {
				this.children.push(child);
			},
			get childElementCount() {
				return this.children.length;
			},
			setAttribute() {},
		};
	}
	const elements = {
		"rebuild-preview": listNode(),
		"rebuild-preview-statements-summary": { textContent: "" },
		"rebuild-preview-editorials-summary": { textContent: "" },
		"rebuild-preview-tab-statements": { setAttribute() {}, textContent: "" },
		"rebuild-preview-tab-editorials": { setAttribute() {}, textContent: "" },
		"rebuild-preview-panel-statements": { hidden: false },
		"rebuild-preview-panel-editorials": { hidden: true },
		"rebuild-preview-list-statements": listNode(),
		"rebuild-preview-list-editorials": listNode(),
		"rebuild-dialog-confirm": { disabled: false },
		"rebuild-dialog": { open: false },
	};
	const navigation = [];
	const context = vm.createContext({
		ALL: [
			{ id: "1605E", contestId: 1605, index: "E", name: "E" },
			{ id: "1605A", contestId: 1605, index: "A", name: "A" },
		],
		$: (id) => elements[id],
		document: {
			createElement: () => ({
				type: "",
				className: "",
				textContent: "",
				onclick: null,
			}),
		},
		el: (_tag, className, text) => ({
			className,
			textContent: text,
		}),
		showDetail: (problem) => navigation.push({ view: "detail", problem }),
		switchTab: (tab) => navigation.push({ view: "tab", tab }),
		t: (key) => key,
	});
	vm.runInContext(source.slice(start, end), context, {
		filename: "rebuild-preview.js",
	});

	context.renderRebuildPreview({
		statements: { items: [{ id: "1605E", label: "1605E — E" }] },
		editorials: { items: [{ id: "1605", label: "Contest 1605" }] },
	});
	assert.equal(
		elements["rebuild-preview-statements-summary"].textContent,
		"rebuildStatements: 1",
	);
	assert.equal(elements["rebuild-preview-list-statements"].children.length, 1);
	assert.equal(
		elements["rebuild-preview-tab-editorials"].textContent,
		"rebuildEditorials (1)",
	);
	assert.equal(elements["rebuild-preview-panel-statements"].hidden, false);
	assert.equal(elements["rebuild-preview-panel-editorials"].hidden, true);

	elements["rebuild-preview-list-statements"].children[0].onclick();
	context.selectRebuildPreviewTab("editorials");
	elements["rebuild-preview-list-editorials"].children[0].onclick();
	assert.equal(navigation[0].problem.id, "1605E");
	assert.deepEqual(navigation[1], { view: "detail", problem: context.ALL[0] });
	assert.deepEqual(navigation[2], { view: "tab", tab: "editorial" });

	context.clearRebuildPreview();
	assert.equal(elements["rebuild-preview-list-statements"].children.length, 1);
	assert.equal(elements["rebuild-preview-list-editorials"].children.length, 1);
	assert.equal(elements["rebuild-dialog-confirm"].disabled, true);
});

test("rebuild control accepts started and coalesced incremental crawls", async () => {
	const source = fs.readFileSync(
		path.join(__dirname, "..", "index.html"),
		"utf8",
	);
	const start = source.indexOf("async function fetchRebuildPreview");
	const end = source.indexOf("/* 恢复默认筛选 */", start);
	const script = source.slice(start, end);

	for (const [operation, statusMessage] of [
		["started", "rebuildStarted"],
		["already_running", "rebuildAlreadyRunning"],
	]) {
		const requests = [];
		const dialogs = [];
		const button = { disabled: false };
		const context = vm.createContext({
			JSON,
			rebuildInFlight: false,
			rebuildRequestVersion: 0,
			rebuildRequestPending: false,
			console,
			fetch: async (url, options) => {
				if (url === "/api/rebuild/preview") {
					return {
						ok: true,
						status: 200,
						json: async () => ({
							ok: true,
							status: "ready",
							statements: { items: [{ id: "1605E", label: "1605E" }] },
							editorials: { items: [{ id: "1605", label: "Contest 1605" }] },
						}),
					};
				}
				requests.push({ url, options });
				return {
					ok: true,
					status: 202,
					json: async () => ({ ok: true, status: "accepted", operation }),
				};
			},
			showRebuildDialog: async (options) => {
				dialogs.push({ mode: options.mode, message: options.message });
				return options.mode === "confirm";
			},
			clearRebuildPreview: () => {},
			t: (key) => key,
		});
		vm.runInContext(script, context, { filename: "rebuild-control.js" });

		await context.requestRebuild(button);

		assert.equal(requests.length, 1);
		assert.equal(requests[0].url, "/api/rebuild");
		assert.equal(requests[0].options.method, "POST");
		assert.equal(requests[0].options.headers["Content-Type"], "application/json");
		assert.deepEqual(JSON.parse(requests[0].options.body), { confirm: true });
		assert.deepEqual(dialogs, [
			{ mode: "confirm", message: "rebuildConfirm" },
			{ mode: "status", message: statusMessage },
		]);
		assert.equal(button.disabled, true);
		assert.equal(context.rebuildInFlight, true);
	}
});

test("rebuild control cancels cleanly and themes request failures", async () => {
	const source = fs.readFileSync(
		path.join(__dirname, "..", "index.html"),
		"utf8",
	);
	const start = source.indexOf("async function fetchRebuildPreview");
	const end = source.indexOf("/* 恢复默认筛选 */", start);
	const script = source.slice(start, end);

	let cancelledFetches = 0;
	const cancelledContext = vm.createContext({
		JSON,
		rebuildInFlight: false,
		rebuildRequestVersion: 0,
		rebuildRequestPending: false,
		console,
		fetch: async () => {
			cancelledFetches += 1;
			return {
				ok: true,
				json: async () => ({
					status: "ready",
					statements: { items: [] },
					editorials: { items: [] },
				}),
			};
		},
		showRebuildDialog: async () => false,
		t: (key) => key,
	});
	vm.runInContext(script, cancelledContext, { filename: "rebuild-control.js" });
	const cancelledButton = { disabled: false };
	await cancelledContext.requestRebuild(cancelledButton);
	assert.equal(cancelledFetches, 1);
	assert.equal(cancelledButton.disabled, false);

	const scenarios = [
		{
			name: "malformed accepted response",
			fetch: async () => ({
				ok: true,
				status: 202,
				json: async () => ({ ok: true, status: "accepted", operation: "wrong" }),
			}),
		},
		{
			name: "HTTP failure",
			fetch: async () => ({ ok: false, status: 500, json: async () => ({}) }),
		},
		{
			name: "network failure",
			fetch: async () => {
				throw new Error("offline");
			},
		},
	];

	for (const scenario of scenarios) {
		const dialogs = [];
		const button = { disabled: false };
		const context = vm.createContext({
			JSON,
			rebuildInFlight: false,
			rebuildRequestVersion: 0,
			rebuildRequestPending: false,
			console,
			fetch: async (url, options) => {
				if (url === "/api/rebuild/preview") {
					return {
						ok: true,
						json: async () => ({
							status: "ready",
							statements: { items: [] },
							editorials: { items: [] },
						}),
					};
				}
				return scenario.fetch(url, options);
			},
			showRebuildDialog: async (options) => {
				dialogs.push({ mode: options.mode, message: options.message });
				return options.mode === "confirm";
			},
			t: (key) => key,
		});
		vm.runInContext(script, context, { filename: "rebuild-control.js" });

		await context.requestRebuild(button);

		assert.equal(button.disabled, false, scenario.name);
		assert.equal(context.rebuildInFlight, false, scenario.name);
		assert.deepEqual(
			dialogs,
			[
				{ mode: "confirm", message: "rebuildConfirm" },
				{ mode: "status", message: "rebuildFailed" },
			],
			scenario.name,
		);
	}
});

test("stale terminal progress cannot re-enable an accepted rebuild", async () => {
	const source = fs.readFileSync(
		path.join(__dirname, "..", "index.html"),
		"utf8",
	);
	const progressStart = source.indexOf("let lastDone = -1;");
	const progressEnd = source.indexOf("/* ═══ 标签筛选", progressStart);
	const requestStart = source.indexOf("async function fetchRebuildPreview");
	const requestEnd = source.indexOf("/* 恢复默认筛选 */", requestStart);
	assert.ok(progressStart >= 0 && progressEnd > progressStart);
	assert.ok(requestStart >= 0 && requestEnd > requestStart);

	let releaseProgress;
	let markProgressRequested;
	const progressBody = new Promise((resolve) => {
		releaseProgress = resolve;
	});
	const progressRequested = new Promise((resolve) => {
		markProgressRequested = resolve;
	});
	const button = { disabled: false };
	const elements = {
		"progress-bar": { classList: { add: () => {}, remove: () => {} } },
		"progress-text": { textContent: "" },
		"progress-fill": { style: {} },
		"rebuild-btn": button,
	};
	const context = vm.createContext({
		JSON,
		Date: { now: () => 0 },
		$: (id) => elements[id],
		console,
		fetch: async (url) => {
			if (url === "/api/progress") {
				markProgressRequested();
				return { json: async () => progressBody };
			}
			if (url === "/api/rebuild/preview") {
				return {
					ok: true,
					json: async () => ({
						status: "ready",
						statements: { items: [] },
						editorials: { items: [] },
					}),
				};
			}
			if (url === "/api/rebuild") {
				return {
					ok: true,
					status: 202,
					json: async () => ({
						ok: true,
						status: "accepted",
						operation: "started",
					}),
				};
			}
			throw new Error(`unexpected URL: ${url}`);
		},
		setTimeout: () => {},
		showRebuildDialog: async (options) => options.mode === "confirm",
		clearRebuildPreview: () => {},
		t: (key) =>
			key === "progress"
				? "{stage} {done}/{total} {fetched} {cached} {failed}"
				: key,
	});
	vm.runInContext(source.slice(progressStart, progressEnd), context, {
		filename: "rebuild-progress.js",
	});
	vm.runInContext(source.slice(requestStart, requestEnd), context, {
		filename: "rebuild-request.js",
	});

	const stalePoll = context.pollProgress();
	await progressRequested;
	await context.requestRebuild(button);
	assert.equal(button.disabled, true);
	releaseProgress({
		stage: "done",
		done: 1,
		total: 1,
		fetched: 1,
		cached: 0,
		failed: 0,
	});
	await stalePoll;

	assert.equal(button.disabled, true);
	assert.equal(vm.runInContext("rebuildInFlight", context), true);
});

test("priority signal is exact, deduplicated, and retryable after failure", async () => {
	const source = fs.readFileSync(
		path.join(__dirname, "..", "index.html"),
		"utf8",
	);
	const start = source.indexOf("const RETRYABLE_CONTENT_STATUSES");
	const end = source.indexOf("/* ═══ 视图切换", start);
	assert.ok(start >= 0 && end > start);
	const script = source.slice(start, end);
	const requests = [];
	const context = vm.createContext({
		JSON,
		currentProblem: { contestId: 1605, index: "E" },
		fetch: async (url, options) => {
			requests.push({ url, options });
			return { ok: true };
		},
	});
	vm.runInContext(script, context, { filename: "content-priority.js" });

	const pending = { status: "pending", body: null };
	await context.prioritizeUnavailableContent("statement", pending);
	await context.prioritizeUnavailableContent("statement", pending);
	await context.prioritizeUnavailableContent("editorial", {
		status: "transient_failure",
		body: null,
	});
	await context.prioritizeUnavailableContent("statement", {
		status: "ready",
		body: "<p>ready</p>",
	});
	await context.prioritizeUnavailableContent("editorial", {
		status: "known_absent",
		body: null,
	});

	assert.equal(requests.length, 2);
	assert.deepEqual(
		requests.map(({ url, options }) => ({
			url,
			method: options.method,
			contentType: options.headers["Content-Type"],
			body: JSON.parse(options.body),
		})),
		[
			{
				url: "/api/prioritize",
				method: "POST",
				contentType: "application/json",
				body: { kind: "statement", contentId: "1605E" },
			},
			{
				url: "/api/prioritize",
				method: "POST",
				contentType: "application/json",
				body: { kind: "editorial", contentId: "1605" },
			},
		],
	);

	let attempts = 0;
	const retryContext = vm.createContext({
		JSON,
		currentProblem: { contestId: 1605, index: "E" },
		fetch: async () => {
			attempts += 1;
			return { ok: attempts > 1 };
		},
	});
	vm.runInContext(script, retryContext, {
		filename: "content-priority-retry.js",
	});
	assert.equal(
		await retryContext.prioritizeUnavailableContent("statement", pending),
		false,
	);
	assert.equal(
		await retryContext.prioritizeUnavailableContent("statement", pending),
		true,
	);
	assert.equal(attempts, 2);
});

test("reader prioritizes retryable statement and editorial payloads", async () => {
	const source = fs.readFileSync(
		path.join(__dirname, "..", "index.html"),
		"utf8",
	);
	const start = source.indexOf("function retryPendingContent");
	const end = source.indexOf("/* ═══ hljs", start);
	assert.ok(start >= 0 && end > start);
	const priorities = [];
	const reader = {
		appendChild: () => {},
		replaceChildren: () => {},
	};
	const context = vm.createContext({
		CFDBReaderPayload: { normalizeApiPayload: (payload) => payload },
		currentProblem: { contestId: 1605, index: "E" },
		currentTab: "statement",
		loadSeq: 0,
		tabCache: {},
		$: (id) => {
			assert.equal(id, "reader");
			return reader;
		},
		el: () => ({ style: {} }),
		fetch: async (url) => ({
			json: async () =>
				url.startsWith("/api/statement")
					? { status: "pending", body: null }
					: { status: "transient_failure", body: null },
		}),
		prioritizeUnavailableContent: (kind, payload) => {
			priorities.push({ kind, status: payload.status });
		},
		setTimeout: () => {},
		t: (key) => key,
	});
	vm.runInContext(source.slice(start, end), context, {
		filename: "reader-priority.js",
	});

	context.loadTab("statement");
	await new Promise((resolve) => setImmediate(resolve));
	context.loadTab("editorial");
	await new Promise((resolve) => setImmediate(resolve));

	assert.deepEqual(priorities, [
		{ kind: "statement", status: "pending" },
		{ kind: "editorial", status: "transient_failure" },
	]);
});

test("stale failed priority request cannot clear a newer navigation signal", async () => {
	const source = fs.readFileSync(
		path.join(__dirname, "..", "index.html"),
		"utf8",
	);
	const start = source.indexOf("const RETRYABLE_CONTENT_STATUSES");
	const end = source.indexOf("/* ═══ 视图切换", start);
	assert.ok(start >= 0 && end > start);
	let rejectFirst;
	let markFirstStarted;
	const firstStarted = new Promise((resolve) => {
		markFirstStarted = resolve;
	});
	let calls = 0;
	const context = vm.createContext({
		JSON,
		currentProblem: { contestId: 1605, index: "E" },
		fetch: async () => {
			calls += 1;
			if (calls === 1) {
				markFirstStarted();
				return new Promise((_, reject) => {
					rejectFirst = reject;
				});
			}
			return { ok: true };
		},
	});
	vm.runInContext(source.slice(start, end), context, {
		filename: "content-priority-race.js",
	});

	const pending = { status: "pending", body: null };
	const first = context.prioritizeUnavailableContent("statement", pending);
	await firstStarted;
	vm.runInContext("prioritySignals.clear()", context);
	assert.equal(
		await context.prioritizeUnavailableContent("statement", pending),
		true,
	);
	rejectFirst(new Error("stale request failed"));
	assert.equal(await first, false);
	assert.equal(
		await context.prioritizeUnavailableContent("statement", pending),
		false,
	);
	assert.equal(calls, 2);
});
