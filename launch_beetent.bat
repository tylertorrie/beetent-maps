@echo off
REM Launch the Beetent Maps app. Git pull runs in the background so the
REM GUI opens immediately even if the network is slow or git stalls on a
REM credential prompt; the in-app auto-update (5-min interval) keeps the
REM code in sync from there.
cd /d "C:\Users\tyler\beetent-maps"
start "git pull" /B git pull
start "" "C:\Users\tyler\AppData\Local\Programs\Python\Python314\pythonw.exe" "C:\Users\tyler\beetent-maps\beetent_app.py"
