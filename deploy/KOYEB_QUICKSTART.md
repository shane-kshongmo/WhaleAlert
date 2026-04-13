# 🚀 Koyeb Quick Start - 5 Minute Deployment

## Step 1: Create Koyeb Account (1 min)
```
https://app.koyeb.com/register
```
- Sign up free
- No credit card needed

## Step 2: Install CLI (1 min)
```bash
curl -L https://github.com/koyeb/koyeb-cli/releases/latest/download/koyeb-linux-amd64 -o koyeb
chmod +x koyeb
sudo mv koyeb /usr/local/bin/
```

## Step 3: Login (30 sec)
```bash
koyeb login
```

## Step 4: Deploy (2 min)
```bash
cd /mnt/d/finance/whale-alert-service
koyeb init
koyeb deploy
```

## Step 5: Add API Keys (1 min)
Via web UI: https://app.koyeb.com
```
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

## ✅ Done!

Your service is live at:
```
https://whale-alert-service-xxx.koyeb.app
```

---

## 📊 What You Get

- ✅ **24/7 uptime** (no spin-down!)
- ✅ **512 MB RAM** (enough for your app)
- ✅ **5 GB storage** (database + logs)
- ✅ **Auto SSL/HTTPS**
- ✅ **Auto-deploys from GitHub**
- ✅ **Built-in monitoring**

---

## 🔄 Update Code

```bash
git add .
git commit -m "Update config"
git push origin main

# Koyeb auto-redeploys in ~2 minutes
```

---

## 📈 Monitor

```bash
# View logs
koyeb logs follow whale-alert

# Check status
koyeb services get whale-alert

# Open dashboard
open https://app.koyeb.com
```

---

## 🆘 Troubleshooting

**Service not starting?**
```bash
koyeb logs whale-alert --tail 100
```

**Out of memory?**
```bash
# Check in dashboard
# Your app uses ~250 MB, so 512 MB free tier is fine
```

**Need help?**
```bash
koyeb --help
# Or: https://www.koyeb.com/docs
```

---

**Full guide:** `deploy/KOYEB_DEPLOYMENT.md`
