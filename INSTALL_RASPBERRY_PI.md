# Raspberry Pi Installation Guide

Complete setup guide for deploying the CAM Tracking Kiosk on a Raspberry Pi with touchscreen.

## Hardware Requirements

- Raspberry Pi 3 Model B+ or later (Pi 4 recommended for better performance)
- Raspberry Pi Official 7" Touchscreen (or compatible)
- 16GB+ microSD card (Class 10 or better)
- Power supply (official 5V 2.5A+ recommended)
- Network connection (for initial setup only, not required for operation)

## Software Prerequisites

### 1. Install Raspberry Pi OS

Download and flash **Raspberry Pi OS Lite** or **Raspberry Pi OS with Desktop**:
- Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
- Choose "Raspberry Pi OS (32-bit)" or "Raspberry Pi OS Lite"
- Configure Wi-Fi and SSH in advanced options

### 2. Initial Setup

```bash
# Update system
sudo apt-get update
sudo apt-get upgrade -y

# Install required packages
sudo apt-get install -y \
    python3 \
    python3-pip \
    chromium-browser \
    unclutter \
    x11-xserver-utils \
    xinput-calibrator

# Install Python dependencies
pip3 install fastapi uvicorn python-multipart aiosqlite
```

## Application Installation

### 1. Copy Application Files

Transfer the cam-tracking directory to the Pi:

```bash
# From your development machine:
scp -r cam-tracking pi@raspberrypi.local:/home/pi/

# Or clone from git:
cd /home/pi
git clone <repository-url> cam-tracking
```

### 2. Initialize Database

```bash
cd /home/pi/cam-tracking
python3 database.py
python3 seed_data.py  # Optional: load test data
```

### 3. Test the Application

```bash
# Start the server
python3 main.py

# In another terminal or from another machine:
chromium-browser http://localhost:8000
```

Verify all screens work correctly before proceeding.

## Kiosk Mode Setup

### Option A: Systemd Service (Recommended)

```bash
# Copy service file
sudo cp /home/pi/cam-tracking/cam-tracking.service /etc/systemd/system/

# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable cam-tracking
sudo systemctl start cam-tracking

# Check status
sudo systemctl status cam-tracking
```

### Option B: Autostart Script

Edit `/etc/xdg/lxsession/LXDE-pi/autostart`:

```bash
sudo nano /etc/xdg/lxsession/LXDE-pi/autostart
```

Add at the end:

```
@/home/pi/cam-tracking/start_kiosk.sh
```

### Option C: rc.local (Simple)

Edit `/etc/rc.local`:

```bash
sudo nano /etc/rc.local
```

Add before `exit 0`:

```bash
# Start CAM Tracking Kiosk
su - pi -c "/home/pi/cam-tracking/start_kiosk.sh" &
```

## Touchscreen Configuration

### 1. Enable Touchscreen

Edit `/boot/config.txt`:

```bash
sudo nano /boot/config.txt
```

Ensure these lines are present:

```
# Enable touchscreen
dtoverlay=rpi-ft5406
```

### 2. Calibrate Touch (if needed)

```bash
# Install calibration tool
sudo apt-get install xinput-calibrator

# Run calibration
DISPLAY=:0 xinput_calibrator

# Follow on-screen instructions
# Save the calibration settings to ~/.config/lxsession/LXDE-pi/autostart
```

### 3. Rotate Display (if needed)

Edit `/boot/config.txt`:

```bash
# Rotate 90 degrees clockwise
display_rotate=1

# Rotate 180 degrees
display_rotate=2

# Rotate 270 degrees (90 ccw)
display_rotate=3
```

## Performance Optimization

### 1. Disable Unnecessary Services

```bash
sudo systemctl disable bluetooth
sudo systemctl disable triggerhappy
sudo systemctl disable avahi-daemon
```

### 2. Reduce GPU Memory (for Lite version)

Edit `/boot/config.txt`:

```
gpu_mem=16
```

### 3. Disable Screen Blanking

Edit `/etc/lightdm/lightdm.conf`:

```
[Seat:*]
xserver-command=X -s 0 -dpms
```

Or add to autostart:

```
@xset s off
@xset -dpms
@xset s noblank
```

## Network Configuration

### Offline Operation

The kiosk works entirely offline. To ensure no network issues:

```bash
# (Optional) Disable Wi-Fi after setup
sudo rfkill block wifi

# Or configure static IP for LAN only
sudo nano /etc/dhcpcd.conf
```

Add:

```
interface eth0
static ip_address=192.168.1.100/24
static routers=192.168.1.1
static domain_name_servers=192.168.1.1
```

## Security Hardening

### 1. Change Default Password

```bash
passwd
```

### 2. Disable SSH (after setup complete)

```bash
sudo systemctl disable ssh
sudo systemctl stop ssh
```

### 3. Auto-login (for kiosk)

```bash
sudo raspi-config
```

Navigate to: **System Options → Boot / Auto Login → Desktop Autologin**

## Backup Configuration

### 1. Database Backup Script

Create `/home/pi/backup_cam_db.sh`:

```bash
#!/bin/bash
BACKUP_DIR="/home/pi/cam-backups"
mkdir -p $BACKUP_DIR
cp /home/pi/cam-tracking/cam_tracking.db \
   $BACKUP_DIR/cam_tracking_$(date +%Y%m%d_%H%M%S).db

# Keep only last 30 backups
ls -t $BACKUP_DIR/cam_tracking_*.db | tail -n +31 | xargs rm -f
```

Make executable and add to cron:

```bash
chmod +x /home/pi/backup_cam_db.sh
crontab -e
```

Add:

```
# Backup database daily at 2 AM
0 2 * * * /home/pi/backup_cam_db.sh
```

### 2. SD Card Image Backup

After setup is complete, create an SD card image:

```bash
# On another Linux machine with SD card reader:
sudo dd if=/dev/sdX of=cam-tracking-kiosk.img bs=4M status=progress
gzip cam-tracking-kiosk.img
```

## Troubleshooting

### Server won't start

```bash
# Check logs
tail -f /home/pi/cam-tracking/cam_tracking.log

# Check if port is in use
sudo lsof -i :8000

# Restart service
sudo systemctl restart cam-tracking
```

### Touchscreen not working

```bash
# Check if detected
DISPLAY=:0 xinput list

# Recalibrate
DISPLAY=:0 xinput_calibrator
```

### Chromium not starting in kiosk mode

```bash
# Check if X server is running
echo $DISPLAY

# Try starting manually
DISPLAY=:0 chromium-browser --kiosk http://localhost:8000
```

### Performance issues

```bash
# Check CPU usage
top

# Check memory
free -h

# Restart Pi
sudo reboot
```

## Maintenance

### Update Application

```bash
# Stop service
sudo systemctl stop cam-tracking

# Backup database
cp /home/pi/cam-tracking/cam_tracking.db /home/pi/cam_tracking_backup.db

# Update code
cd /home/pi/cam-tracking
git pull  # or copy new files

# Restart service
sudo systemctl start cam-tracking
```

### View Logs

```bash
# Server logs
tail -f /home/pi/cam-tracking/cam_tracking.log

# System logs
sudo journalctl -u cam-tracking -f
```

### Monitor System Health

```bash
# Temperature
vcgencmd measure_temp

# CPU frequency
vcgencmd measure_clock arm

# Voltage
vcgencmd measure_volts
```

## Production Checklist

- [ ] Raspberry Pi OS installed and updated
- [ ] Application files copied and tested
- [ ] Database initialized with real jobs
- [ ] Touchscreen calibrated
- [ ] Kiosk mode starts on boot
- [ ] Screen blanking disabled
- [ ] Network configured (or disabled if offline)
- [ ] Default password changed
- [ ] Backup script configured
- [ ] SD card image backup created
- [ ] Physical mounting secured
- [ ] Power supply stable
- [ ] Testing complete with shop floor users

---

**Support**: For technical issues, contact engineering team.
