#!/bin/bash
# Oracle Cloud Tokyo Setup - Optimized for Asia
# Run this on your Oracle Cloud VM (Tokyo region)

set -e

echo "=== Whale Alert Service - Tokyo Region Setup ==="

# Update system (fast with Japan mirrors)
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

# Upload your code (from your local machine):
# scp -r /mnt/d/finance/whale-alert-service/* ubuntu@YOUR_TOKYO_IP:~/whale-alert/

# Create virtual environment
python3.12 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
# Use Japan PyPI mirror for faster downloads
pip install -i https://pypi.python.org/simple -r requirements.txt

# Setup systemd service
sudo tee /etc/systemd/system/whale-alert.service > /dev/null <<EOF
[Unit]
Description=Whale Alert Service (Tokyo)
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

# Setup firewall
sudo ufw allow 8888/tcp
sudo ufw allow 22/tcp
sudo ufw --force enable

echo "=== Tokyo Setup Complete ==="
echo "Public IP: $(curl -s ifconfig.me)"
echo "Dashboard: http://$(curl -s ifconfig.me):8888"
echo ""
echo "🇯🇵 Tokyo latency test:"
ping -c 3 tokyping.vultr.com  # Reference: ~5-10ms from East Asia
