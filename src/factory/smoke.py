"""Temporary manual smoke check for M3: one real reasoner call.

Run: python -m factory.smoke
Verifies subscription auth and the run_role plumbing end to end.
Deleted once the graph has real stages exercising the same path.
"""

import asyncio

from factory.claude import (
    JSON_ROLE_SYSTEM_PROMPT,
    extract_json,
    reasoner_role,
    run_role,
)

PROMPT = (
    "Reply with JSON matching this schema: "
    '{"ok": boolean, "model_note": "one short sentence about what you are"}'
)


async def main() -> None:
    result = await run_role(
        reasoner_role(), PROMPT, system_prompt=JSON_ROLE_SYSTEM_PROMPT
    )
    print(f"session_id : {result.session_id}")
    print(f"num_turns  : {result.num_turns}")
    print(f"cost_usd   : {result.cost_usd}")
    print(f"usage      : {result.usage}")
    print(f"text       : {result.text!r}")
    print(f"parsed     : {extract_json(result.text)}")


if __name__ == "__main__":
    asyncio.run(main())
