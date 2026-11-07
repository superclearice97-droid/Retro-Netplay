// The chat filter (on by default, Settings > Chat): hides common swear words and slurs behind asterisks on this
// device. PREFIX entries match the start of a word ("fuck" also hides "fucking"); WHOLE entries only match the
// whole word (so "spicy" stays). The lobby can mask more words of its own. Base64 keeps the list out of plain text.
const PREFIX = atob("ZnVjayxzaGl0LGJpdGNoLGN1bnQsYXNzaG9sZSxiYXN0YXJkLGRpY2toZWFkLHB1c3N5LHdhbmtlcixtb3RoZXJmdWNrLHNsdXQsd2hvcmUsbmlnZ2VyLG5pZ2dhLGZhZ2dvdCxyZXRhcmQsdHJhbm55LHdldGJhY2ssa2lsbCB5b3Vyc2VsZg==").split(",");
const WHOLE = atob("ZmFnLGZhZ3MsZHlrZSxraWtlLHNwaWMsY2hpbmssZ29vayxjb29uLGt5cw==").split(",");
const esc = w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const RE = new RegExp("\\b(?:" + PREFIX.map(esc).join("|") + ")\\w*|\\b(?:" + WHOLE.map(esc).join("|") + ")\\b", "gi");

export function clean(text) {
  return text.replace(RE, m => "*".repeat(m.length));
}
