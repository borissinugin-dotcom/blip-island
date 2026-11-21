import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from borh_island.core.licensing import (
    DEV_EXCLUSIVE_CHARACTER_IDS,
    FREE_CHARACTER_IDS,
    LicenseManager,
    verify_ed25519_license,
)


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


class LicensingTests(unittest.TestCase):
    def _make_mgr(self, tmp: Path) -> LicenseManager:
        proj = tmp / "proj"
        appd = tmp / "appdata"
        proj.mkdir(parents=True, exist_ok=True)
        appd.mkdir(parents=True, exist_ok=True)
        return LicenseManager(project_dir=proj, appdata_dir=appd)

    def test_trial_then_free_restrictions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch("borh_island.core.licensing._reg_read", return_value=""), patch(
                "borh_island.core.licensing._reg_write"
            ):
                mgr = self._make_mgr(Path(td))
                st = mgr.get_status(force_refresh=True)
                self.assertEqual(st.tier, "TRIAL")
                self.assertTrue(st.is_pro)
                self.assertTrue(mgr.is_feature_allowed("glass"))
                self.assertTrue(mgr.is_character_allowed("cat"))
                self.assertFalse(mgr.is_character_allowed("kamaz"))

                # Expire trial (4 days ago)
                expired = {"hwid": mgr.hwid, "started_at": time.time() - 4 * 86400, "last_seen_at": time.time(), "tampered": False}
                mgr._write_dpapi_json(mgr._trial_file, "TrialState", expired)
                st2 = mgr.get_status(force_refresh=True)
                self.assertEqual(st2.tier, "FREE")
                self.assertFalse(st2.is_pro)
                for feat in ("glass", "realtime_weather", "game_stats", "timer"):
                    self.assertFalse(mgr.is_feature_allowed(feat))
                for cid in FREE_CHARACTER_IDS:
                    self.assertTrue(mgr.is_character_allowed(cid))
                self.assertFalse(mgr.is_character_allowed("cat"))
                for dcid in DEV_EXCLUSIVE_CHARACTER_IDS:
                    self.assertFalse(mgr.is_character_allowed(dcid))

    def test_developer_codes_unlock_exact_entitlements(self) -> None:
        cases = [
            ("NOvaBEaSt1488", {"purple_star"}, False),
            ("kamaz19367buLLDOZA", {"kamaz"}, False),
            ("vanyyBARsiK3825492", {"meimei"}, False),
            ("BORhBaRsMEYY673514", {"borscht", "purple_star", "kamaz", "meimei"}, True),
            ("BLIP-FULL-C23EA8D2", {"borscht", "purple_star", "kamaz", "meimei"}, True),
            ("BLIP-FULL-F0739A26", {"borscht", "purple_star", "kamaz", "meimei"}, True),
            ("BLIP-FULL-777A3C47", {"borscht", "purple_star", "kamaz", "meimei"}, True),
            ("BLIP-FULL-C5DBA0B1", {"borscht", "purple_star", "kamaz", "meimei"}, True),
        ]
        for code, expected_dev, expected_all in cases:
            with self.subTest(code=code):
                with tempfile.TemporaryDirectory() as td:
                    with patch("borh_island.core.licensing._reg_read", return_value=""), patch(
                        "borh_island.core.licensing._reg_write"
                    ):
                        mgr = self._make_mgr(Path(td))
                        ok, _ = mgr.activate_code(code)
                        self.assertTrue(ok)
                        st = mgr.get_status(force_refresh=True)
                        self.assertEqual(st.tier, "PRO")
                        self.assertTrue(st.is_permanent_pro)
                        self.assertEqual(st.all_dev_unlocked, expected_all)
                        for dcid in DEV_EXCLUSIVE_CHARACTER_IDS:
                            self.assertEqual(mgr.is_character_allowed(dcid), dcid in expected_dev)

    def test_ed25519_license_verification_and_hwid_binding(self) -> None:
        priv = Ed25519PrivateKey.generate()
        pub_b64u = _b64u_encode(priv.public_key().public_bytes_raw())
        payload = json.dumps({"product": "Blip", "tier": "pro", "hwid": "hw123", "order_id": "BLIP-1"})
        sig = _b64u_encode(priv.sign(payload.encode("utf-8")))
        lic = {"payload": payload, "signature": sig, "alg": "Ed25519"}

        ok, parsed = verify_ed25519_license(lic, expected_hwid="hw123", public_key_b64u=pub_b64u)
        self.assertTrue(ok)
        self.assertEqual(parsed["order_id"], "BLIP-1")

        ok_wrong_hw, _ = verify_ed25519_license(lic, expected_hwid="other_hw", public_key_b64u=pub_b64u)
        self.assertFalse(ok_wrong_hw)

        tampered = {"payload": payload.replace("BLIP-1", "BLIP-2"), "signature": sig, "alg": "Ed25519"}
        ok_tampered, _ = verify_ed25519_license(tampered, expected_hwid="hw123", public_key_b64u=pub_b64u)
        self.assertFalse(ok_tampered)
