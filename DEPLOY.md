# Deployment Guide

## Option A: Render.com (Recommended — easiest persistent disk)

1. Create a free account at https://render.com
2. Push this folder to a GitHub repo (see "Push to GitHub" below)
3. In Render dashboard → New → Web Service → connect your GitHub repo
4. Render auto-detects the `render.yaml` — just click Deploy
5. Your app is live at: https://trip-expenses-XXXX.onrender.com

**Persistent disk**: `render.yaml` already configures a 1 GB disk at `/data`.
Your trips.json is safe across restarts.

---

## Option B: Railway.app

1. Create a free account at https://railway.app
2. New Project → Deploy from GitHub repo
3. Add a Volume in Railway dashboard:
   - Go to your service → Volumes → Add Volume
   - Mount path: `/data`
4. Set environment variable: `SECRET_KEY` = any random string
5. Done — Railway auto-uses the `Procfile`

---

## Option C: Docker (self-hosted or any VPS)

Build and run locally or on any server with Docker:

```bash
# Build
docker build -t trip-expenses .

# Run with persistent data volume
docker run -d \
  -p 8080:8080 \
  -v trip-data:/data \
  -e SECRET_KEY=your-secret-here \
  --name trip-expenses \
  trip-expenses
```

Access at: http://your-server-ip:8080

To update after code changes:
```bash
docker build -t trip-expenses .
docker stop trip-expenses && docker rm trip-expenses
docker run -d -p 8080:8080 -v trip-data:/data -e SECRET_KEY=your-secret-here --name trip-expenses trip-expenses
```

---

## Push to GitHub (required for Render/Railway)

```bash
cd "C:\Users\vinit.kumar\OneDrive - Forcepoint, LLC\old Documents\Claude\trip_expenses"

git init
git add .
git commit -m "Initial commit: trip expenses app"

# Create repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/trip-expenses.git
git push -u origin main
```

---

## Notes

- `trips.json` is in `.gitignore` — your trip data is never pushed to GitHub
- The app auto-detects `/data` directory (cloud) vs local `data/` folder
- Change `SECRET_KEY` env var in production for security
