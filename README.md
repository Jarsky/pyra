# 🔥 Pyra — Modern Python IRC Bot

<p align="center">
  <b>A powerful, extensible, and production-ready IRC bot built with modern Python.</b>
</p>

![CI](https://img.shields.io/github/actions/workflow/status/Jarsky/pyra/ci.yml?branch=main)
![License](https://img.shields.io/github/license/Jarsky/pyra)
![Issues](https://img.shields.io/github/issues/Jarsky/pyra)
![Stars](https://img.shields.io/github/stars/Jarsky/pyra)

---

## ✨ Features
- Async IRC (IRCv3, SASL, TLS)
- Plugin system with hot reload
- Moderation + utilities
- FastAPI web UI
- Partyline admin console
- Docker + native deploy

---

## 🚀 Quick Start

### Docker
```bash
git clone https://github.com/Jarsky/pyra.git
cd pyra/docker
docker-compose up -d
```

### Native
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pybot-setup
pybot
```

---

## 🔌 Plugin Example

```python
from pybot import plugin

@plugin.command("hello")
async def hello(bot, trigger):
    await bot.reply(trigger, f"Hello {trigger.nick}")
```

---

## 📜 License
MIT
