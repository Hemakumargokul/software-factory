"""Graph stages. Each node is `async def node(state) -> dict` returning a
partial state update; routing lives in graph.py, side effects on the product
live behind git_ops and subprocess calls with hard timeouts."""
