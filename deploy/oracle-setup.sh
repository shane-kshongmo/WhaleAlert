#!/bin/bash
# Oracle Cloud Deployment Script for Whale Alert Service
# Run this on your Oracle Cloud Free Tier instance

set -e

echo "=== Whale Alert Service - Oracle Cloud Setup ==="

# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.12
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev git

# Install system dependencies
sudo apt install -y build-essential

# Create app directory
mkdir -p ~/whale-alert
cd ~/whale-alert

# Clone your repository (replace with your repo URL)
# git clone https://github.com/YOUR_USERNAME/WhaleAlert.git .

# Or upload files via rsync from your local machine:
# rsync -avz --exclude 'venv' --exclude '__pycache__' --exclude '*.pyc' \
#   --exclude '.git' --exclude 'logs' --exclude '*.db' \
#   /mnt/d/finance/whale-alert-service/ user@YOUR_ORACLE_IP:~/whale-alert/

# Create virtual environment
python3.12 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Setup systemd service for auto-restart
sudo tee /etc/systemd/system/whale-alert.service > /dev/null <<EOF
[Unit]
Description=Whale Alert Service
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/home/$USER/whale-alert
Environment="PATH=/home/$USER/whale-alert/venv/bin"
ExecStart=/home/$USER/whale-alert/venv/bin/python main.py
Restart=always
RestartSec=10
StandardOutput=append:/home/$USER/whale-alert/logs/service.log
StandardError=append:/home/$USER/whale-alert/logs/service.log

[Install]
WantedBy=multi-user.target
EOF

# Create logs directory
mkdir -p logs

# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable whale-alert
sudo systemctl start whale-alert

# Setup firewall (if needed)
sudo ufw allow 8888/tcp  # Dashboard
sudo ufw allow 22/tcp    # SSH

echo "=== Setup Complete ==="
echo "Service status: sudo systemctl status whale-alert"
echo "View logs: sudo journalctl -u whale-alert -f"
echo "Dashboard: http://$(curl -s ifconfig.me):8888"
