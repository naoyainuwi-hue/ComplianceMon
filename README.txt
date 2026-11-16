COMPLIANCE AUTO-FILTER — CENTRALIZED SERVER
=============================================

WHAT THIS IS
------------
The same Compliance Auto-Filter tool you already had, redesigned, plus a
small Python server so every PC on your network shares ONE database
(compliance_data.db, a SQLite file) instead of each browser keeping its own
local copy.

FILES
-----
  ComplianceAutoFilter.html   The tool itself (don't move it — server.py looks
                              for it in the same folder).
  server.py                   The server. Python standard library only —
                              nothing to pip install (SQLite support is
                              built into Python already).
  start_server.bat            Double-click launcher for Windows (manual run —
                              see "RUNNING WITH DOCKER" below for a version
                              that starts itself and stays running).
  Dockerfile / docker-compose.yml / .dockerignore
                              Runs the same server.py in a container that
                              restarts on its own — see "RUNNING WITH DOCKER".
  compliance_data.db          Created automatically the first time you run
                              the server. This IS your shared database — a
                              SQLite file (with WAL mode on, so you may also
                              see compliance_data.db-wal / -shm next to it;
                              that's normal, leave them alone). Back it up /
                              put it on a shared drive if you want, see
                              "Moving the database" below. If you're
                              upgrading from the old JSON-file version, see
                              "UPGRADING FROM THE OLD JSON VERSION" below —
                              your history is migrated automatically.

HOW TO RUN IT (the PC that will host the tool) — MANUAL / start_server.bat
-----------------------------------------------------------------------------
1. Make sure Python 3.8+ is installed on this PC.
   Check with:  python --version
   If missing, install from https://www.python.org/downloads/ and tick
   "Add python.exe to PATH" during setup.

2. Put all the files above in one folder, e.g.
   C:\Tools\ComplianceAutoFilter\

3. Double-click start_server.bat
   (or run "python server.py" from a terminal in that folder)

4. The console window will print something like:

     On this PC      ->  http://localhost:8000
     From other PCs  ->  http://172.16.xx.xx:8000

   Keep that window open — closing it stops the server.

5. Open the "On this PC" link in your browser. Anyone else on the same
   network/VPN can open the "From other PCs" link to use the exact same
   tool and the exact same database.

RUNNING WITH DOCKER (recommended — no bat file, no manual restart)
-----------------------------------------------------------------------------
This runs server.py inside a container that restarts itself automatically
(after a crash, after Docker/Windows updates, after a reboot once Docker
Desktop is set to launch at login) — you never have to double-click
start_server.bat or add anything to the Windows Startup folder yourself.

1. Install Docker Desktop: https://www.docker.com/products/docker-desktop/
   During/after install, enable "Start Docker Desktop when you sign in"
   in Docker Desktop's Settings > General — this is the ONE thing that
   replaces "put it in startup", since Docker Desktop itself then launches
   your container automatically every time this PC boots.

2. Put ALL the files from this folder (including Dockerfile,
   docker-compose.yml, .dockerignore) on the host PC, e.g.
   C:\Tools\ComplianceAutoFilter\

3. Open a terminal (PowerShell/CMD) in that folder and run:
     docker compose up -d --build

   That builds the image once and starts it in the background ("-d").
   You won't see a console window at all — check it's running with:
     docker compose ps

4. Same links as before:
     On this PC      ->  http://localhost:8000
     From other PCs  ->  http://<this-PC's-LAN-IP>:8000
   (run `ipconfig` on the host PC if you need the LAN IP, or check the
   server.py startup banner via `docker compose logs`)

5. That's it — it keeps running in the background, restarts itself if it
   ever crashes ("restart: unless-stopped" in docker-compose.yml), and
   comes back up automatically on reboot once Docker Desktop auto-starts.
   It only stays stopped if you explicitly run `docker compose stop`.

DATA PERSISTENCE WITH DOCKER
------------------------------
docker-compose.yml mounts a "data" folder (created next to docker-compose.yml
the first time you run it) into the container, and compliance_data.db (the
SQLite database) lives there — NOT inside the container itself. This
matters: rebuilding the image (`docker compose up -d --build` after
replacing files) or updating Docker never touches that folder, so your
permanent Daily Summary archive survives. Back up the "data" folder the same
way you'd have backed up compliance_data.json before — just copy the whole
folder (it may also contain compliance_data.db-wal / -shm; grab those too,
or stop the container first so SQLite merges them into the main file before
you copy).

UPDATING THE APP LATER (DOCKER)
----------------------------------
Replace ComplianceAutoFilter.html (and/or server.py) in the folder, then:
  docker compose up -d --build
This rebuilds the image with the new file(s) and restarts the container —
your data/ folder (and everyone's saved history) is untouched.

PING FEATURE + DOCKER — READ THIS
-------------------------------------
The Ping buttons in the app ask the server to run a real ICMP ping, which
needs the `ping` command to exist and actually be allowed to send ICMP
packets. The Dockerfile installs `iputils-ping` so the command exists, but
whether it can actually RING OUT to your office LAN depends on Docker
Desktop's network mode:
  - Docker Desktop on Windows with WSL2 (the default, most common setup):
    outbound ping usually works fine, since the container's traffic gets
    NAT'd out through the WSL2 VM the same way normal outbound TCP/UDP does.
  - Occasionally WSL2's networking silently drops ICMP echo replies even
    though TCP/UDP works fine — a known quirk, not something this app can
    control.
After deploying, TEST a Ping button on a device you know is online. If it
always reports "Offline" for devices you know are up, ping isn't getting
through the container's network — the app itself, uploads, filters, Daily
Summary, and syncing are all completely unaffected either way, since none
of that depends on ping. If you hit this, the fallback is running server.py
the normal way (start_server.bat / `python server.py`) directly on Windows
instead of in Docker, since ping always works fine straight on the host OS.

WINDOWS FIREWALL
-----------------
The first time you run it, Windows may ask to allow Python through the
firewall — allow it for "Private networks" so your teammates' PCs can
reach it. If they still can't connect, check that inbound TCP port 8000
(or whichever --port you used) is allowed.

RUNNING ON A DIFFERENT PORT
-----------------------------
  python server.py --port 9000

MOVING THE DATABASE
-----------------------
  python server.py --db "D:\ComplianceData\compliance_data.db"

IMPORTANT — unlike the old JSON file, do NOT put compliance_data.db on a
network share (\\server\share, a mapped network drive, etc.). SQLite needs
real file-locking support from the filesystem to stay safe with concurrent
writes, and network filesystems (SMB/CIFS especially) are well known for not
implementing that reliably — this can silently corrupt the database, not
just fail loudly. Keep compliance_data.db on the LOCAL disk of whichever PC
runs server.py; only the server process touches it directly (everyone else
talks to it over HTTP, which has no such restriction), so there's no
reliability downside to this — and back that file up periodically instead
(a network drive is a fine place for BACKUPS, just not for the live
database).

UPGRADING FROM THE OLD JSON VERSION
---------------------------------------
Nothing to do manually — just replace server.py (and ComplianceAutoFilter.html)
with these new versions and start the server as usual. The first time it
starts, if it finds a compliance_data.json next to where compliance_data.db
would go and the new database is still empty, it automatically imports every
saved day into SQLite, then renames the old file to
compliance_data.migrated.json (never deleted) as a backup. You'll see a
couple of lines about this in the console output confirming what was
migrated. This only ever runs once — once compliance_data.db has data in it,
it's never overwritten by an old JSON file again.

WHAT CHANGED
--------------
- Switched the Asset Organizer's Excel export from SheetJS to ExcelJS.
  Testing showed SheetJS's free version silently ignores bold/fill
  styling on write — the column widths and frozen header worked, but
  headers never actually came out bold no matter what was set. ExcelJS
  is also fully free and open-source (MIT) and actually writes real
  bold headers, a light header background fill, filter dropdowns on the
  header row, auto-fitted column widths, and a frozen top row — matching
  a normal clean asset report layout.
- Added a new "Asset Organizer" page (Tools section in the sidebar). It's
  entirely self-contained and doesn't need the server at all — pure
  client-side cleanup for the Web Asset Monitoring CSV export:
    * Upload the raw export (the ~74-column A-BV version) and it
      automatically keeps only the 32 useful columns (ID through Type of
      Storage), drops the rest (Address/City/State/Country/Zip breakdown,
      Cost, EOL, Warranty, etc.), and shows a clean preview.
    * Download as a plain CSV, or as a properly formatted Excel (.xlsx)
      file with auto-fitted column widths (sized to the longest value in
      each column) and a frozen header row so it stays visible while
      scrolling.
    * If any of the 32 expected columns aren't found in the file you
      uploaded (e.g. your export was customized), it tells you exactly
      which ones were skipped instead of silently guessing.
    * Requires an internet connection the first time the page loads (it
      pulls a small charting library from a CDN for the Excel export) —
      CSV download always works offline either way.
- REBUILT the sync model around a single "current" live report, separate
  from the permanent dated archive:
    * Upload & Run / Results always show ONE live report — whatever was
      most recently uploaded and run. Running a new CSV completely
      REPLACES it; the old one is gone from that view, exactly as
      requested ("what's last to upload is the one will display").
    * Every PC unconditionally mirrors this same single report — on
      page load, every 30 seconds, AND instantly whenever you switch
      back to an already-open tab or refocus the window. There's no
      more date-matching logic that could disagree between PCs — there
      is only one "current" value, so there's nothing to disagree about.
    * Daily Summary's historical archive is completely separate and
      untouched by this churn — it's only written to when you click
      "Save today's run to database". Running a new CSV never deletes
      a day you've already explicitly saved to history.
- FIXED A CRITICAL BUG from the previous build: comparing each report's
  date to the real calendar date and auto-clearing the screen within a
  second if they didn't match. Removed entirely — replaced by the
  single "current" slot above, which doesn't depend on dates at all.
- Uploading a new CSV while data is already loaded asks for confirmation
  first. Cancel keeps your current data untouched; confirming clears the
  local view and loads the new file fresh (which then becomes the new
  "current" once you click Run Filters).
- Sync model rebuilt to match your other in-house tools (e.g. SUS)
  exactly: the WHOLE database is fetched with GET /api/data and saved
  wholesale with POST /api/data, instead of the previous per-date REST
  calls. The browser polls every 30 seconds (same interval SUS uses)
  and pushes any local change (a Complied checkbox, a new filter run)
  about 400ms after it happens, so every PC converges to the same state
  quickly.
- Fixed a startup deadlock in the previous server.py: the JSON-file save
  function tried to re-acquire a lock it was already holding, which could
  cause the server to hang silently on first run. Switched to a
  reentrant lock — this may explain earlier "not syncing at all" reports.
- Added a real device Ping feature. Each row in the Results tables now
  has a "Ping" button (and a "Ping All" button per category) that asks
  the SERVER to run an actual ICMP ping to that device's IP — browsers
  can't send raw ICMP themselves, so this has to happen server-side,
  same as your other tool. Shows ● Online / ● Offline per device.
- Visual redesign: sidebar app shell with three pages (Upload & Run,
  Results, Daily Summary), matching your other in-house IT tools'
  dark/green console aesthetic (Space Mono + DM Sans, decorative stat
  cards, sync-status pill in the top bar).
- Today's Results centralize automatically: as soon as you run filters
  (or check/uncheck a "Complied" box), the results silently sync to the
  shared database. Any other PC — already open or newly opened — picks
  up the same data within moments via the 30-second poll.
- A "Complied" checkbox column was added to the right side of every
  results table (PF/WU/ME/SF). Checking it visually marks that device as
  already addressed (dimmed + strikethrough) and syncs the state to the
  shared database. Also included in CSV exports and Copy-to-Teams.
- Fixed "Copy All" not producing a real table when pasted into Teams:
  the old code used the browser's newer Clipboard API for rich HTML,
  which only works on secure origins (https:// or localhost) — over a
  plain http://<lan-ip>:8000 address it was silently falling back to
  plain tab-separated text. Copy now uses the older DOM-selection copy
  method first, which isn't gated behind that restriction.
- A "Sync" button in the top bar lets you manually force a reconnect +
  refresh instead of waiting for the automatic checks.
- The connection check times out after 5 seconds instead of hanging
  indefinitely. If it's stuck on "Checking..." forever, that's usually a
  firewall silently DROPPING packets rather than rejecting the
  connection outright — the tool will now tell you this explicitly.
- If you ever open ComplianceAutoFilter.html directly as a local file
  (file://) with no server running, a warning banner appears and it
  automatically falls back to browser localStorage — but that copy can
  never sync with anyone else.

TROUBLESHOOTING
-----------------
"Server unreachable" / "Timed out" / red dot in the sidebar
  -> server.py isn't running, or you opened the file directly (file://)
     instead of via http://<server-ip>:8000. If you opened it as a local
     file, you'll see a yellow warning banner at the top of the page
     confirming this — that browser is NOT talking to the shared database.

  -> If it specifically says "Timed out" (not "Unreachable"), that means
     the request never got a response at all within 5 seconds. This
     usually points to a firewall silently dropping the connection
     attempt rather than actively refusing it — check Windows Firewall
     inbound rules on the HOST PC for this port, on Private networks.

Teammates can't reach http://<your-ip>:8000 ("not syncing" from other PCs)
  This is almost always one of these three things:

  1. They double-clicked the HTML file instead of typing the server
     address into their browser. Opening the file directly always uses
     that browser's own local storage — it can never sync, no matter
     what. Everyone must open the same http://<server-ip>:8000 link.

  2. Windows Firewall on the HOST PC (the one running server.py) is
     blocking inbound connections. Allow Python / this app for Private
     networks when prompted, or add an inbound rule for the port you're
     using (default 8000).

  3. The printed IP is wrong. If the host PC is connected to a VPN, has
     Hyper-V/Docker/VirtualBox installed, or has multiple network
     adapters, server.py's startup message lists ALL of that PC's
     network addresses — try each one from another PC's browser to find
     the one that's actually reachable on your office LAN.

  Also confirm all PCs are on the same physical network/VPN, and that
  the host PC's IP hasn't changed (it can change on DHCP lease renewal —
  restarting the server after a PC reboot will show you the current one).

Want to wipe the database and start over
  -> Click "Clear database" in the tool, or just delete compliance_data.db
     (and compliance_data.db-wal / -shm if present) while the server is
     stopped and restart it (a fresh empty one is created automatically).
