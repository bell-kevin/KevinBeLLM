# Security policy

This repository is designed to be public; the running assistant is not.

Never commit or publish:

- `.env` or `infra/search/.env`;
- Cloudflare Tunnel tokens or origin certificates;
- KevinBeLLM's account database or data volume;
- Ollama model storage, SSH keys, browser profiles, or personal files.

KevinBeLLM and every diagnostic port bind to `127.0.0.1`. Remote access must go
through a named Cloudflare Tunnel protected by Cloudflare Access, followed by
the KevinBeLLM login. Do not port-forward 3000, 8090, 8888, or 11434 on the
router. Do not point GitHub Pages JavaScript directly at any of those ports.

Treat model output and fetched pages as untrusted. The included internet tools
are read-only and have no shell, filesystem-write, email, credential, or model
installation capability. The custom UI has no plug-in loader, shell tool, code interpreter, or remote model
installer. Review any future extension before adding it because server-side tools
can cross that security boundary.

If a secret is accidentally committed, rotate it immediately; removing it from
the latest commit is not enough because git history retains it.
