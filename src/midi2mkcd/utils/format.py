import json


def format_ts_str_array(elements: list[str]) -> str:
    # json.dumps escapes all special chars and adds surrounding double quotes.
    # ensure_ascii=False ensures native UTF-8 chars (e.g. Japanese, emojis, accents)
    # stay cleanly formatted as UTF-8 rather than \uXXXX escapes.
    ts_elements = [json.dumps(s, ensure_ascii=False) for s in elements]

    # Format into a TypeScript array
    return f"[{', '.join(ts_elements)}]"
