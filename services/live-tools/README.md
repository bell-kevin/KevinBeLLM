# Live data tools

This small FastAPI service exposes two read-only tools to KevinBeLLM:

- current weather and a 1–7 day forecast from Open-Meteo;
- live Hugging Face text-generation model discovery.

It deliberately has no shell, filesystem, model-download, email, or account
tools. Its machine-readable schema remains available at `/openapi.json`.
