# Seed Packet Planner

Self-hosted seed catalog and planting planner for phone and PC: photograph the back of a seed packet and have your server read it, look up growing details on seed company sites, keep packet and plant photos and a planting journal, build a planting schedule from your frost dates, and sync everything to a home server over Tailscale.

```
SEED PACKET PLANNER — home server with sync
===========================================

One catalog on your Pop!_OS server, shared by your phone and PC. Each device also keeps a
local copy, so the app works offline and sends its changes when it reconnects.
Only devices signed in to your Tailscale account can reach it.

INSTALL (on the Pop!_OS server, about 2 minutes)
  1. Copy this folder to the server and open a terminal in it.
  2. Run:   sudo ./install.sh
  3. It prints your address, e.g.  https://pop-os.tail1234.ts.net:8443
     If it asks you to enable HTTPS, turn on HTTPS Certificates under DNS in the
     Tailscale admin console, then run the script again.

ON YOUR PHONE AND PC
  - Make sure the Tailscale app is on and signed in.
  - Open the address. On the phone, add it to the home screen
    (iPhone: Share > Add to Home Screen. Android: menu > Add to Home screen).
  - The top-right corner shows "Synced", or "Offline · changes will sync".
  - Already used the earlier version? On that device, tap Garden > Save backup in the old
    copy, then Restore backup in the new one. Restored seeds upload to the server.

SEED COMPANY LOOKUPS (no Claude key needed)
  When you add a seed (by packet photo or by name), the server looks it up on Baker Creek (rareseeds.com),
  Eden Brothers, Burpee, Johnny's Selected Seeds and Victory Seeds, and fills in days to
  maturity, days to sprout, depth, spacing, sun and timing from the product page.
  - Pick the company on the Add tab for a faster, more accurate match.
  - "Not the right one?" on the review screen lists other matches.
  - Anything a page doesn't list falls back to typical values for that plant.
  - Pages are cached for 30 days, with a short pause between requests, and each site's
    robots.txt rules are respected (Johnny's is matched from its published sitemap).
  Test from the server's terminal:
    cd /opt/seed-planner && python3 server.py --lookup "cherokee purple tomato"
    python3 server.py --lookup "genovese basil" eden
    python3 server.py --lookup https://www.rareseeds.com/products/tomato-cherokee-purple
  (Run these as root or with SEED_DB=/tmp/test.db in front, so they can write their cache.)

READ PACKET PHOTOS ON YOUR SERVER (no cloud)
  Every packet photo is read on the server itself, with no cloud service.
  - Add tab > Photograph the back. The back of the packet has
    days to maturity, depth, spacing, timing and planting instructions. The app reads it, then searches your seed
    companies with the name it found to fill anything still missing.
  - In the editor, "Read details from packet photos" re-reads the front and back photos.
    Packet values fill empty fields; timing printed on the packet replaces typical timing.
  Engines:
  - Tesseract text recognition: installed by install.sh. Any CPU, 1-3 seconds a photo.
    Best on flat, well-lit packet backs.
  - Local vision model (optional, better on decorative fronts and glossy packets):
      curl -fsSL https://ollama.com/install.sh | sh
      ollama pull qwen2.5vl:7b            # about 6 GB; suits an 8 GB graphics card
      sudo nano /etc/seed-planner.env      # set VISION_MODEL=qwen2.5vl:7b
      sudo systemctl restart seed-planner
    Without a graphics card it still works, but takes a minute or more per packet.
    If the model is down, the server falls back to Tesseract automatically.
  Status: Garden tab shows which engine is active.
  Test from the server:  python3 /opt/seed-planner/server.py --read-packet back.jpg [front.jpg]

LET CLAUDE READ PACKET PHOTOS (optional, for every device at once)
  sudo nano /etc/seed-planner.env      put your key after ANTHROPIC_API_KEY=
  sudo systemctl restart seed-planner
  The key stays on the server; phones and browsers never see it.

EVERYDAY COMMANDS
  Status / logs:   systemctl status seed-planner      journalctl -u seed-planner -n 50
  Update the app:  copy the new folder over, then  sudo ./install.sh  again (data is kept)
  Back up data:    include /var/lib/seed-planner in your server backups. It holds
                   seeds.db (catalog and journal) and photos/ (one .jpg per photo).
  Stop sharing:    sudo tailscale serve --https=8443 off

SEED PAGES, PHOTOS AND JOURNAL
  Each seed has Planting instructions (filled from the packet or the seed company's
  growing guide) and Plant notes (your own notes).
  Tap a seed in My seeds to open its page: a photo gallery at the top (front and back of the
  packet, then plant photos; swipe or tap the thumbnails, + tiles to add more), growing details,
  this season's schedule, planting instructions, plant notes, and a planting journal whose
  entries each have their own date, grouped by year.
  Edit (top right) changes details and replaces the packet photos.
  Photos are resized to about 1400 px and stored as files on the server. Each device also
  keeps its own copies, so photos you take offline upload when it reconnects.
  Garden > Save backup covers seeds and journal text only, not photos.

HOW SYNC WORKS
  Every seed and your garden settings carry a "last changed" time. When two devices change
  the same seed, the most recent change wins. Deleting a seed on one device removes it
  everywhere. Devices refresh when the app opens, when you switch tabs, and every minute
  while it's open.

USE A DIFFERENT PORT
  The app uses HTTPS port 8443 so it won't disturb anything already served on your main
  Tailscale address (such as Nextcloud). To use the main address instead:
    sudo HTTPS_PORT=443 ./install.sh

FILES
  server.py              the sync server (Python standard library only, no pip installs)
  seedsites.py           seed company lookups
  packetreader.py        packet photo reading (Tesseract / Ollama vision model)
  tests/                 python3 -m unittest discover -s tests
  public/                the web app
  seed-planner.service   systemd service (runs as a locked-down "seedplanner" user)
  install.sh             installer / updater

```
