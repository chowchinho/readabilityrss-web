import os, requests, json

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

key = os.getenv("DEEPSEEK_API_KEY")
print("API key loaded:", bool(key))

for model in ["deepseek-v4-flash", "deepseek-chat"]:
    print(f"\n--- Testing model: {model} ---")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    data = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You are a JSON generator. Always return JSON format."},
            {"role": "user", "content": "Return a json object: {\"status\": \"ok\", \"articles\": [{\"id\": 1, \"title\": \"test\"}]}"}
        ]
    }
    r = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=data, timeout=30)
    print("Status:", r.status_code)
    print("Response text:", r.text[:300])
