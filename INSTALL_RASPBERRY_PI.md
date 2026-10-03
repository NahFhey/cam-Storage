# Raspberry Pi Installation Guide

How to set up the CAM Tracking Kiosk on a Raspberry Pi with a touchscreen, keep it running, and update it.

The setup has two parts:

1. **The server** runs in the background as a systemd service (`cam-tracking`). It starts at boot and restarts itself if it stops.
2. **The kiosk browser** is started by `start_kiosk.sh` when the desktop logs in. It waits for the server, then opens the app full-screen in Chromium.

Everything runs on the Pi. No internet connection is needed once it's installed.

> **Usernames:** the examples assume the app lives in `~/cam-tracking` (for the `pi` user, `/home/pi/cam-tracking`). Commands use `$HOME` and `$USER`, so they work whatever username you chose in Raspberry Pi Imager.

## Hardware

- Raspberry Pi 3 Model B+ or later (Pi 4 or 5 recommended)
- Touchscreen, such as the official Raspberry Pi Touch Display
- 16 GB+ microSD card (A1/A2 rated), or an SSD on a Pi 5
- Official power supply
- Optional: a USB barcode scanner in keyboard mode (see [Barcode scanners](#barcode-scanners))
- A network connection during installation

## 1. Install Raspberry Pi OS

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to flash **Raspberry Pi OS with desktop**. Choose 64-bit on a Pi 4 or 5.

In Imager's settings, set:
- a username and password
- Wi-Fi (if you aren't using Ethernet)
- SSH enabled, so you can work on the Pi from another computer

Boot the Pi, then update it:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y git curl python3-venv
```

The desktop image already includes Chromium. If it's missing, install it. The package is `chromium` on newer releases and `chromium-browser` on older ones:

```bash
sudo apt install -y chromium || sudo apt install -y chromium-browser
```

## 2. Install the application

```bash
cd ~
git clone https://github.com/NahFhey/cam-Storage.git cam-tracking
cd ~/cam-tracking

# Python packages go in a virtual environment; current Raspberry Pi OS
# refuses system-wide `pip install`.
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

If the repository is private, `git clone` asks for your GitHub username and a [personal access token](https://github.com/settings/tokens) as the password.

No internet on the Pi? Copy the folder over instead (`scp -r cam-tracking <user>@<pi-address>:~/`), then run the two `venv` commands on the Pi with a temporary connection.

## 3. First run and admin PIN

Start the server by hand once to check it works:

```bash
cd ~/cam-tracking
venv/bin/python main.py
```

The database (`cam_tracking.db`) is created automatically on first start. Open <http://localhost:8000> in Chromium on the Pi, or `http://<pi-address>:8000` from another computer.

**Change the default admin PIN now.** A new database has one user, *Administrator*, with PIN **1234**:

1. Sign in with `1234`.
2. Go to **Admin → Users → Reset PIN** and choose a new PIN.
3. Add a user for each operator (**Admin → Users → Add user**). Moves are recorded under the signed-in user's name.

Optional, for trying things out on a test kiosk only, you can load sample jobs and tools: `venv/bin/python seed_data.py`.

Press **Ctrl+C** to stop the server.

## 4. Run the server as a service

The service file assumes the `pi` user. This command installs it with your username and home directory substituted:

```bash
cd ~/cam-tracking
sed -e "s#/home/pi#$HOME#g" -e "s#^User=pi#User=$USER#" cam-tracking.service \
    | sudo tee /etc/systemd/system/cam-tracking.service > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now cam-tracking
```

Check it:

```bash
systemctl status cam-tracking           # should say "active (running)"
curl -s http://localhost:8000/health    # should print {"status":"healthy",...}
```

Settings such as the port and database location are `Environment=` lines in `/etc/systemd/system/cam-tracking.service` (see the README's *Configuration* section). After editing that file, run `sudo systemctl daemon-reload && sudo systemctl restart cam-tracking`.

> **Network access:** by default other computers on the network can open the app at `http://<pi-address>:8000`, which is handy for checking the hot list from the office. To restrict it to the kiosk itself, change `HOST=0.0.0.0` to `HOST=127.0.0.1` in the service file.

## 5. Start the kiosk browser at login

First, make the desktop log in automatically and stop the screen from blanking:

```bash
sudo raspi-config
```

- **System Options → Boot / Auto Login → Desktop Autologin**
- **Display Options → Screen Blanking → No**

Then add `start_kiosk.sh` to the desktop's autostart. Current Raspberry Pi OS uses the **labwc** (Wayland) desktop on every Pi model:

```bash
chmod +x ~/cam-tracking/start_kiosk.sh
mkdir -p ~/.config/labwc
echo "$HOME/cam-tracking/start_kiosk.sh &" >> ~/.config/labwc/autostart
```

<details>
<summary>Older images running the X11 (LXDE) desktop</summary>

```bash
sudo apt install -y unclutter    # hides the mouse pointer when idle
mkdir -p ~/.config/lxsession/LXDE-pi
cp /etc/xdg/lxsession/LXDE-pi/autostart ~/.config/lxsession/LXDE-pi/ 2>/dev/null
echo "@$HOME/cam-tracking/start_kiosk.sh" >> ~/.config/lxsession/LXDE-pi/autostart
```

On X11 the script also turns off screen blanking itself.
</details>

Reboot to test the whole chain:

```bash
sudo reboot
```

The Pi should boot to the PIN sign-in screen, full-screen.

**Leaving kiosk mode:** with a keyboard attached, press **Alt+F4** to close Chromium. Over SSH, run `pkill chromium`. To restart the kiosk without rebooting, run `~/cam-tracking/start_kiosk.sh &` from a terminal on the desktop.

## 6. Touchscreen and display

- The official Raspberry Pi Touch Display is detected automatically. You don't need `dtoverlay` lines in `config.txt`.
- **Rotation:** use **Preferences → Screen Configuration** on the desktop. The old `display_rotate=` setting in `config.txt` does not work with the current graphics driver. (On current Raspberry Pi OS the file is `/boot/firmware/config.txt`, not `/boot/config.txt`.)
- The app has its own on-screen keypads for the PIN and for material removed, so you don't need an on-screen keyboard.

### Barcode scanners

Any USB scanner in **keyboard (HID) mode** works with no setup on the Pi. Configure the scanner to send **Enter** after each code. On the Entry screen, a scan always starts a new lookup, even when another tool is open.

## 7. Backups

**From the app:** Admin → Settings & data → *Full database backup* downloads a copy of the database. *Restore from backup* replaces all data with an uploaded backup, and saves the current database on the Pi first.

**Automatic nightly backups:** `backup_db.sh` makes a consistent copy while the server is running. It keeps the newest 30 backups in `~/cam-backups`.

```bash
chmod +x ~/cam-tracking/backup_db.sh
~/cam-tracking/backup_db.sh                # run once to check it works
crontab -e
```

Add this line (backs up every night at 2 AM):

```
0 2 * * * $HOME/cam-tracking/backup_db.sh >> $HOME/cam-backups/backup.log 2>&1
```

Copy backups off the Pi from time to time (USB stick, network share, or `scp`). An SD card failure takes its backups with it.

**Restoring by hand:**

```bash
sudo systemctl stop cam-tracking
cp ~/cam-backups/cam_tracking_YYYYMMDD_HHMMSS.db ~/cam-tracking/cam_tracking.db
rm -f ~/cam-tracking/cam_tracking.db-wal ~/cam-tracking/cam_tracking.db-shm
sudo systemctl start cam-tracking
```

Older backups are upgraded to the current database layout automatically on start.

## 8. Updating

```bash
cd ~/cam-tracking
./backup_db.sh                              # 1. back up first
git pull                                    # 2. get the new version
venv/bin/pip install -r requirements.txt    # 3. pick up any new Python packages
sudo systemctl restart cam-tracking         # 4. restart the server
sudo reboot                                 # 5. reload the kiosk screen
```

Database changes are applied automatically when the server starts. Step 5 makes sure the kiosk loads the new screens. Restarting Chromium has the same effect.

### Upgrading a kiosk installed before version 2.0

Older instructions installed Python packages system-wide and started the server from `rc.local` or the LXDE autostart. To move to this setup:

1. Remove any old start-up entries that run `python3 main.py` or `start_kiosk.sh` (check `/etc/rc.local`, `/etc/xdg/lxsession/LXDE-pi/autostart` and `~/.config/lxsession/LXDE-pi/autostart`). Running two copies of the server makes the second fail with "address already in use".
2. Back up the database (`./backup_db.sh` after `git pull`, or copy `cam_tracking.db` while the server is stopped).
3. Follow steps **2** (from `python3 -m venv venv`), **4** and **5** above. Your existing `cam_tracking.db` is kept and upgraded in place.

## 9. Logs and troubleshooting

```bash
systemctl status cam-tracking            # is the server running?
journalctl -u cam-tracking -f            # startup errors and tracebacks
tail -f ~/cam-tracking/cam_tracking.log  # application log
curl -s http://localhost:8000/health     # health, database counts and disk space
```

| Problem | Fix |
| --- | --- |
| `error: externally-managed-environment` | Install packages into the venv: `venv/bin/pip install -r requirements.txt`, not `pip3 install`. |
| `ModuleNotFoundError` (e.g. `slowapi`) in the journal | The venv is missing packages, or the service is using the wrong Python. Re-run the pip command, then check that `ExecStart` points at `venv/bin/python`. |
| `address already in use` | Another copy of the server is running, often an old `rc.local` or autostart entry. Find it with `sudo lsof -i :8000`. |
| Screen stays black or shows the desktop | Check `~/.config/labwc/autostart`, then run `~/cam-tracking/start_kiosk.sh` in a desktop terminal to see its messages. |
| "Server not reachable" from `start_kiosk.sh` | The service didn't come up within 60 seconds. Check `journalctl -u cam-tracking`. |
| Old screens after an update | Restart Chromium or reboot (step 5 of *Updating*). |
| Forgot the admin PIN | Ask another admin to reset it in **Admin → Users**. |
| Touches land in the wrong place after rotating | Rotate with **Preferences → Screen Configuration** rather than `config.txt`. |

**Pi health:**

```bash
vcgencmd measure_temp       # under ~80°C is fine; add a heatsink or fan if higher
vcgencmd get_throttled      # 0x0 = no under-voltage or throttling
free -h
df -h ~                     # disk space (also shown by /health)
```

## 10. Security

- Change the default admin PIN `1234` (step 3).
- Use a strong Raspberry Pi OS password, set in Imager or with `passwd`.
- Restrict the app to the kiosk with `HOST=127.0.0.1` if nobody else needs it (step 4).
- Turn off SSH when you no longer need remote access: `sudo systemctl disable --now ssh`.
- A user who is signed in is signed out after 5 minutes without activity, and the screen returns to the PIN pad.

## Production checklist

- [ ] Raspberry Pi OS installed and updated
- [ ] App installed in `~/cam-tracking` with its `venv`
- [ ] Default admin PIN changed; operator users created
- [ ] Real jobs and tools entered (no seed data)
- [ ] `cam-tracking` service enabled and healthy
- [ ] Desktop autologin on, screen blanking off
- [ ] Kiosk opens full-screen after a reboot
- [ ] Touchscreen orientation correct; barcode scanner sends Enter
- [ ] Nightly backup in cron and tested; backups copied off the Pi
- [ ] SSH disabled or secured
- [ ] Tested on the shop floor with operators

---

**Support**: For technical issues, contact the engineering team.
