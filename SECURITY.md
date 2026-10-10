# Security policy

Please report vulnerabilities privately using GitHub's **Security → Report a vulnerability** on this repository, and don't post details publicly or include anyone's personal data. Only the latest version receives fixes.

Notes for people who run a matchmaker: don't use `--demo` on a public server; put it behind HTTPS; keep stream keys secret (they work only for their own role and expire after two hours); and restrict any TURN relay to authenticated sessions.
