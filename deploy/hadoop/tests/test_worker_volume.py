"""Safety checks use synthetic NVMe metadata; never access disks or mount filesystems."""

import importlib.util
from pathlib import Path
import unittest


path = Path(__file__).parents[1] / "prepare-worker-volume.py"
spec = importlib.util.spec_from_file_location("worker_volume", path)
volume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(volume)
VOLUME_ID = "vol-01234567890abcdef"


def controller(serial=VOLUME_ID.replace("-", ""), mapping="/dev/sdf", model="Amazon Elastic Block Store"):
    data = bytearray(4096)
    data[:2] = (0x1D0F).to_bytes(2, "little")
    data[4:24] = serial.encode().ljust(20, b" ")
    data[24:64] = model.encode().ljust(40, b" ")
    data[3072:3104] = mapping.encode().ljust(32, b" ")
    return bytes(data)


def disk(**changes):
    value = {"path": "/dev/nvme1n1", "type": "disk", "serial": VOLUME_ID.replace("-", ""),
             "size": 200 * 1024**3, "mountpoints": [None], "fstype": None}
    value.update(changes)
    return value


class VolumeIdentityTests(unittest.TestCase):
    def test_matches_volume_and_both_sdf_vendor_forms(self):
        volume.identify(controller(), VOLUME_ID)
        volume.identify(controller(mapping="sdf"), VOLUME_ID)

    def test_rejects_wrong_volume_even_when_device_mapping_matches(self):
        with self.assertRaisesRegex(ValueError, "volume ID"):
            volume.identify(controller(serial="volfedcba09876543210"), VOLUME_ID)

    def test_rejects_root_attachment_mapping(self):
        with self.assertRaisesRegex(ValueError, "attachment sdf"):
            volume.identify(controller(mapping="sda1"), VOLUME_ID)

    def test_rejects_instance_store_and_truncated_controller_data(self):
        with self.assertRaisesRegex(ValueError, "not EBS"):
            volume.identify(controller(model="Amazon EC2 NVMe Instance Storage"), VOLUME_ID)
        with self.assertRaisesRegex(ValueError, "4096"):
            volume.identify(controller()[:512], VOLUME_ID)

    def test_rejects_other_mounted_data_and_partitions(self):
        with self.assertRaisesRegex(ValueError, "mounted outside"):
            volume.select_volume([disk(mountpoints=["/"])], VOLUME_ID)
        with self.assertRaisesRegex(ValueError, "Partitioned"):
            volume.select_volume([disk(children=[{"path": "/dev/nvme1n1p1"}])], VOLUME_ID)

    def test_existing_data_filesystem_is_selected_without_modification(self):
        existing = disk(fstype="ext4", mountpoints=["/data"], uuid="existing-filesystem")
        self.assertIs(volume.select_volume([existing], VOLUME_ID), existing)
        self.assertEqual(existing["uuid"], "existing-filesystem")
        self.assertEqual(existing["fstype"], "ext4")

    def test_rejects_missing_ambiguous_or_small_device(self):
        for disks in ([], [disk(), disk()], [disk(size=30 * 1024**3)]):
            with self.subTest(disks=disks), self.assertRaises(ValueError):
                volume.select_volume(disks, VOLUME_ID)


if __name__ == "__main__":
    unittest.main()
