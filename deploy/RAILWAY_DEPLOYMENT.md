# 🚀 Railway.app Deployment Guide - 30 Seconds to Live!

## Why Railway.app?
- ✅ **Fastest deployment** - 30 seconds
- ✅ **Easy GitHub integration** - one click
- ✅ **Auto-deploys on push** - update instantly
- ✅ **Great Asia CDN** - fast from China/Korea/Japan
- ✅ **Built-in env vars** - easy API key setup
- ⚠️ $5 free credit (then paid, but very cheap)

---

## ⚡ Quick Deploy (30 Seconds)

### Step 1: Sign Up (10 seconds)
```
https://railway.app/new
```
- Click **"Login with GitHub"**
- Authorize Railway to access your repos
- That's it!

### Step 2: Deploy from GitHub (15 seconds)
1. Click **"New Project"** → **"Deploy from GitHub repo"**
2. Select: **`shane-kshongmo/WhaleAlert`**
3. Click **"Deploy Now"**

**That's it!** Railway will:
- Detect Python automatically
- Install dependencies
- Start your service
- Give you a URL

### Step 3: Add Environment Variables (5 seconds)
1. Click on your **whale-alert** project
2. Click **"Variables"** tab
3. Add these:
   ```
   PYTHONUNBUFFERED = 1
   SCAN_INTERVAL_MINUTES = 15
   BINANCE_API_KEY = your_key_here
   BINANCE_API_SECRET = your_secret_here
   TELEGRAM_BOT_TOKEN = your_token_here
   TELEGRAM_CHAT_ID = your_chat_id_here
   ```
4. Click **"Add Variables"**
5. Service auto-restarts

### Step 4: Access Your Service!
Your dashboard will be at:
```
https://whale-alert-production.up.railway.app
```

**Health check:**
```
https://whale-alert-production.up.railway.app/api/health
```

---

## 🔄 How to Update

After deployment, updating is automatic:

```bash
# Make changes locally
vim config.py

# Commit and push
git add config.py
git commit -m "Optimize trading params"
git push origin main

# Railway auto-detects and redeploys in ~30 seconds!
```

---

## 📊 Monitoring

### View Logs
1. Click your project in Railway dashboard
2. Click **"Logs"** tab
3. See real-time logs

### View Metrics
1. Click **"Metrics"** tab
2. See CPU, RAM, network usage

### Settings
1. Click **"Settings"** tab
2. Change region, scale, etc.

---

## 💰 Pricing

**Free tier:**
- $5 credit one-time
- Good for testing and initial deployment

**After free credit:**
- ~$5-10/month for continuous operation
- Very affordable for 24/7 service

**Money-saving tip:**
- Use Railway for testing (fast deploy)
- Use Oracle Tokyo for production (truly free)

---

## 🌏 Asia Performance

Railway has good Asia coverage:
- **From Shanghai:** ~50-100ms
- **From Seoul:** ~40-80ms
- **From Tokyo:** ~30-50ms
- **From Singapore:** ~10-20ms

---

## ⚙️ Advanced Configuration

### Change Region (to be closer to Asia)
1. Project Settings → **"General"**
2. Change **"Region"** to:
   - **Singapore** (best for Southeast Asia)
   - **Tokyo** (best for East Asia)
3. Click **"Save"**
4. Railway redeploys automatically

### Auto-Deploys on Push
By default, Railway auto-deploys when you push to GitHub.

To disable:
1. Project Settings → **"Git"**
2. Toggle **"Auto Deploy on Push"**

---

## 🐛 Troubleshooting

### Service not starting?
```bash
# Check logs in Railway dashboard
# Common issues:
# 1. Missing env vars → Add in Variables tab
# 2. Port conflict → Railway auto-detects port 8888
# 3. Dependencies missing → Check requirements.txt
```

### Out of memory?
```bash
# In Railway dashboard:
# Settings → Change plan
# Or: Add more RAM (paid)
```

### Can't access from China?
```bash
# Railway domains might be slow
# Consider using Oracle Tokyo for production
```

---

## 🆚 Railway vs Oracle Tokyo

| Feature | Railway | Oracle Tokyo |
|---------|---------|--------------|
| **Setup Time** | 30 sec ⚡ | 10 min |
| **Cost** | $5-10/mo | FREE |
| **Auto-Deploy** | ✅ Yes | ❌ Manual |
| **Asia Speed** | Good ⭐⭐⭐⭐ | Excellent ⭐⭐⭐⭐⭐ |
| **24/7** | ✅ Yes | ✅ Yes |
| **Best For** | Testing/Dev | Production |

---

## 💡 Pro Tips

### 1. Use Both for Best Results
```
Railway.app     → Fast testing, quick iterations
Oracle Tokyo    → Production 24/7, truly free
```

### 2. Monitor First 24 Hours
- Check logs regularly
- Monitor RAM usage (should be ~250-300 MB)
- Verify alerts are working

### 3. Set Up Alerts
- Railway can send alerts on failures
- Configure in Settings → Notifications

### 4. Backup Database
- Railway has ephemeral storage
- Download .db file regularly:
  ```bash
  # In Railway dashboard
  # Click "Storage" → Download
  ```

---

## 📱 Mobile Access

You can manage Railway from your phone:
1. Open browser: https://railway.app
2. Login with GitHub
3. View logs, metrics, settings
4. All features available!

---

## ✅ Summary

**What you get with Railway:**
- ✅ Deployed in 30 seconds
- ✅ Auto-updates on git push
- ✅ Easy env var management
- ✅ Built-in monitoring
- ✅ Good Asia CDN
- ⚠️ $5-10/month after free credit

**Perfect for:**
- Quick deployment
- Testing new features
- Fast iterations
- Not production-critical workloads

---

## 🎯 Next Steps

1. ✅ Sign up: https://railway.app/new
2. ✅ Deploy from GitHub
3. ✅ Add environment variables
4. ✅ Test your service
5. ✅ Monitor for 24 hours
6. ✅ If happy, consider Oracle Tokyo for free production

---

**Ready?** Let's go! 🚀

```
1. Open: https://railway.app/new
2. Login with GitHub
3. Click "New Project" → "Deploy from GitHub repo"
4. Select: shane-kshongmo/WhaleAlert
5. Deploy!
```

**30 seconds later**, your service is live! 🎉
