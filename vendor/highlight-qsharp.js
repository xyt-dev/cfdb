(function (root, factory) {
	"use strict";
	if (typeof module === "object" && module.exports) {
		module.exports = factory;
		return;
	}
	if (root.hljs) {
		root.hljs.registerLanguage("qsharp", factory);
	}
})(globalThis, function (hljs) {
	"use strict";
	return {
		name: "Q#",
		aliases: ["qs"],
		keywords: {
			$pattern: /[A-Za-z_][A-Za-z0-9_]*/,
			keyword:
				"adjoint apply as auto body borrow borrowing controlled distribute elif else export fail fixup for function if import in internal intrinsic invert is let mutable namespace newtype open operation repeat return self set struct until use using while within",
			type: "BigInt Bool Double Int Pauli Qubit Range Result String Unit",
			literal: "false One PauliI PauliX PauliY PauliZ true Zero",
			built_in:
				"Adjoint Controlled DumpMachine H Length M Message MResetX MResetY MResetZ Reset ResetAll X Y Z",
		},
		contains: [
			hljs.C_LINE_COMMENT_MODE,
			hljs.C_BLOCK_COMMENT_MODE,
			hljs.QUOTE_STRING_MODE,
			hljs.C_NUMBER_MODE,
			{
				className: "meta",
				begin: /@[A-Za-z_][A-Za-z0-9_]*/,
			},
		],
	};
});
