# Running a local translation model

ReadabilityRSS can translate with a model running on your own hardware, as a fallback
under every remote provider and as a per-feed translator in its own right. It cannot be
rate limited, walled or billed, which is the point: the remote free endpoints all fail
eventually, and this one keeps working when they do.

It is **entirely optional**. Leave `LMT_URL` unset and nothing changes — the provider
reports itself unavailable and the remote ones carry on as before.

## What it is

[NiuTrans LMT-60-1.7B](https://huggingface.co/NiuTrans/LMT-60-1.7B) (Apache-2.0), a
1.7B translation model covering 60 languages with Chinese and English at its centre. It
is served through [llama.cpp](https://github.com/ggerganov/llama.cpp) behind an
OpenAI-compatible HTTP endpoint, so the backend talks to it the same way it talks to
any other provider.

Any OpenAI-compatible endpoint works. Point `LMT_URL` at whatever you prefer.

## Setup

Tested on a Raspberry Pi 5 (4× Cortex-A76, 8GB). Roughly 1.8GB of disk.

```bash
mkdir -p ~/lmt && cd ~/lmt
python3 -m venv venv
./venv/bin/pip install "llama-cpp-python[server]"

mkdir -p models && cd models
curl -sL -o LMT-60-1.7B.Q4_K_M.gguf \
  https://huggingface.co/mradermacher/LMT-60-1.7B-GGUF/resolve/main/LMT-60-1.7B.Q4_K_M.gguf
```

Run it as a service — `/etc/systemd/system/lmt-server.service`:

```ini
[Unit]
Description=LMT-60-1.7B translation server
After=network-online.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/lmt
ExecStart=/home/youruser/lmt/venv/bin/python -m llama_cpp.server \
  --model /home/youruser/lmt/models/LMT-60-1.7B.Q4_K_M.gguf \
  --model_alias LMT-60-1.7B \
  --host 127.0.0.1 --port 8099 \
  --n_ctx 2048 --n_threads 3
Restart=on-failure
Nice=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now lmt-server
curl -s http://127.0.0.1:8099/v1/models
```

Then in `backend/.env`:

```
LMT_URL=http://127.0.0.1:8099/v1/chat/completions
```

Bind to `127.0.0.1` unless the backend runs on a different host — in Docker it needs an
address the container can reach, so use the host's LAN address and firewall the port.

`--n_threads 3` on a 4-core box leaves a core for everything else, and `Nice=10` means
translation yields to whatever else is running. Removing it all is
`sudo systemctl disable --now lmt-server && rm -rf ~/lmt`.

## What to expect

Measured on a Pi 5 at Q4_K_M, under normal load:

| | |
|---|---|
| Throughput | ~4 tokens/sec |
| Memory | ~2.1GB resident, 2.5GB peak |
| A title | 2-3 seconds |
| A 22-block article | 2-5 minutes |

That last number drives the design. The scheduler allows 240 seconds per article, so a
feed set to `lmt` translates its **title inline** and hands the **body to a background
worker** that runs every few minutes off the refresh path. An article shows a translated
headline immediately and fills in its body within the hour.

Budget roughly one CPU-hour per 250 articles.

## Quality, honestly

It is a 1.7B model. It finishes what it starts — measured 89% mean block coverage across
91 articles — but it paraphrases, drops clauses on long sentences, and gets proper nouns
and numbers wrong. Observed: 石炭火力発電 ("coal-fired power") rendered as 石油電廠 ("oil
plant"), 6人に1人 ("one in six") as 6成 ("60%"), and a Hilton hotel as a Sheraton.

Good enough to skim a feed you would otherwise not read at all. Not good enough to trust
for detail. For quality, use a remote provider and keep this one as the fallback.

## Language targets

The model takes language **names**, not codes, and the exact string matters:

| Target | Result |
|---|---|
| `Traditional Chinese` | Traditional ✓ |
| `Yue Chinese` | genuine Cantonese ✓ |
| `Chinese` | Simplified |
| `cht` | returns the source untranslated |

`LMT_LANG_MAP` in `backend/app/services/translation.py` holds the mapping. Output still
goes through the Simplified-to-Traditional pass, because the model mixes scripts within
a single block.
