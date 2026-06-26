# LearnX-Delftse

An interactive trainer for the **Delftse methode** chapters in [book1/](book1/). It was
modelled on LearnX-Radar's Dutch trainer, but is now **self-contained** — the pipeline it
needs is vendored into [delftse/](delftse/), so it runs with no dependency on the Radar
checkout. It only optionally *shares the same Telegram bot*.

## Layout

- `book1/*.md` — each chapter: a `Dutch | English` table (the lesson text) + a `---`
  Q&A "spreken" section.
- [delftse/](delftse/) — the vendored pipeline: `llm` (NVIDIA/Groq), `audio` (edge-tts
  MP3 + ms-timestamped A/B/C blocks), `cloze`, `trainer` (payload), `lesson`, `models`.
- [config.py](config.py) — reads `.env` (see [.env.example](.env.example)); no secrets in git.
- [scripts/](scripts/) — `convert.py` (one chapter), `batch.py` (all/selected), `qc.py`
  (read-only quality check).
- [trainer/delftse.html](trainer/delftse.html) — a static, client-side page (no backend).
  Tabs: **🎧 leren** (5 Delft input steps), **✍️ test** (gatentekst + luistertoets),
  **💬 vragen** (spreken Q&A, browser TTS), **🏆 lessen** (per-chapter scores), and
  **🔁 herhaling** (cross-chapter review, shown with `?u=<token>`). A header chapter
  picker opens any chapter. Per-device progress is in localStorage; the Telegram loop
  syncs spaced-repetition across devices.

## Setup

```sh
pip install -r requirements.txt        # also needs ffmpeg on PATH
cp .env.example .env                    # then fill in the keys you need
```

`.env` keys: LLM (`NVIDIA_API_KEY`, optional `GROQ_API_KEY`) are only needed to
(re)generate chapters; Telegram (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`REVIEW_TOKEN_SECRET`) are for the personalization sync.

## (Re)generate chapters

```sh
python scripts/convert.py "book1/1_Hoe_heet_je.md"   # one chapter
python scripts/batch.py                               # all 42
python scripts/qc.py                                  # quality check
```

## Run the trainer

```sh
cd trainer && python -m http.server 8731
# open http://localhost:8731/delftse.html   (or ?h=<chapter>, ?u=<token>)
```