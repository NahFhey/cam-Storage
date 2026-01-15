# Quick Start Guide

Get the CAM Tracking Kiosk running in 5 minutes for testing and development.

## Prerequisites

- Python 3.8 or later
- Web browser (Chrome/Chromium recommended)

## Installation

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Initialize database
python database.py

# 3. Load test data
python seed_data.py

# 4. Start server
python main.py
```

Server will start at: **http://localhost:8000**

## Test the Application

### 1. Entry Screen (Main Page)
- Open http://localhost:8000
- Try entering these test tools:
  - `S1793 SET1 CAM1` (exact match)
  - `1793-1-2` (compact format)
  - `S1793` (multiple matches - shows selection list)

### 2. Move a Tool
- After entering a tool, you'll see 4 station buttons
- Click any station to move the tool
- Try the **Undo** button to revert

### 3. Hot List
- Navigate to **Hot List** in the menu
- See jobs sorted by priority
- Click **View** on any job

### 4. Search
- Navigate to **Search**
- Try searching: `1793`, `Front Panel`, `Set 1`

### 5. Analytics
- Navigate to **Analytics**
- View station counts, recent moves, dwell times, etc.

### 6. Admin
- Navigate to **Admin**
- Toggle **Auto-Bump** feature
- Create a new job
- Bulk create CAM items
- Export data (CSV or database)

## Test Data Included

The seed script creates:

**S1793** - Front Panel Assembly
- 3 sets, 4 cams each
- Mixed stations (some active, some in cabinet, some need refill)
- Priority: High

**S2104** - Rear Housing Unit
- 2 sets, 6 cams each
- Mostly in cabinet, one active
- Priority: Medium

**S2387** - Control Box Cover
- 1 set, 4 cams
- All in refill (urgent scenario!)
- Priority: Low → becomes Urgent due to all refill

**S2501** - Mounting Bracket
- 4 sets, 3 cams each
- Includes move history for analytics testing
- Priority: Urgent

**S2645** - (No title)
- 2 sets, 5 cams each
- Heavy sharpen backlog
- Priority: Medium

## Common Test Scenarios

### Test Auto-Bump Feature
1. Go to **Admin** → toggle **Auto-Bump** ON
2. Find a tool currently in cabinet (e.g., `S1793-1-2`)
3. Move it to **Active**
4. Now enter `S1793-1-1` (currently active)
5. Move it to **Active**
6. The system should auto-bump the first tool to **Sharpen**

### Test Undo
1. Enter any tool
2. Move it to a different station
3. Click **Undo Last Move**
4. Tool reverts to previous station

### Test Priority Calculation
1. Go to **Hot List**
2. Notice **S2387** is urgent (all tools in refill)
3. Go to entry, move `S2387-1-1` to **Cabinet**
4. Refresh **Hot List** - priority should decrease

### Test Search
1. Search `1793` - finds job and all its tools
2. Search `Front Panel` - finds by title
3. Search `set 1` - finds tools in set 1

### Test Analytics
1. Make several moves with different tools
2. Go to **Analytics**
3. See move counts increase
4. Check dwell times per station

## Development Tips

### Use Different Database
```bash
# Test database
CAM_DB_PATH=test.db python database.py
CAM_DB_PATH=test.db python seed_data.py
CAM_DB_PATH=test.db python main.py
```

### Enable Debug Mode
```python
# In main.py, change:
uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=True)
```

### Reset Everything
```bash
rm cam_tracking.db
python database.py
python seed_data.py
python main.py
```

## API Testing

### Using curl:

```bash
# Get all jobs
curl http://localhost:8000/api/jobs

# Resolve entry
curl -X POST http://localhost:8000/api/resolve-entry \
  -H "Content-Type: application/json" \
  -d '{"entry": "S1793-1-1"}'

# Move tool
curl -X POST http://localhost:8000/api/moves \
  -H "Content-Type: application/json" \
  -d '{
    "cam_item_id": 1,
    "to_station": "sharpen"
  }'

# Get hot list
curl http://localhost:8000/api/hot-list

# Get analytics
curl http://localhost:8000/api/analytics/station-counts
```

### Using Browser DevTools:
Open browser console (F12) and try:

```javascript
// Get hot list
fetch('/api/hot-list').then(r => r.json()).then(console.log)

// Resolve entry
fetch('/api/resolve-entry', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({entry: 'S1793-1-1'})
}).then(r => r.json()).then(console.log)
```

## Next Steps

1. **Customize**: Edit test data in `seed_data.py`
2. **Extend**: Add new endpoints in `main.py`
3. **Deploy**: Follow `INSTALL_RASPBERRY_PI.md` for production setup

## Troubleshooting

**Port already in use:**
```bash
# Find process using port 8000
lsof -i :8000
# Or use different port
PORT=8080 python main.py
```

**Database locked:**
```bash
# Stop all Python processes
pkill python3
rm cam_tracking.db-journal
```

**Missing dependencies:**
```bash
pip install --upgrade -r requirements.txt
```

---

**Enjoy testing!** Report issues or suggestions to the engineering team.
