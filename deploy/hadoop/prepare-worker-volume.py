#!/usr/bin/env python3
"""Mount the explicitly identified EBS data volume, preserving existing filesystems."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, **kwargs)


def identify(raw, expected_id):
    # Amazon's ebsnvme utility defines serial at byte 4 and vendor bdev at 3072.
    # https://github.com/amazonlinux/amazon-ec2-utils/blob/main/ebsnvme
    if len(raw) != 4096:
        raise ValueError("NVMe controller identification was not 4096 bytes")
    decode = lambda value: value.decode("ascii").strip(" \x00")
    if int.from_bytes(raw[:2], "little") != 0x1D0F:
        raise ValueError("Device is not an Amazon NVMe controller")
    if decode(raw[24:64]) != "Amazon Elastic Block Store":
        raise ValueError("Device is not EBS")
    if decode(raw[4:24]).replace("-", "") != expected_id.replace("-", ""):
        raise ValueError("NVMe volume ID does not match the requested EBS volume")
    mapping = decode(raw[3072:3104]).removeprefix("/dev/")
    if mapping != "sdf":
        raise ValueError(f"Expected the data attachment sdf, found {mapping!r}")


def select_volume(devices, expected_id):
    matches = [
        disk for disk in devices
        if (disk.get("serial") or "").strip().replace("-", "") == expected_id.replace("-", "")
    ]
    if len(matches) != 1:
        raise ValueError("Expected exactly one disk with the supplied EBS volume ID")
    disk = matches[0]
    if disk["type"] != "disk" or not re.fullmatch(r"/dev/nvme\d+n\d+", disk["path"]):
        raise ValueError("The matching volume is not a whole NVMe disk")
    if disk.get("children"):
        raise ValueError("Partitioned volumes require manual review; nothing will be formatted")
    if disk["size"] < 200 * 1024**3:
        raise ValueError("The data volume must be at least 200 GiB")
    mounts = [mount for mount in disk.get("mountpoints", []) if mount]
    if any(mount != "/data" for mount in mounts):
        raise ValueError(f"The volume is mounted outside /data: {mounts}")
    return disk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--volume-id", required=True)
    parser.add_argument("--format-empty", action="store_true",
                        help="Only pass for a newly provisioned empty volume with this exact ID")
    args = parser.parse_args()
    if os.geteuid() != 0 or not re.fullmatch(r"vol-(?:[0-9a-f]{8}|[0-9a-f]{17})", args.volume_id):
        parser.error("Root and a valid EBS volume ID are required")
    inventory = json.loads(run("lsblk", "--json", "--tree", "--bytes", "--output",
                               "PATH,TYPE,SERIAL,SIZE,FSTYPE,UUID,MOUNTPOINTS", text=True).stdout)
    disk = select_volume(inventory["blockdevices"], args.volume_id)
    device = disk["path"]
    identify(run("nvme", "id-ctrl", "--raw-binary", device).stdout, args.volume_id)
    root_source = run("findmnt", "--noheadings", "--output", "SOURCE", "/", text=True).stdout.strip()
    ancestors = run("lsblk", "--inverse", "--list", "--noheadings", "--paths", "--output", "NAME",
                    root_source, text=True).stdout.split()
    if os.path.realpath(device) in [os.path.realpath(path) for path in ancestors]:
        raise ValueError("Refusing to use the root disk")

    target = Path("/data")
    if target.is_symlink():
        raise ValueError("/data must not be a symlink")
    current_mount = subprocess.run(("findmnt", "--noheadings", "--mountpoint", "/data", "--output", "SOURCE"),
                                   capture_output=True, text=True)
    if current_mount.returncode == 0:
        if os.path.realpath(current_mount.stdout.strip()) != os.path.realpath(device):
            raise ValueError("/data is already mounted from a different device")
    elif current_mount.returncode != 1:
        raise ValueError("Unable to inspect the existing /data mount")
    elif target.exists() and any(target.iterdir()):
        raise ValueError("/data contains files on another filesystem; refusing to hide them")

    if not disk.get("fstype"):
        signatures = json.loads(run("wipefs", "--no-act", "--json", device, text=True).stdout)
        if signatures.get("signatures"):
            raise ValueError("Disk has existing signatures; refusing to format")
        if not args.format_empty:
            raise ValueError("Blank volume found; --format-empty is required for initial provisioning")
        if current_mount.returncode == 0:
            raise ValueError("Refusing to format a mounted device")
        print(f"Formatting explicitly approved new EBS data volume {args.volume_id} ({device})", flush=True)
        run("mkfs.ext4", "-L", "cosmos-data", device)
    elif disk["fstype"] != "ext4":
        raise ValueError("An existing non-ext4 filesystem is preserved; manual mount configuration is required")

    uuid = run("blkid", "--output", "value", "--match-tag", "UUID", device, text=True).stdout.strip()
    if not re.fullmatch(r"[0-9a-fA-F-]+", uuid):
        raise ValueError("Missing or invalid filesystem UUID")
    fstab = Path("/etc/fstab")
    entries = [line.split() for line in fstab.read_text().splitlines()
               if line.strip() and not line.lstrip().startswith("#")]
    data_entries = [entry for entry in entries if len(entry) > 1 and entry[1] == "/data"]
    expected_source = f"UUID={uuid}"
    if len(data_entries) > 1 or any(entry[0] != expected_source for entry in data_entries):
        raise ValueError("Existing /data fstab entry points to a different filesystem")
    if any(entry[0] == expected_source and entry[1] != "/data" for entry in entries):
        raise ValueError("This filesystem is already configured at another mountpoint")
    if not data_entries:
        shutil.copy2(fstab, f"/etc/fstab.cosmos-{time.time_ns()}.bak")
        with fstab.open("a") as handle:
            handle.write(f"\n{expected_source} /data ext4 defaults,nofail 0 2\n")
            handle.flush()
            os.fsync(handle.fileno())
    target.mkdir(exist_ok=True)
    run("systemctl", "daemon-reload")
    if current_mount.returncode != 0:
        run("mount", "/data")
    actual_uuid = run("findmnt", "--noheadings", "--mountpoint", "/data", "--output", "UUID", text=True).stdout.strip()
    if actual_uuid != uuid:
        raise ValueError("Mounted filesystem UUID does not match the data volume")
    print(json.dumps({"volume_id": args.volume_id, "device": device, "uuid": uuid, "mountpoint": "/data"}))


if __name__ == "__main__":
    main()
