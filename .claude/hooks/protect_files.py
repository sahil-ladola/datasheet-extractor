import json, sys

data = json.load(sys.stdin)
path = data.get("tool_input", {}).get("file_path", "").replace("\\", "/")
for pattern in (".env", ".db", "schemas/"):
    if pattern in path:
        print(f"Blocked: {path} is protected ({pattern}). Ask Sahil first.", file=sys.stderr)
        sys.exit(2)  # exit code 2 blocks the tool call and shows Claude this message
sys.exit(0)
