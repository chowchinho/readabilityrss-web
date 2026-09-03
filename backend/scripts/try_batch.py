import os, sys, requests, json
from validate_topic_tagging import sample_articles, SYSTEM_PROMPT_TAGGING, TAGGING_MODEL

# Force UTF-8 output on Windows console
sys.stdout.reconfigure(encoding='utf-8')

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

key = os.getenv("DEEPSEEK_API_KEY")
db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "feeds.db")

sampled = sample_articles(db_path, seed=42, articles_per_feed=5)
batch = sampled[:20]

payload_articles = []
for it in batch:
    payload_articles.append({
        "id": it["id"],
        "title": it["title"],
        "body": it["body_snippet"]
    })

batch_prompt = f"Articles to tag:\n{json.dumps({'articles': payload_articles}, ensure_ascii=False, indent=2)}"

headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
data = {
    "model": TAGGING_MODEL,
    "messages": [
        {"role": "system", "content": SYSTEM_PROMPT_TAGGING},
        {"role": "user", "content": batch_prompt}
    ],
    "response_format": {"type": "json_object"},
    "temperature": 0.2,
    "max_tokens": 8192
}

print(f"Sending batch of {len(batch)} items to DeepSeek...")
r = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=data, timeout=90)
print("Status code:", r.status_code)
j = r.json()
choice = j["choices"][0]
finish_reason = choice.get("finish_reason")
content = choice["message"].get("content", "")
usage = j.get("usage", {})

print(f"Finish reason: {finish_reason}")
print(f"Usage: {usage}")
print(f"Content length: {len(content)}")

try:
    parsed = json.loads(content)
    art_list = parsed.get("articles", [])
    print(f"Successfully parsed JSON! Got {len(art_list)} articles.")
    if art_list:
        print("Sample article output:", json.dumps(art_list[0], ensure_ascii=False))
except Exception as e:
    print(f"Failed to parse JSON content: {e}")
    print("Content snippet:", content[:300])
