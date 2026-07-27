# External storage for recordings

By default eeper records to its built-in media volume — on a Raspberry Pi that's the SD
card. You can point recording at an external disk instead, and switch between them from
**Settings → Recording → Where clips are saved** without touching the host again.

## Why you have to prepare the disk yourself

eeper never mounts, formats, or even enumerates disks. The `api` and `recorder` containers
run non-root with a read-only rootfs and `cap_drop: ALL`, and they get no `/dev`, no
`/sys/block`, and no Docker socket. Mounting a filesystem needs `CAP_SYS_ADMIN`; giving a
network-facing service that capability is functionally giving it root on the host, which
would undo the entire hardening posture for a convenience feature.

So the split is: **you** prepare and mount storage on the host and declare which paths eeper
may use; **eeper** lists those, shows you how much space each has, and lets an admin pick
one. The stored setting is an id like `ssd` — it is matched against your declared list and
never used as a path, so it can't be edited into pointing somewhere else.

## 1. Prepare the disk (on the host, once)

Format and mount it wherever you like; `/storage/ssd` is used throughout these examples.
Mount it via `/etc/fstab` with **`nofail`** so a missing disk can never block boot:

```bash
sudo mkdir -p /storage/ssd
echo 'UUID=<your-uuid> /storage/ssd ext4 defaults,nofail,noatime 0 2' | sudo tee -a /etc/fstab
sudo mount /storage/ssd
```

Then give it to eeper's container user. The api and recorder both run as uid/gid `10001`:

```bash
sudo chown 10001:10001 /storage/ssd
```

Skipping the `chown` is the most common failure. The directory stats fine, so nothing looks
wrong until the recorder tries to create its segment directory and gets `EACCES`. Settings
catches this: the target lists as _"Connected, but eeper cannot write to it"_ and can't be
selected.

> **Use a real filesystem, not exFAT/FAT.** Clip promotion hard-links segments, and the
> recorder relies on POSIX ownership. ext4 is the safe default.

## 2. Declare it to eeper

Copy the blocks from [`storage.example.yml`](storage.example.yml) into your deployment-local
`docker-compose.override.yml` — the same file that holds your camera and audio adapters —
adjusting the path and label. Two rules:

- **The same value on `api` and `recorder`.** The recorder writes segments; the api promotes
  clips out of them with `os.link`. A hard link can't cross filesystems, so if the two
  resolve the selected target differently, clip building fails with `EXDEV`.
- **`propagation: rslave` on the bind mount.** Without it the container keeps the mount view
  it had at startup. Unplug and remount the disk on the host and the container quietly keeps
  writing to the underlying directory on the SD card — full speed, no errors, wrong disk.

The format is `id:Label:/path`, comma-separated for more than one:

```yaml
EEPER_STORAGE_TARGETS: 'ssd:External SSD:/storage/ssd,usb:USB stick:/storage/usb'
```

`id` is lowercase `[a-z0-9_-]`, up to 32 characters — it's what gets stored in the database.
`Label` is what the app shows and may contain spaces (and colons). A malformed entry is
skipped with a warning in the api log rather than failing startup, so a typo here can never
take the monitor down.

Then bring the stack back up:

```bash
sudo ./eeper-up.sh
```

## 3. Pick it in the app

**Settings → Recording** now shows a **Where clips are saved** list with free space per
disk. Choosing one takes effect within a few seconds — the recorder restarts its `ffmpeg`
children against the new path on its next reconcile tick. No container is started or stopped
and nothing is remounted.

The picker is hidden entirely when only one target exists, so an untouched deployment sees
no change.

## What moves and what doesn't

- **New** recording segments and newly promoted clips go to the disk you pick.
- **Existing** clips stay where they were written and still play back — clip rows store an
  absolute path, so nothing breaks.
- **Existing segments** on the old disk stay there and age out normally: retention sweeps
  every declared target, not just the selected one, so a deselected disk still respects the
  byte quota and age bound instead of filling up forever.
- Segments and clips always share one root. They can't be split across disks — promotion
  hard-links segments into the clips directory, and that only works within one filesystem.

If you want to move the old recordings too, do it on the host with the stack down (`rsync
-a /var/lib/docker/volumes/eeper_media-data/_data/ /storage/ssd/`). Nothing in the app
depends on it.

## Troubleshooting

| What Settings shows                        | What it means                                                                                                                         |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| **Not connected**                          | The path doesn't exist in the container — the bind mount is missing, or the host isn't mounted.                                       |
| **Connected, but eeper cannot write**      | Almost always a missing `chown 10001:10001` on the host directory. Can also be a full or read-only disk.                              |
| **… free — warning: no disk mounted here** | The bind mount exists but nothing is mounted at it on the host. Writes would land on the SD card. Check `mount \| grep /storage/ssd`. |
| The picker isn't there at all              | `EEPER_STORAGE_TARGETS` is unset or every entry was malformed. Check `docker compose logs api \| grep 'storage target'`.              |

Lite mode (see [LITE.md](LITE.md)) runs no recorder, so none of this applies there.
