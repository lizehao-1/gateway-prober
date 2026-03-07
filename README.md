# Gateway Prober

Probe an OpenAI-compatible gateway with a base URL and API key.

It supports:

- A local Web UI for non-technical users
- A CLI for scripting and automation
- Capability checks for models, chat, tool calling, responses, embeddings, images, and docs endpoints

## Quick Start

1. Install dependencies:

```powershell
pip install -r requirements.txt
```

2. Start the Web UI:

```powershell
python .\src\web_app.py
```

Then open [http://127.0.0.1:5050](http://127.0.0.1:5050).

Windows shortcuts:

```powershell
.\start.bat
```

or

```powershell
.\start.ps1
```

## CLI Usage

Text report:

```powershell
python .\src\probe_gateway.py --base-url "https://example.com" --api-key "sk-xxx"
```

JSON output:

```powershell
python .\src\probe_gateway.py --base-url "https://example.com" --api-key "sk-xxx" --format json
```

## What It Tests

- `GET /v1/models`
- `POST /v1/chat/completions`
- Tool calling via `chat/completions`
- `POST /v1/responses`
- `POST /v1/embeddings`
- `POST /v1/images/generations`
- Basic metadata on `/docs`, `/openapi.json`, `/health`, `/version`

## Notes

- The UI does not persist your API key.
- Different gateways expose different subsets of the OpenAI-compatible API.
- A gateway may support text generation but still fail on embeddings or images. This tool is meant to surface that quickly.
