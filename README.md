
# 🚀 MedusaXD TTS Bot with Redis Integration

This is a complete Telegram Text-to-Speech (TTS) bot built with Python, `python-telegram-bot`, and ElevenLabs, featuring robust Redis integration for user data persistence, rate limiting, session management, and caching.

## 📁 Project Structure

```
MedusaXD-TTS-Bot/
├── src/
│   ├── __init__.py
│   ├── bot.py
│   ├── config.py
│   ├── redis_client.py
│   └── utils.py
├── Dockerfile
├── docker-compose.yml
├── render.yaml
├── requirements.txt
├── .env.example
├── .dockerignore
├── .gitignore
└── README.md
```

## ✨ Features

- **Text-to-Speech**: Converts text messages into high-quality speech using ElevenLabs.
- **Redis Integration**: 
  - **User Settings Persistence**: Saves user-specific voice preferences and other settings.
  - **Rate Limiting**: Prevents abuse and ensures fair usage across all instances.
  - **Voice Caching**: Caches available ElevenLabs voices to reduce API calls and improve response times.
  - **Usage Analytics**: Tracks bot usage and statistics.
- **Docker Support**: Easy deployment with `Dockerfile` and `docker-compose.yml`.
- **Render Deployment**: Optimized configuration for seamless deployment on Render.
- **Graceful Fallback**: Operates with in-memory storage if Redis is unavailable.

## 🔧 Configuration

### `src/config.py`

This file handles all application configurations, including API keys, bot settings, and Redis parameters. It loads sensitive information from environment variables.

```python
# ... (content as provided in the prompt)
```

## 🔴 Redis Client Implementation

### `src/redis_client.py`

An asynchronous Redis client that manages all interactions with the Redis server, including user settings, rate limiting, voice caching, and analytics.

```python
# ... (content as provided in the prompt)
```

## 🤖 Bot Implementation

### `src/bot.py`

The core of the bot, handling all Telegram updates, commands, and message processing. It integrates with `redis_client.py` for data management and `utils.py` for TTS generation.

```python
# ... (content as provided in the prompt)
```

## 🛠️ Utilities

### `src/utils.py`

Provides the `TTSGenerator` class for interacting with the ElevenLabs API, including audio generation and voice fetching with error handling and retries.

```python
# ... (content as provided in the prompt)
```

## 🐳 Docker Configuration

### `Dockerfile`

Defines the Docker image for the bot, including dependencies and application setup.

```dockerfile
# ... (content as provided in the prompt)
```

### `docker-compose.yml`

Orchestrates the bot and Redis services for local development and testing.

```yaml
# ... (content as provided in the prompt)
```

## ☁️ Render Configuration

### `render.yaml`

Specifies the deployment configuration for Render, defining the worker and Redis services.

```yaml
# ... (content as provided in the prompt)
```

## 📦 Dependencies

### `requirements.txt`

Lists all Python dependencies required for the project.

```txt
# ... (content as provided in the prompt)
```

## ⚙️ Environment Configuration

### `.env.example`

An example file for setting up environment variables. Copy this to `.env` and fill in your actual API keys and desired configurations.

```env
# ... (content as provided in the prompt)
```

## 🚀 Deployment Instructions

### 1. Local Development

To run the bot locally using Docker Compose:

```bash
# Clone the repository
git clone <repo-url> # Replace with your repository URL
cd MedusaXD-TTS-Bot

# Create .env file from example and fill in your API keys
cp .env.example .env
# Open .env in a text editor and add your TELEGRAM_BOT_TOKEN and ELEVENLABS_API_KEY

# Start the services (bot and Redis)
docker-compose up -d

# View bot logs
docker-compose logs -f telegram-bot
```

### 2. Deploy to Render

1. **Push to GitHub**: Ensure your project is pushed to a GitHub repository.
2. **Connect to Render**: Log in to your Render dashboard and connect your GitHub repository.
3. **Auto-detection**: Render will automatically detect the `render.yaml` file.
4. **Environment Variables**: Add the following environment variables in your Render dashboard for the `telegram-tts-bot` service:
   - `TELEGRAM_BOT_TOKEN`
   - `ELEVENLABS_API_KEY`
5. **Deploy**: Initiate the deployment. Render will build and deploy your bot and Redis instance.

This comprehensive setup ensures a robust, scalable, and production-ready Telegram TTS bot experience.

