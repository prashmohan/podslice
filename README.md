# Podslice 🎙️✂️

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Django](https://img.shields.io/badge/django-5.2-green.svg)](https://www.djangoproject.com/)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://www.docker.com/)

**Podslice** is a self-hosted application designed to transform standard podcast feeds into clean, ad-free RSS feeds. It leverages **Google Gemini AI** to detect sponsor segments, host-read native ads, and promotional intros/outros, and uses **FFmpeg** to seamlessly slice them out.

Subscribe to your customized ad-free feeds from any standard podcast player (Apple Podcasts, Pocket Casts, Overcast, AntennaPod, etc.) and enjoy uninterrupted listening.

---

## 🌟 Key Features

* **Intelligent AI Ad Detection**: Uses Google Gemini to detect pre-roll, mid-roll, native host-read sponsorships, repetitive intros/outros, and promotional CTAs.
* **Automatic Model Fallback**: Configured to seamlessly fall back to secondary models (e.g. from Flash to Flash-Lite) to handle rate limits and transient errors.
* **On-Demand Dynamic Processing**: Downloads and slices audio only when a client or podcast app requests the episode, saving storage and API quota.
* **iTunes / Apple Podcasts Compliant Feeds**: Generated feeds include full metadata, episode durations, cover art, descriptions, and standard podcast tags.
* **OPML Import, Export & Backup**: Effortlessly migrate subscriptions to/from any podcast client with full OPML support.
* **Granular Retention Limits**: Configure retention policies per podcast (e.g., retain the last $N$ episodes or store unlimited episodes).
* **Web UI & REST API**: Intuitive web dashboard to monitor episode statuses, toggle AI processing per episode, trigger reprocessing, or interact via REST APIs.
* **Production Ready**: Bundled with Docker, Docker Compose, Gunicorn, and an Nginx reverse-proxy configuration.

---

## 🔄 How It Works

```
                                 +--------------------------------+
                                 | Original Podcast RSS Feed (Web)|
                                 +--------------------------------+
                                                 |
                                                 v
  +-------------------+       HTTP GET       +--------------------+
  |   Podcast App     | <==================  |   Podslice Server  |
  | (Apple Podcasts,  |                      |  (Django + Nginx)  |
  |  Pocket Casts)    | --- Request Audio -> |                    |
  +-------------------+                      +--------------------+
                                                       |
                                           +-----------+-----------+
                                           |                       |
                                    1. Download Audio       2. AI Ad Analysis
                                           |                       |
                                           v                       v
                                    [ Original MP3 ]      [ Google Gemini API ]
                                           \                       /
                                            \  3. Slicing (FFmpeg)/
                                             v                   v
                                         +--------------------------+
                                         |   Clean Ad-Free Audio    |
                                         +--------------------------+
                                                     |
                                                     v
                                         Streamed to Podcast App 🎧
```

1. **Ingest**: You subscribe to a podcast feed via the web UI or OPML import. Podslice ingests the feed and generates a unique rehosted RSS URL.
2. **On-Demand Processing**: When your podcast player requests an episode, Podslice downloads the audio file and sends it to the Gemini API.
3. **Smart Slicing**: FFmpeg cuts out the identified timestamps and concatenates the clean audio segments.
4. **Rehosting & Caching**: The sliced audio is cached and served to your podcast client. Subsequent requests for the same episode are served instantly.

---

## 🚀 Getting Started

### Prerequisites

* **Google Gemini API Key**: Obtain a free API key from [Google AI Studio](https://aistudio.google.com/).
* **FFmpeg**: Required for audio slicing (pre-installed in Docker images).

---

### Option 1: Quick Start with Docker (Recommended)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/prashmohan/podslice.git
   cd podslice
   ```

2. **Configure environment variables:**
   ```bash
   cp .env.example .env
   ```
   Open `.env` and configure your settings (at minimum, set `GEMINI_API_KEYS` or `GEMINI_API_KEY`, and `DJANGO_SECRET_KEY`):
   ```ini
   DJANGO_SECRET_KEY=your_random_secret_key_here
   GEMINI_API_KEY=your_gemini_api_key_here
   # Or for multi-key support:
   # GEMINI_API_KEYS=key1,key2,key3
   REHOST_BASE_URL=http://localhost:12343
   ```

3. **Start the services:**
   ```bash
   docker compose up -d --build
   ```

4. **Access the application:**
   * **Web Dashboard**: [http://localhost:12343](http://localhost:12343)
   * **Dedicated Media/Feed Port**: `http://localhost:12341`

---

### Option 2: Local Development (Without Docker)

1. **Clone and setup a virtual environment:**
   ```bash
   git clone https://github.com/prashmohan/podslice.git
   cd podslice
   python3 -m venv venv
   source venv/bin/activate
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
   *Ensure `ffmpeg` is installed on your operating system (`sudo apt install ffmpeg` on Ubuntu/Debian, `brew install ffmpeg` on macOS).*

3. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env and set your GEMINI_API_KEY and DJANGO_SECRET_KEY
   ```

4. **Apply database migrations:**
   ```bash
   python manage.py migrate
   ```

5. **Start the development server:**
   ```bash
   python manage.py runserver
   ```
   Visit `http://localhost:8000` in your browser.

---

## ⚙️ Configuration & Environment Variables

Create a `.env` file in the project root. The available configuration options are:

| Variable | Description | Default | Required |
| :--- | :--- | :--- | :--- |
| `GEMINI_API_KEYS` | Comma-separated Google AI Studio API keys for quota distribution and rotation. | — | **Yes** (or `GEMINI_API_KEY`) |
| `GEMINI_API_KEY` | Single Google AI Studio API key (legacy fallback). | — | **Yes** (if `GEMINI_API_KEYS` unset) |
| `DJANGO_SECRET_KEY` | Django cryptographic secret key. | Generated if unset | **Recommended** |
| `REHOST_BASE_URL` | Base public URL where feeds and media are served. | `http://localhost:12343` | **Yes** |
| `GEMINI_MODEL` | Primary Gemini model for ad detection. | `gemini-3.8-flash` | No |
| `FALLBACK_GEMINI_MODEL` | Secondary fallback model if primary fails across keys. | `gemini-3.5-flash-lite` | No |
| `GEMINI_MAX_RETRY_DELAY_SEC` | Max sleep delay in seconds on 429 quota limits before primary retry. | `30` | No |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated list of allowed host header values. | `localhost,127.0.0.1` | No |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated list of trusted origins for CSRF. | `http://localhost:12343` | No |
| `MAX_EPISODES_PER_PODCAST` | Default retention limit for new subscriptions (0 = all). | `10` | No |
| `PODCAST_POLLING_INTERVAL` | Polling frequency for new episodes (in seconds). | `43200` (12h) | No |
| `SECURE_SSL_REDIRECT` | Redirect all HTTP traffic to HTTPS in production. | `False` | No |
| `LOG_LEVEL` | Application logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). | `INFO` | No |
| `TIME_ZONE` | Time zone string for timestamp rendering. | `UTC` | No |

---

## 📱 Usage Guide

### Subscribing to a Podcast
1. Open the Podslice web dashboard.
2. Paste the RSS feed URL of any podcast.
3. Click **Subscribe**. Podslice will fetch the podcast metadata and generate a custom feed link.
4. Copy the generated **Rehosted RSS Feed** URL and add it to your podcast app (e.g., Pocket Casts *Add by URL*, Apple Podcasts *Follow a Show by URL*).

### OPML Import & Export
* **Export OPML**: Download an OPML file containing your re-hosted (ad-free) feed links to import into any podcast app in bulk.
* **Backup OPML**: Download an OPML file with the original podcast URLs for archiving.
* **Import OPML**: Upload an existing OPML file to batch-subscribe to multiple podcasts at once.

### Per-Episode Controls
* **Skip AI & Rehost**: If a specific episode doesn't contain ads or you prefer original audio, toggle off AI processing from the episode status page.
* **Reprocess Episode**: Clear cached audio and re-run Gemini analysis and FFmpeg slicing.

---

## 🧪 Testing & Code Quality

Run the test suite:
```bash
python manage.py test podcasts
```

Run linting:
```bash
python manage.py lint
```

---

## 📄 License

This project is open source and available under the [MIT License](LICENSE).
