# Use an old phone as a camera (RTSP)

An old Android or iOS phone running an **RTSP-server / IP-camera app** can act as a
camera for eeper — no adapter container needed. eeper pulls the phone's RTSP
stream exactly like any other camera, so the phone just has to serve a
stream that meets the RTSP contract.

## What the stream must be

eeper validates every source against the same contract (with `ffprobe`) before it
accepts it:

- **Codec: H.264** — H.265/HEVC is rejected. Set the app's codec to H.264 (a.k.a.
  AVC), not HEVC.
- **Resolution: ≤ 1080p** — the short edge ≤ 1080 and the long edge ≤ 1920 (either
  orientation). 720p is a good default.
- **URL scheme: `rtsp://`** — only `rtsp://` (or `rtsps://`) is accepted.
- **Same LAN** — the phone and the eeper host must be on the same network. Don't
  port-forward the phone to the internet; use WireGuard/Tailscale for remote access.

A keyframe interval of ~1–2 seconds and TCP transport give the snappiest live view.

## Steps

1. Install an RTSP-server / IP-camera app on the phone (search your app store for
   "RTSP server" or "IP camera"). Pick one that can serve **H.264 over RTSP**.
2. In the app, set: codec **H.264**, resolution **1280×720** (or any ≤ 1080p),
   and note the **RTSP URL** it shows — typically
   `rtsp://<user>:<pass>@<phone-lan-ip>:<port>/<path>`.
3. Keep the phone plugged in and on the same Wi‑Fi, screen-lock allowed (most apps
   keep serving in the background).
4. Register the URL with eeper (admin only) via the cameras API — for example:

   ```sh
   curl -X POST https://<your-eeper-host>/api/v1/cameras \
     -H 'content-type: application/json' \
     -b cookies.txt \
     -d '{"name":"hallway phone","source_url":"rtsp://user:pass@192.168.1.42:8554/live"}'
   ```

   (`cookies.txt` is an authenticated admin session — sign in at `/api/v1/auth/login`
   first, or use an API token **minted with the `admin` scope** as
   `Authorization: Bearer <token>`; a default-scope token is rejected with `403`.)
   A `201` means the stream passed the contract and is registered; it then appears
   in the Live view like any camera.

## Troubleshooting (what the API tells you)

| Response                                                       | Fix                                                                   |
| -------------------------------------------------------------- | --------------------------------------------------------------------- |
| `422` "Unsupported video codec '…'. eeper requires H.264 (…)." | Switch the app's codec from H.265/HEVC to **H.264**.                  |
| `422` "Resolution WxH exceeds the 1080p limit."                | Lower the resolution (short edge ≤ 1080, long edge ≤ 1920).           |
| `422` validation error mentioning `source_url` + `rtsp://`     | Use the `rtsp://…` URL, not an `http://` web/snapshot URL.            |
| `502` "Could not read the camera stream: …"                    | Check the phone is on the LAN, the app is serving, and the URL/creds. |
| `409` "This camera source is already registered."              | That URL is already registered — reuse the existing camera.           |
| `403` "…not permitted for admin operations" / admin required   | Register with an admin session or an `admin`-scoped API token.        |

## Notes

- The source URL (including any phone credentials) is stored server-side and is
  **never returned** by the API — the Live view only exposes the relayed stream.
- This is a monitor, not a medical device. eeper shows and records what the camera
  sees; it makes no clinical or alarm claims.
