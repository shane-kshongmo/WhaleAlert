# 🚀 Asia Region Deployment Guide

## 🇯🇵 Recommended: Oracle Cloud Tokyo

### Why Tokyo is Best for Asia:
- **From Shanghai:** ~10-20ms latency
- **From Seoul:** ~15-25ms latency  
- **From Taipei:** ~10-15ms latency
- **From Singapore:** ~80-100ms latency
- **From Mumbai:** ~150-200ms latency

### Step 1: Create Oracle Cloud Account
```
https://www.oracle.com/cloud/free/
```
- Sign up free (email + phone verification)
- Takes 2 minutes

### Step 2: Create Tokyo VM
1. Go to: **Oracle Cloud Console**
2. Navigate: **Compute → Instances**
3. Click: **Create Instance**
4. Configure:
   ```
   Name: whale-alert-tokyo
   Shape: VM.Standard.E2.1.Micro (FREE)
   Image: Ubuntu 22.04
   Region: Japan East (Tokyo)  ← IMPORTANT!
   SSH Key: Upload your public key
   ```
5. Click: **Create**

### Step 3: Get Your Tokyo IP
After instance creation (2-3 minutes):
- Click on your instance
- Copy **Public IP Address**
- Example: `150.238..xxx.xxx`

### Step 4: Upload Your Code
```bash
# From your WSL terminal:
cd /mnt/d/finance/whale-alert-service

# Upload to Tokyo VM (replace with your IP)
TOKYO_IP="YOUR_TOKYO_IP_HERE"

rsync -avz --exclude 'venv' --exclude '__pycache__' \
  --exclude '*.pyc' --exclude '.git' --exclude 'logs' \
  --exclude '*.db' --exclude 'models' \
  . ubuntu@$TOKYO_IP:~/whale-alert/
```

### Step 5: SSH & Deploy
```bash
# SSH into Tokyo VM
ssh ubuntu@$TOKYO_IP

# Run setup script
cd ~/whale-alert
chmod +x deploy/oracle-tokyo-setup.sh
./deploy/oracle-tokyo-setup.sh
```

### Step 6: Add API Keys
```bash
# On the Tokyo VM
nano .env

# Add your keys:
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

### Step 7: Start Service
```bash
# Service is already started by setup script
# Check status:
sudo systemctl status whale-alert

# View logs:
sudo journalctl -u whale-alert -f
```

### Step 8: Access Dashboard
```
http://YOUR_TOKYO_IP:8888
```

---

## 🇸🇬 Alternative: Oracle Cloud Singapore

**Best for:**
- Southeast Asia (Thailand, Vietnam, Malaysia, Indonesia, Philippines)
- Australia, New Zealand

**Latency:**
- Bangkok: ~30-40ms
- Singapore: ~5ms
- Jakarta: ~25-35ms
- Sydney: ~70-90ms

**Same setup, just select region:**
- **Asia Pacific (Singapore)** instead of Japan East (Tokyo)

---

## ⚡ Alternative: Railway.app (Fastest Deploy)

**If you want the easiest setup (30 seconds):**

### Pros:
- ✅ Fastest deployment (30 seconds)
- ✅ Very fast global CDN in Asia
- ✅ Auto-deploys from GitHub
- ⚠️ $5 free credit (not truly free)
- ⚠️ Spin-down on free tier

### Setup:
1. **Sign up:** https://railway.app
2. **Click:** "New Project" → "Deploy from GitHub repo"
3. **Select:** `shane-kshongmo/WhaleAlert`
4. **Region:** Automatic (picks closest to you)
5. **Deploy!**

---

## 📊 Asia Speed Comparison

| Platform | Region | Latency (Shanghai) | Latency (Seoul) | 24/7 Free |
|----------|--------|-------------------|-----------------|-----------|
| **Oracle Tokyo** | Japan East | ~15ms | ~20ms | ✅ Yes |
| **Oracle Singapore** | AP South | ~180ms | ~250ms | ✅ Yes |
| **Railway** | Auto | ~50ms | ~70ms | ❌ No |
| **Render** | Singapore | ~180ms | ~250ms | ❌ No |
| **Koyeb** | Washington | ~200ms | ~150ms | ✅ Yes |

**Winner: Oracle Tokyo** ⭐

---

## 🚀 Quick Decision Tree

**Need true 24/7 free?**
→ Use **Oracle Tokyo** (10 min setup)

**Want fastest deploy?**
→ Use **Railway.app** (30 seconds)

**Best of both?**
→ Use **Oracle Tokyo** for production + **Railway** for testing

---

## 🔧 Tokyo VM Specs (Free Tier)

```
CPU: 1 OCPU (0.2-1 vCPU)
RAM: 1 GB
Storage: 200 GB
Network: 10 TB/month egress
Region: Tokyo, Japan
Cost: $0 (forever)
```

---

## 📈 Performance Expectations

Your app will use:
- **RAM:** ~250-300 MB (well under 1 GB limit)
- **CPU:** ~10-20% average
- **Storage:** ~50-100 MB (well under 200 GB)
- **Network:** ~1-2 GB/day (well under 10 TB/month)

**Result:** Comfortably fits in free tier! ✅

---

## 🎯 Next Steps

1. **Sign up Oracle Cloud** (2 min)
2. **Create Tokyo VM** (3 min)
3. **Upload code** (1 min)
4. **Run setup script** (2 min)
5. **Add API keys** (1 min)
6. **Start trading!** ✅

**Total time:** ~10 minutes

---

## 💡 Pro Tips

### Use Tokyo if you're in:
- ✅ China, Korea, Japan, Taiwan, Hong Kong
- ✅ Fastest latency (~10-20ms)

### Use Singapore if you're in:
- ✅ Thailand, Vietnam, Malaysia, Indonesia, Philippines
- ✅ Australia, New Zealand
- ✅ India (though still slower)

### Monitor from Asia:
```bash
# Test latency to Tokyo
ping -c 5 150.238.xxx.xxx  # Your Tokyo VM IP

# Should be ~10-30ms from East Asia
```

---

## 🆘 Troubleshooting

**Can't access Oracle from China?**
- Use mobile hotspot for sign-up
- Or use VPN for initial setup
- After setup, direct access works fine

**Tokyo VM full?**
- Oracle gives 2 free VMs
- Use second one in Singapore
- Or upgrade to paid tier (~$5-10/month)

**Slow file upload?**
- Use rsync with compression:
  ```bash
  rsync -avz --compress . ubuntu@$IP:~/whale-alert/
  ```

---

**Ready to deploy to Tokyo?** Just follow the 6 steps above! 🇯🇵
