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

### Current Features:
- ✅ Manual entry of tool identifiers (designed for keyboard-wedge input)
- ✅ Touch-first UI with large buttons optimized for shop floor use, on-screen keypads for PIN and material entry
- ✅ Scan anytime: typing anywhere on the Entry screen starts a new lookup (keyboard-wedge friendly)
- ✅ Light and dark themes (follows the OS, toggle in the top bar)
- ✅ Atomic whole-set moves (all cams move, or none do)
- ✅ Station movement tracking with full audit trail
- ✅ Optional auto-bump: moving to Active can automatically bump existing Active tool to Sharpen
- ✅ Undo capability for correcting mistakes (from the move screen or straight from the confirmation toast)
- ✅ Priority hot list computation based on available sets and refill counts
- ✅ Analytics dashboards (moves, dwell times, cycle counts, sharpen backlog, refill forecast)
- ✅ Search functionality by S-number, set, or cam
- ✅ Job and tool management interface
- ✅ Bulk CAM item creation with die position configuration
- ✅ Data export (CSV and SQLite database download) and database import/restore
- ✅ PIN-based user authentication with role-based access (admin/user)
- ✅ Tool lifespan tracking with material removal and refill forecasting
- ✅ Batch move operations (up to 50 items per request)
- ✅ Rate limiting and input validation
- ✅ SQLite connection pooling and TTL caching for performance
- ✅ Offline-first: everything runs locally, no internet required

### Future Additions:
- QR code scanning via keyboard-wedge input (hardware already supported)
- Multi-kiosk synchronization (if needed)
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
- Score = base priority (Low 0 … Top 4) plus:
  - Every tool in Sharpen (+100)
  - A cam position blocked: every copy in Sharpen or Refill (+100)
  - No cabinet stock while a full set is active (+2)
  - No sets available (+2), or only 1 set available (+1)
  - 3 or more tools in Refill (+1)
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

# Default material life for tool lifespans (inches)
export DEFAULT_MATERIAL_LIFE="0.375"

# Session duration (hours, default: 8-hour shift)
export SESSION_DURATION_HOURS="8"

# Server settings
export HOST="0.0.0.0"
export PORT="8000"
```

### Runtime Configuration:
The **Admin** page provides a UI to toggle:
- **Auto-Bump**: When moving a tool to Active, automatically move any existing Active tool from the same job+set to Sharpen
- **Default Material Life**: Default max material life for new tool lifespans

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
- Type or scan tool identifier: `S1793 SET1 CAM2`, `1793-1-2`, `S1793-C2-SET1`, or just `1793` for the whole job
- System is permissive: ignores extra spaces, punctuation, case
- Typing anywhere on the page jumps to the entry box, so a scan always starts a fresh lookup
- If several tools match, tap a cam to move it or tap a set to move every cam in it
- The move screen shows the current station, material life, and the last five moves; press **Esc** to cancel
- Sharpen → Cabinet asks for material removed on an on-screen keypad (type thousandths: `12` = 0.012")
- Moving a whole set collects every material reading first; cancelling leaves the set untouched
- Every move shows a confirmation with an **Undo** button
- While idle, the screen shows tools per station, the top of the hot list, and recent activity from every kiosk

### Hot List Screen
- Jobs with tools waiting in Sharpen, ranked by score (see "How ranking works" on the page)
- Flags jobs with a **blocked** cam position or with **every tool in Sharpen**
- Shows each job's station distribution and how many sets are ready
- Columns are sortable; auto-refreshes every 30 seconds; tap a row for job details

### Search Screen
- Live search by S-number, job title, or any tool format
- Tool results link straight to the move screen

### Job Detail Screen
- Tools per station, then every tool with sharpening history and material life used

### Analytics Screen
- KPI tiles, station distribution with average dwell time
- Moves per day and material removed per day (7 / 30 / 90 days)
- Station transitions, operator activity, refill forecast, lifespan statistics
- Most sharpened and most cycled tools

### Admin Screen (requires admin login)
- **Jobs**: create, edit, delete; change priority (a reason is recorded in the audit log)
- **Tools**: add sets & cams with a live preview; edit die position, orientation, material life and notes (auto-saves)
- **Top 5**: reorder by drag or ▲▼ buttons (touch friendly), swap jobs in, review priority history and escalation analytics
- **Users**: add users, reset PINs, change roles, deactivate/reactivate
- **Settings & data**: auto-bump, default material life, CSV exports, database backup and restore

## Data Model

### Tables:

**users**
- `username`: Unique login identifier
- `display_name`: Name shown in UI and audit log
- `pin_hash`: Hashed PIN for authentication
- `role`: `admin` or `user`
- `active`: Whether the account is active

**jobs**
- `s_number`: Production job identifier (e.g., "1793")
- `title`: Optional job description
- `priority_level`: Categorical priority (low, medium, high, urgent, top)
- `notes`: Optional notes
- `created_at`: When job was added

**cam_items**
- `job_id`: Foreign key to jobs
- `set_no`: Set number (1, 2, 3...)
- `cam_no`: CAM number within set (1, 2, 3, 4...)
- `die_position`: Optional (upper/lower)
- `status_station`: Current station (active, sharpen, cabinet, refill)
- `status_updated_at`: Last move timestamp
- `enter_die_steel`, `exit_die_steel`: Optional die steel specs
- `max_material_life`: Maximum material life in inches (default 0.375)
- `notes`: Optional notes
- `eol_cycles_expected`: Expected end-of-life cycles

**moves** (immutable audit log)
- `cam_item_id`: Foreign key to cam_items
- `from_station`: Previous station
- `to_station`: New station
- `moved_at`: Timestamp
- `operator`: Who made the move (logged-in user's display name)
- `material_removed`: Inches removed (for sharpen→cabinet moves)
- `undone`: Boolean flag if move was undone
- `notes`: Optional move notes

**tool_lifespans**
- `cam_item_id`: Foreign key to cam_items
- `lifespan_number`: Sequential lifespan count
- `max_material_life`: Maximum material for this lifespan
- `total_material_removed`: Cumulative material removed
- `sharpen_count`: Number of sharpen cycles in this lifespan
- `started_at`, `ended_at`: Lifespan date range

**sessions**
- `token`: Bearer token for API authentication
- `user_id`: Foreign key to users
- `expires_at`: Session expiry time

### Manual Entry Parsing:
The system accepts multiple formats:
- `S1793 SET1 CAM2` - Full format with keywords
- `1793-1-2` - Compact format (S# - Set - Cam)
- `S1793-C2-SET1` - Variant with C prefix
- `1793 1 2` - Space-separated
- Partial: `S1793 SET1` - Returns all cams in that set
- Partial: `S1793` - Returns all cams for that job

## API Reference

Interactive documentation is served at `/docs` while the server is running. Endpoints live in `app/routers/`.

### Authentication:
- `POST /api/auth/login` - Log in with PIN, returns Bearer token
- `POST /api/auth/logout` - Invalidate session
- `GET /api/auth/me` - Get current user info

### User Management (admin only):
- `GET /api/users` - List all users
- `POST /api/users` - Create user
- `PATCH /api/users/{id}` - Update user
- `DELETE /api/users/{id}` - Deactivate user

### Jobs & CAM Items:
- `GET /api/jobs` - List jobs (paginated)
- `POST /api/jobs` - Create job (admin)
- `GET /api/jobs/{id}` - Get job with cam items
- `GET /api/jobs/{id}/tool-stats` - Sharpening and lifespan stats for every tool in a job
- `PATCH /api/jobs/{id}` - Update job (admin)
- `DELETE /api/jobs/{id}` - Delete job (admin)
- `GET /api/cam-items` - List cam items (filterable, paginated)
- `POST /api/cam-items` - Create single cam item (admin)
- `POST /api/cam-items/bulk` - Bulk create cam items (admin)
- `PATCH /api/cam-items/{id}` - Update cam item (admin)
- `POST /api/resolve-entry` - Parse manual entry string

### Moves:
- `POST /api/moves` - Move cam to station (requires login)
- `POST /api/moves/set` - Move a whole set atomically; moving to Active bumps conflicting sets (requires login)
- `POST /api/moves/batch` - Batch move up to 50 items (requires login)
- `POST /api/moves/undo/{cam_item_id}` - Undo last move (requires login)
- `GET /api/moves` - List recent moves

### Analytics & Data:
- `GET /api/hot-list` - Priority queue (cached)
- `GET /api/search?q=...` - Search jobs and tools
- `GET /api/analytics/station-counts` - Station distribution (cached)
- `GET /api/analytics/moves-recent` - Recent move activity
- `GET /api/analytics/dwell-times` - Average dwell times
- `GET /api/analytics/cycle-counts` - Cycle counts per tool
- `GET /api/analytics/sharpen-backlog` - Sharpen backlog stats
- `GET /api/analytics/refill-forecast` - Tools approaching refill
- `GET /api/analytics/material-stats` - Most-sharpened tools with last/average material removed
- `GET /api/analytics/station-transitions`, `operator-activity`, `lifespan-stats`, `material-trends`
- `GET /api/cam-items/{id}/lifespan` - Lifespan forecast for a tool
- `GET /api/cam-items/{id}/sharpen-stats` - Sharpen history stats
- `GET /api/export/*` - CSV and database exports (requires login)
- `POST /api/import/database` - Database restore (admin)
- `GET /health` - Health check endpoint

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
├── main.py                  # Server entry point: builds the FastAPI app, mounts routers and static files
├── app/
│   ├── routers/             # API endpoints, grouped by area
│   │   ├── auth.py          #   login/logout, users
│   │   ├── jobs.py          #   jobs CRUD, per-job tool stats
│   │   ├── cam_items.py     #   tools CRUD, bulk create, per-tool stats
│   │   ├── moves.py         #   entry resolution, single/set/batch moves, undo
│   │   ├── priority.py      #   hot list, Top 5, priority audit log
│   │   ├── analytics.py     #   search and analytics
│   │   └── system.py        #   health, config, CSV exports, backup/restore
│   ├── schemas.py           # Request models and validation
│   ├── deps.py              # Sessions and auth dependencies
│   ├── utils.py             # Query, update, audit and CSV helpers
│   ├── cache.py             # TTL cache
│   └── limiter.py           # Rate limiter
├── business_logic.py        # Moves, set moves, undo, priority ranking, lifespan tracking
├── entry_parser.py          # Manual entry parsing and resolution
├── database.py              # Schema, migrations, connection pooling
├── config.py                # Configuration settings
├── seed_data.py             # Test data generator
├── tests/                   # Test suite
└── static/                  # Frontend (no build step, works offline)
    ├── css/styles.css       # Design system: tokens, light/dark themes, components
    ├── js/
    │   ├── ui.js            # Escaping, formatting, icons, toasts, dialogs, keypad, sortable tables
    │   ├── api.js           # API client and session storage
    │   ├── layout.js        # Top bar, navigation, theme toggle
    │   └── auth.js          # PIN login, session checks, inactivity logout
    ├── index.html           # Entry/Move screen
    ├── hot-list.html        # Priority queue
    ├── search.html          # Search
    ├── job-detail.html      # Job details
    ├── analytics.html       # Dashboards
    └── admin.html           # Admin tools
```

### Running Tests:
```bash
pytest tests/ -v

# See TESTING.md for detailed test guide
```

### Adding New Features:
1. Backend: Add endpoints to the matching router in `app/routers/` (new routers are registered in `main.py`)
2. Frontend: Create/modify HTML files in `static/`; reuse helpers from `static/js/ui.js` and escape user data with `UI.esc`
3. Business logic: Extend `business_logic.py`
4. Database: Update schema in `database.py` (add migrations if needed)

## License

Proprietary - Internal use only

## Support

For issues or questions, contact the engineering team.

---

**Version**: 2.0.0
**Last Updated**: 2026-10-03
