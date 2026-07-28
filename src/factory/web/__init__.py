"""Web UI backend: a FastAPI layer over the same machinery the CLI uses.

The server owns run-driving (each active run is an asyncio task in this
process), which also centralizes environment configuration — Langfuse keys
and acceptance-suite location are set once at server start instead of
per-terminal, so every run is traced by default.
"""
