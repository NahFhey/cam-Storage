# CAM Tracking Kiosk - Phase 1

An offline-first web application for tracking cutting tool (CAM) movements on the shop floor. Designed to run on a Raspberry Pi with a touchscreen in Chromium kiosk mode.

## Overview

This system allows shop-floor workers to quickly record tool movements between four stations:
- **Active**: Currently in use on production floor
- **Sharpen**: Needs sharpening/maintenance
- **Cabinet**: Ready and available for use
- **Refill**: Needs grinding/refurbishment

The application maintains a complete audit log of all movements, computes priority hot lists, and provides analytics dashboards to optimize tool availability and workflow.

## Features

### Phase 1 Includes:
- ✅ Manual entry of tool identifiers (no QR scanning yet, but designed for keyboard-wedge input)
- ✅ Touch-first UI with large buttons optimized for shop floor use
- ✅ Station movement tracking with full audit trail
- ✅ Optional auto-bump: moving to Active can automatically bump existing Active tool to Sharpen
- ✅ Undo capability for correcting mistakes
- ✅ Priority hot list computation based on available sets and refill counts
- ✅ Analytics dashboards (moves, dwell times, cycle counts, sharpen backlog)
- ✅ Search functionality by S-number, set, or cam
- ✅ Job and tool management interface
- ✅ Bulk CAM item creation
- ✅ Data export (CSV and SQLite database download)
- ✅ Offline-first: everything runs locally, no internet required

### Phase 2/3 Future Additions:
- QR code scanning via keyboard-wedge input (hardware already supported)
- Multi-kiosk synchronization (if needed)
- User authentication and operator tracking
- Advanced analytics and reporting
- Die steel tracking and lifecycle management
- Email/SMS alerts for urgent priority jobs
- Historical trend analysis and forecasting

## Physical Shop Floor Workflow

### Daily Operations:
1. **Worker picks up a tool** → Scans or types identifier (e.g., "S1793-1-2")
2. **System shows current station** → Worker selects new station with big button
3. **Move is recorded** → Audit log updated, priority recalculated
4. **Next tool** → Input auto-focuses for fast scanning

### Priority Management:
- Supervisors check **Hot List** to see urgent jobs
- Priority increases when:
  - Only 1 set available (+1)
  - No sets available (+3)
  - Tools in refill (+1 each)
- Hot list updates in real-time as tools move

### Error Correction:
- **Undo button** on move screen reverts last move
- Mistakes are corrected immediately without data loss

## Installation

### Requirements:
- Python 3.8 or later
- SQLite 3 (included with Python)
- Modern web browser (Chromium recommended for kiosk mode)

### On Raspberry Pi or Linux:

```bash
# 1. Clone or copy the repository
cd /home/pi/cam-tracking

# 2. Install Python dependencies
pip3 install -r requirements.txt

# 3. Initialize the database
python3 database.py

# 4. (Optional) Seed test data
python3 seed_data.py

# 5. Start the server
python3 main.py
```

The server will start on `http://0.0.0.0:8000`

### On Windows (Development):

```cmd
# 1. Install Python 3.8+ from python.org

# 2. Install dependencies
pip install -r requirements.txt

# 3. Initialize database
python database.py

# 4. (Optional) Seed test data
python seed_data.py

# 5. Start server
python main.py
```

Access the application at `http://localhost:8000`

## Configuration

### Environment Variables:
Create a `.env` file (optional) or set these in your shell:

```bash
# Database location
export CAM_DB_PATH="./cam_tracking.db"

# Auto-bump feature (can also be toggled in Admin UI)
export AUTO_BUMP_ENABLED="false"

# Server settings
export HOST="0.0.0.0"
export PORT="8000"
```

### Runtime Configuration:
The **Admin** page provides a UI to toggle:
- **Auto-Bump**: When moving a tool to Active, automatically move any existing Active tool from the same job+set to Sharpen

## Running on Raspberry Pi Kiosk Mode

### Setup Script:
Use the provided startup script for production deployment:

```bash
# 1. Make script executable
chmod +x start_kiosk.sh

# 2. Run on boot (add to /etc/rc.local or create systemd service)
sudo nano /etc/rc.local

# Add before "exit 0":
/home/pi/cam-tracking/start_kiosk.sh &
```

### Manual Kiosk Mode:

```bash
# 1. Start the server in background
python3 main.py &

# 2. Wait for server to start
sleep 3

# 3. Launch Chromium in kiosk mode
chromium-browser \
  --kiosk \
  --start-fullscreen \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  http://localhost:8000
```

### Systemd Service (Recommended):

Create `/etc/systemd/system/cam-tracking.service`:

```ini
[Unit]
Description=CAM Tracking Kiosk Server
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/cam-tracking
ExecStart=/usr/bin/python3 /home/pi/cam-tracking/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable cam-tracking
sudo systemctl start cam-tracking
```

## User Guide

### Entry Screen (Main)
- Type or scan tool identifier: `S1793 SET1 CAM2`, `1793-1-2`, `S1793-C2-SET1`
- System is permissive: ignores extra spaces, punctuation, case
- If multiple tools match, selection list appears
- After selection, move screen shows with 4 station buttons

### Hot List Screen
- Shows all jobs sorted by priority (Urgent → Low)
- Priority calculation:
  - Base priority from job settings
  - +1 if only 1 set available
  - +3 if no sets available
  - +1 for each tool in refill
- Auto-refreshes every 30 seconds
- Click "View" to see job details

### Search Screen
- Search by S-number, job title, set, cam
- Returns matching jobs and individual tools
- Fast lookup for specific tools or jobs

### Analytics Screen
- **Station Counts**: Current distribution of tools
- **Recent Moves**: Activity over last 7 days
- **Dwell Times**: Average hours tools spend in each station
- **Sharpen Backlog**: How many tools waiting and average wait time
- **Cycle Counts**: Most-used tools (moves into Active)
- Auto-refreshes every 60 seconds

### Admin Screen
- **Configuration**: Toggle auto-bump feature
- **Create Job**: Add new production jobs (S-numbers)
- **Bulk Create CAM Items**: Generate sets and cams for a job quickly
- **Export Data**: Download CSV files or entire database backup
- **Jobs List**: View all jobs in system

## Data Model

### Tables:

**jobs**
- `s_number`: Production job identifier (e.g., "1793")
- `title`: Optional job description
- `priority_base`: Base priority (0-10)
- `created_at`: When job was added

**cam_items**
- `job_id`: Foreign key to jobs
- `set_no`: Set number (1, 2, 3...)
- `cam_no`: CAM number within set (1, 2, 3, 4...)
- `status_station`: Current station (active, sharpen, cabinet, refill)
- `status_updated_at`: Last move timestamp
- `enter_die_steel`, `exit_die_steel`: Optional die steel specs
- `notes`: Optional notes
- `eol_cycles_expected`: Expected end-of-life cycles

**moves** (immutable audit log)
- `cam_item_id`: Foreign key to cam_items
- `from_station`: Previous station
- `to_station`: New station
- `moved_at`: Timestamp
- `operator`: Who made the move (default: "kiosk")
- `undone`: Boolean flag if move was undone
- `notes`: Optional move notes

### Manual Entry Parsing:
The system accepts multiple formats:
- `S1793 SET1 CAM2` - Full format with keywords
- `1793-1-2` - Compact format (S# - Set - Cam)
- `S1793-C2-SET1` - Variant with C prefix
- `1793 1 2` - Space-separated
- Partial: `S1793 SET1` - Returns all cams in that set
- Partial: `S1793` - Returns all cams for that job

## API Reference

See `main.py` for full FastAPI documentation.

Key endpoints:
- `GET /api/jobs` - List all jobs
- `POST /api/jobs` - Create job
- `GET /api/jobs/{id}` - Get job with cam items
- `POST /api/cam-items` - Create single cam item
- `POST /api/cam-items/bulk` - Bulk create cam items
- `POST /api/resolve-entry` - Parse manual entry string
- `POST /api/moves` - Move cam to station
- `POST /api/moves/undo/{cam_item_id}` - Undo last move
- `GET /api/hot-list` - Get priority queue
- `GET /api/search?q=...` - Search jobs and tools
- `GET /api/analytics/*` - Various analytics endpoints
- `GET /api/export/*` - CSV and database exports

## Troubleshooting

### Server won't start:
- Check if port 8000 is already in use: `lsof -i :8000`
- Try a different port: `PORT=8080 python3 main.py`

### Database errors:
- Delete and reinitialize: `rm cam_tracking.db && python3 database.py`
- Check permissions: Database file must be writable

### Entry not found:
- Check if job exists in Admin → Jobs List
- Create job first, then create CAM items
- Use seed data script for testing: `python3 seed_data.py`

### Touch not working:
- Ensure touchscreen drivers installed on Raspberry Pi
- Calibrate touch: `sudo apt-get install xinput-calibrator`
- Test in regular browser first, then kiosk mode

### Performance issues:
- Raspberry Pi 3 or later recommended
- Use Chromium (faster than Firefox on Pi)
- Close other applications
- Consider using Raspberry Pi 4 for better performance

## Backup and Maintenance

### Database Backup:
```bash
# Via web UI: Admin → Export → Download Database

# Or manually:
cp cam_tracking.db cam_tracking_backup_$(date +%Y%m%d).db
```

### Logs:
Application logs to stdout. Redirect when running as service:
```bash
python3 main.py > cam_tracking.log 2>&1
```

### Updates:
To update the application:
1. Stop the server
2. Backup the database
3. Pull new code/copy new files
4. Restart the server

Database schema migrations are backward compatible in Phase 1.

## Development

### Project Structure:
```
cam-tracking/
├── main.py                  # FastAPI application
├── database.py              # Schema and DB initialization
├── config.py                # Configuration settings
├── entry_parser.py          # Manual entry parsing logic
├── business_logic.py        # Move operations, priority calculation
├── seed_data.py             # Test data generator
├── requirements.txt         # Python dependencies
├── start_kiosk.sh          # Startup script for Pi
├── README.md               # This file
└── static/                 # Frontend
    ├── css/
    │   └── styles.css
    ├── js/
    │   └── api.js
    ├── index.html          # Entry/Move screen
    ├── hot-list.html       # Priority queue
    ├── search.html         # Search interface
    ├── job-detail.html     # Job details
    ├── analytics.html      # Dashboards
    └── admin.html          # Admin tools
```

### Running Tests:
```bash
# Initialize test database
CAM_DB_PATH=test.db python3 database.py
CAM_DB_PATH=test.db python3 seed_data.py

# Start test server
CAM_DB_PATH=test.db python3 main.py

# Access at http://localhost:8000
```

### Adding New Features:
1. Backend: Add endpoints in `main.py`
2. Frontend: Create/modify HTML files in `static/`
3. Business logic: Extend `business_logic.py`
4. Database: Update schema in `database.py` (add migrations if needed)

## License

Proprietary - Internal use only

## Support

For issues or questions, contact the engineering team.

---

**Version**: Phase 1 (v1.0)
**Last Updated**: 2026-01-15
