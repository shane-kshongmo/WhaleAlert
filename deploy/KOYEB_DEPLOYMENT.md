# 🚀 Koyeb Deployment Guide

## Why Koyeb?
- ✅ **True 24/7 free tier** - no spin-down
- ✅ **512 MB RAM** - enough for your app
- ✅ **5 GB storage** - for logs and database
- ✅ **Global edge deployment** - low latency
- ✅ **Auto-deploys from Git** - push to deploy
- ✅ **No credit card required**

---

## 📋 Prerequisites

1. **Koyeb Account** (Free):
   ```
   https://app.koyeb.com/register
   ```

2. **GitHub Repository**:
   - Push your code to GitHub (if not already)
   - Koyeb will deploy from your repo

3. **API Keys Ready**:
   - Binance API Key
   - Telegram Bot Token
   - Telegram Chat ID
   - (Optional) Webhook URL

---

## 🚀 Deployment Steps

### Option 1: Deploy via CLI (Recommended)

#### 1. Install Koyeb CLI
```bash
# Download CLI
curl -L https://github.com/koyeb/koyeb-cli/releases/latest/download/koyeb-linux-amd64 -o koyeb

# Make executable
chmod +x koyeb

# Move to path
sudo mv koyeb /usr/local/bin/

# Verify installation
koyeb version
```

#### 2. Login to Koyeb
```bash
koyeb login
```
This will open your browser for authentication.

#### 3. Create koyeb.yaml
```bash
# Already created in your repo root
cat koyeb.yaml
```

#### 4. Deploy!
```bash
cd /mnt/d/finance/whale-alert-service
koyeb init
koyeb deploy
```

That's it! Your app will be live in ~2 minutes.

---

### Option 2: Deploy via Web UI

#### 1. Go to Koyeb Dashboard
```
https://app.koyeb.com
```

#### 2. Create App
- Click "Create App"
- Select "GitHub" as source
- Choose your repo: `shane-kshongmo/WhaleAlert`
- Branch: `main`

#### 3. Configure
```
Name: whale-alert-service
Region: Washington DC (was) or Paris (par)
Instance Type: Nano (Free)
Build Command: pip install -r requirements.txt
Start Command: python main.py
Port: 8888
```

#### 4. Add Environment Variables
```
PYTHONUNBUFFERED=1
SCAN_INTERVAL_MINUTES=15

# Add your API keys:
BINANCE_API_KEY=your_key_here
BINANCE_API_SECRET=your_secret_here
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

#### 5. Deploy
Click "Deploy" and wait ~2 minutes!

---

## 🔧 Configure Environment Variables

After deployment, add your API keys:

### Via CLI:
```bash
koyeb service update whale-alert \
  --env BINANCE_API_KEY="your_key" \
  --env BINANCE_API_SECRET="your_secret" \
  --env TELEGRAM_BOT_TOKEN="your_token" \
  --env TELEGRAM_CHAT_ID="your_chat_id"
```

### Via Web UI:
1. Go to your service in Koyeb dashboard
2. Click "Environment Variables"
3. Add each variable
4. Click "Update Service"
5. Service will auto-restart

---

## 📊 Monitor Your Deployment

### View Logs
```bash
# CLI
koyeb logs follow whale-alert

# Or in web UI
# Click your service → Logs tab
```

### Check Status
```bash
koyeb services get whale-alert
```

### View Metrics
- CPU usage
- Memory usage
- Network traffic
- Request count

All available in Koyeb dashboard!

---

## 🌐 Access Your Service

After deployment, Koyeb will give you:
```
https://whale-alert-service-xxx.koyeb.app
```

Access dashboard at:
```
https://whale-alert-service-xxx.koyeb.app
```

Health check:
```
https://whale-alert-service-xxx.koyeb.app/api/health
```

---

## 🔄 Continuous Deployment

Koyeb auto-deploys when you push to GitHub:

```bash
# Make changes locally
git add .
git commit -m "Update trading parameters"
git push origin main

# Koyeb automatically detects and redeploys!
# Takes ~2-3 minutes to rebuild and restart
```

---

## 💾 Database Persistence

Your SQLite database and logs are stored in the volume:
```
/opt/whale-alert/
├── whale_alert.db
├── logs/
└── models/
```

This persists across deployments!

---

## 📈 Scaling

Your service runs on **nano** instance (free tier):
- 512 MB RAM
- 0.2 vCPU
- 5 GB storage
- **24/7 uptime**

To scale (paid):
```bash
koyeb service update whale-alert --instance-type 2xlarge
```

---

## 🐛 Troubleshooting

### Service not starting?
```bash
# Check logs
koyeb logs whale-alert

# Common issues:
# 1. Missing .env file → Add env vars in Koyeb dashboard
# 2. Dependencies missing → Check requirements.txt
# 3. Port conflict → Make sure port 8888 is exposed
```

### Out of memory?
```bash
# Check RAM usage in Koyeb dashboard
# Your app uses ~200-300 MB RAM, so 512 MB should be fine

# If needed:
koyeb service update whale-alert --instance-type micro  # 1 GB RAM
```

### Database locked?
```bash
# This can happen if multiple instances start
# Ensure min_instances = 1 and max_instances = 1
koyeb service update whale-alert --min-instances 1 --max-instances 1
```

---

## 🔒 Security Best Practices

1. **Never commit .env file**
   ```bash
   # .gitignore already includes .env
   echo ".env" >> .gitignore
   ```

2. **Use Koyeb secrets for sensitive data**
   ```bash
   koyeb secret create binance_key --value "your_api_key"
   koyeb service update whale-alert --env BINANCE_API_KEY="@binance_key"
   ```

3. **Enable HTTPS**
   - Koyeb provides automatic SSL certificates
   - Your app is accessible via HTTPS only

4. **Access logs regularly**
   ```bash
   koyeb logs whale-alert --since 1h
   ```

---

## 📊 Resource Limits (Free Tier)

| Resource | Limit |
|----------|-------|
| RAM | 512 MB |
| vCPU | 0.2 core |
| Storage | 5 GB |
| Bandwidth | Fair use policy |
| Instances | 1 service |
| Regions | 2 (was + par) |

**Your app usage:**
- RAM: ~250-300 MB ✅
- Storage: ~50-100 MB ✅
- CPU: ~10-20% average ✅

---

## 🆚 Koyeb vs Oracle Cloud

| Feature | Koyeb | Oracle Cloud |
|---------|-------|--------------|
| **Setup difficulty** | Very Easy | Medium |
| **Deployment time** | 2 min | 10 min |
| **Auto-deploy** | ✅ Yes | ❌ Manual |
| **RAM** | 512 MB | 1 GB |
| **Storage** | 5 GB | 200 GB |
| **24/7 uptime** | ✅ Yes | ✅ Yes |
| **SSL/HTTPS** | ✅ Auto | Manual setup |
| **Monitoring** | ✅ Built-in | Manual setup |
| **Best for** | Quick deploy | Max resources |

---

## 🎯 Next Steps

1. ✅ Sign up for Koyeb (free)
2. ✅ Install CLI or use web UI
3. ✅ Deploy your service
4. ✅ Add environment variables (API keys)
5. ✅ Monitor first 24 hours
6. ✅ Check logs and performance

---

## 💰 Cost After Free Tier

If you outgrow free tier (unlikely for this app):
- **Nano**: Free (your current tier)
- **Micro**: ~$5-10/month (1 GB RAM)
- **Standard**: ~$20-50/month (2 GB RAM)

**Recommendation:** Start with free tier, upgrade only if needed.

---

## 🆘 Need Help?

- Koyeb Docs: https://www.koyeb.com/docs
- Koyeb CLI: `koyeb --help`
- Community: https://discord.gg/koyeb

---

## ✅ Summary

With Koyeb, you get:
- ✅ **24/7 free hosting**
- ✅ **2-minute deployment**
- ✅ **Auto-deploys from Git**
- ✅ **Built-in monitoring**
- ✅ **SSL/HTTPS included**
- ✅ **No credit card needed**

Your whale alert service will run continuously, collecting data and paper trading - all for free! 🚀
