# 🚀 Deployment Guide - Free 24/7 Hosting

## Option 1: Oracle Cloud Free Tier ⭐ RECOMMENDED

### Why Oracle Cloud?
- ✅ **Permanently free** - no trial expiration
- ✅ **24/7 uptime** - no spin-down
- ✅ **2 VMs included** - 1 GB RAM each
- ✅ **200 GB storage**
- ✅ **No credit card required** for verification

### Quick Start (5 minutes)

1. **Sign Up:**
   ```
   https://www.oracle.com/cloud/free/
   ```

2. **Create VM:**
   - Compute → Instances → Create Instance
   - Shape: `VM.Standard.E2.1.Micro` (FREE)
   - Image: `Ubuntu 22.04`
   - Add your SSH public key

3. **Deploy Code:**
   ```bash
   # From your local machine (WSL)
   cd /mnt/d/finance/whale-alert-service

   # Upload to Oracle Cloud
   rsync -avz --exclude 'venv' --exclude '__pycache__' \
     --exclude '*.pyc' --exclude '.git' --exclude 'logs' \
     --exclude '*.db' --exclude 'models' \
     . ubuntu@YOUR_ORACLE_IP:~/whale-alert/

   # SSH into Oracle VM
   ssh ubuntu@YOUR_ORACLE_IP

   # Run setup script
   chmod +x deploy/oracle-setup.sh
   ./deploy/oracle-setup.sh
   ```

4. **Configure .env:**
   ```bash
   nano .env
   # Add your API keys
   ```

5. **Start Service:**
   ```bash
   sudo systemctl start whale-alert
   sudo systemctl status whale-alert
   ```

### Access Your Dashboard
```
http://YOUR_ORACLE_IP:8888
```

### Monitoring
```bash
# View logs
sudo journalctl -u whale-alert -f

# Check service status
sudo systemctl status whale-alert

# Restart if needed
sudo systemctl restart whale-alert
```

---

## Option 2: Fly.io (Easier, Limited)

### Why Fly.io?
- ✅ Very easy deployment
- ✅ Global edge deployment
- ⚠️ Free tier has spin-down (not truly 24/7)
- ⚠️ 256 MB RAM limit

### Quick Start

1. **Install Fly CLI:**
   ```bash
   curl -L https://fly.io/install.sh | sh
   ```

2. **Login:**
   ```bash
   flyctl auth signup
   flyctl auth login
   ```

3. **Deploy:**
   ```bash
   cd /mnt/d/finance/whale-alert-service
   flyctl launch
   flyctl deploy
   ```

4. **Access:**
   ```bash
   flyctl apps open
   ```

---

## Option 3: GitHub Codespaces (Testing Only)

### Good For:
- ✅ Development and testing
- ✅ Free hours (60 hrs/month)
- ❌ Not 24/7 (stops after inactivity)

### Setup:
1. Create Codespace from your repo
2. Terminal: `python main.py`
3. Access via port forwarding

---

## Comparison Table

| Feature | Oracle Cloud | Fly.io | GitHub Codespaces |
|---------|--------------|--------|-------------------|
| **Cost** | FREE forever | FREE (limited) | 60 hrs/month FREE |
| **24/7 Uptime** | ✅ Yes | ⚠️ Spin-down | ❌ No |
| **RAM** | 1 GB | 256 MB | Varies |
| **Storage** | 200 GB | 3 GB | Temporary |
| **Setup Difficulty** | Medium | Easy | Very Easy |
| **Best For** | Production | Testing | Development |

---

## Post-Deployment Checklist

### Security 🔒
- [ ] Change default SSH port
- [ ] Setup firewall (UFW)
- [ ] Use strong passwords
- [ ] Never commit .env file
- [ ] Use environment variables for secrets

### Monitoring 📊
- [ ] Check logs daily for first week
- [ ] Monitor disk space: `df -h`
- [ ] Monitor CPU: `htop`
- [ ] Monitor RAM: `free -h`

### Maintenance 🔧
- [ ] Auto-restart via systemd (Oracle)
- [ ] Update code: `git pull && systemctl restart whale-alert`
- [ ] Backup database weekly
- [ ] Rotate API keys monthly

---

## Troubleshooting

### Service won't start
```bash
sudo journalctl -u whale-alert -n 50 --no-pager
```

### Out of memory
```bash
# Check RAM usage
free -h

# Add swap space
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### Database locked
```bash
# Check for stuck processes
ps aux | grep python

# Kill if needed
sudo pkill -f python
```

---

## Cost Comparison (After Free Tier)

| Provider | Monthly Cost | For What |
|----------|--------------|----------|
| Oracle Cloud | $0 | Always free tier |
| Fly.io | ~$5-10 | After free hours |
| AWS | ~$15-30 | t3.micro instance |
| DigitalOcean | ~$6-20 | Basic droplet |

**Recommendation:** Start with Oracle Cloud free tier - it's truly free and sufficient for paper trading.

---

## Next Steps

1. ✅ Create Oracle Cloud account
2. ✅ Create VM instance
3. ✅ Deploy code using scripts above
4. ✅ Configure .env with API keys
5. ✅ Start service via systemd
6. ✅ Monitor for 1 week
7. ✅ Adjust based on performance

---

## Need Help?

- Oracle Cloud Docs: https://docs.oracle.com/en-us/iaas/
- Fly.io Docs: https://fly.io/docs/
- SystemD Docs: https://www.freedesktop.org/software/systemd/man/systemd.service.html
