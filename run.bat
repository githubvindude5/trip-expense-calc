@echo off
echo Installing dependencies...
pip install -r requirements.txt
echo.
echo Starting Trip Expenses app...
echo Open http://localhost:5000 in your browser
echo Press Ctrl+C to stop
echo.
python app.py
pause
