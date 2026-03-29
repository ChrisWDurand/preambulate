# preambulate

If someone steals memory.db (local Kuzu database):
It's unencrypted on disk right now. Everything in it is readable — every Decision node, every rationale, every file path, every architectural decision you've ever recorded. For a solo developer that's their entire project reasoning history. For a team that's potentially proprietary architecture and business logic.
The fix is Kuzu database encryption — Kuzu supports encryption at rest. Not implemented yet. Worth adding before you have paying customers storing sensitive project memory.
If someone steals the R2 stored graph:
Same problem — the JSON export sitting in R2 is plaintext. An R2 bucket breach exposes every user's graph.
The fix is encryption before upload. preambulate sync push encrypts the JSON payload with a user-held key before sending. The server never sees plaintext. Even a full R2 breach exposes only ciphertext.
The key hierarchy that follows:

User generates a local encryption key on first preambulate init
Key stored locally only — never sent to server
All R2 content is encrypted with that key
You can't read your users' graphs even if you wanted to

That's a strong privacy guarantee and a selling point — "we never see your project memory."
The API key theft scenario:
Someone steals PREAMBULATE_API_KEY from a user's environment. They can push garbage to that user's R2 graph or pull their graph. The is_authorized flag doesn't help here — the key is valid.
The fix is key rotation — preambulate auth rotate generates a new key, invalidates the old one, re-syncs. Simple operation, needs to exist.
Priority order:

Document the current risk honestly in the README — "local database is unencrypted, treat it like source code"
Add key rotation — cheap, important
Encrypt R2 payloads with user-held key — medium effort, strong guarantee
Kuzu at-rest encryption — when Kuzu supports it cleanly
