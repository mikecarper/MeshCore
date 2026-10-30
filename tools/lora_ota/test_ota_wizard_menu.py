"""Exercise the normal menu journeys without radios, compilers, or downloads."""
import asyncio
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import lora_ota as ota
import ota_wizard as wizard
import ota_wizard_bundle as bundles
import ota_wizard_packages as packages
from ota_wizard_catalog import ReleaseChoice
from test_ota_wizard_bundle import KEY, destination, image, signed_package


class WizardMenuTests(unittest.TestCase):
    def setUp(self):
        self.files = tempfile.TemporaryDirectory(prefix="meshcore-wizard-menu-")
        self.addCleanup(self.files.cleanup)
        self.cache = Path(self.files.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(wizard, "cache_directory", return_value=self.cache))
        self.stack.enter_context(mock.patch.object(wizard.sys.stdin, "isatty", return_value=True))
        self.output = io.StringIO()
        self.stack.enter_context(redirect_stdout(self.output))
        self.stack.enter_context(redirect_stderr(self.output))

    def answers(self, values):
        return mock.patch("builtins.input", side_effect=values)

    def test_plain_launch_can_exit_without_usb_or_dependency_setup(self):
        with self.answers(["6"]), \
                mock.patch.object(wizard, "ensure_dependencies", side_effect=AssertionError("USB setup")), \
                mock.patch.object(wizard, "run", side_effect=AssertionError("radio access")):
            self.assertEqual(wizard.main([]), 0)
        self.assertIn("Update a nearby radio", self.output.getvalue())
        self.assertIn("Prepare an update bundle on this computer", self.output.getvalue())

    def test_update_and_check_need_only_menu_numbers(self):
        for action, menu_number in (("update", "1"), ("check", "2")):
            with self.subTest(action=action), self.answers([menu_number, "6"]), \
                    mock.patch.object(wizard, "ensure_dependencies") as setup, \
                    mock.patch.object(wizard, "run", new_callable=mock.AsyncMock, return_value=0) as run:
                self.assertEqual(wizard.main([]), 0)
            run.assert_awaited_once()
            options = run.await_args.args[0]
            self.assertEqual(options.check, action == "check")
            self.assertFalse(options.recover)
            self.assertTrue(options.interactive_menu)
            self.assertIsNone(options.port)
            self.assertIsNone(options.target)
            self.assertEqual(setup.call_args.args[1],
                             ["--menu-action", action, "--cache", str(self.cache)])

    def test_runtime_resume_runs_selected_action_before_showing_main_menu(self):
        with self.answers(["6"]) as input_call, \
                mock.patch.object(wizard, "ensure_dependencies"), \
                mock.patch.object(wizard, "run", new_callable=mock.AsyncMock, return_value=0) as run:
            self.assertEqual(wizard.main(["--runtime-ready", "--menu-action", "check"]), 0)
        self.assertTrue(run.await_args.args[0].check)
        self.assertEqual(input_call.call_count, 1)  # Only the post-check Exit choice.

    def test_failed_update_returns_to_menu_without_a_traceback(self):
        with self.answers(["1", "6"]), mock.patch.object(wizard, "ensure_dependencies"), \
                mock.patch.object(wizard, "run", new_callable=mock.AsyncMock,
                                  side_effect=ota.OtaError("Connect a source radio")):
            self.assertEqual(wizard.main([]), 0)
        self.assertIn("Update stopped: Connect a source radio", self.output.getvalue())
        self.assertNotIn("Traceback", self.output.getvalue())

    def test_bad_menu_numbers_retry_and_back_cancels_before_usb_setup(self):
        with self.answers(["nonsense", "0", "3", "", "6"]), \
                mock.patch.object(wizard, "ensure_dependencies", side_effect=AssertionError("USB setup")):
            self.assertEqual(wizard.main([]), 0)
        self.assertEqual(self.output.getvalue().count("Enter one of the numbers above."), 2)

    def test_settings_round_trip_without_saving_environment_credentials(self):
        source, download_cache = self.cache / "custom source", self.cache / "downloads elsewhere"
        values = ["1", "2", "2", "2", "3", "2", "4", "COM12", "5", "/tools/motatool",
                  "6", str(source), "7", "v1.18.0.4-custom", "8", str(download_cache),
                  "9", "2", "10", "2", "11", "910.25,62.5,7,5", "15"]
        options = wizard.arguments([])
        with self.answers(values), mock.patch.dict(os.environ, {"MESHCORE_ADMIN_PASSWORD": "never save me"}):
            wizard.settings_menu(options)
        stored = json.loads(wizard.menu_settings_path().read_text())
        self.assertNotIn("never save me", json.dumps(stored))
        self.assertEqual(set(stored), {"full", "no_bootloader", "mode", "port", "motatool",
                                      "source_tree", "version", "cache", "auth", "source_radio", "transfer_radio",
                                      "link", "ota_hops", "transfer_preamble", "relay_keys"})
        restored = wizard.arguments([])
        wizard.load_menu_settings(restored)
        self.assertTrue(restored.full)
        self.assertTrue(restored.no_bootloader)
        self.assertEqual((restored.mode, restored.port), ("repeater", "COM12"))
        self.assertEqual(restored.motatool, "/tools/motatool")
        self.assertEqual(restored.source_tree, source)
        self.assertEqual(restored.cache, download_cache)
        self.assertEqual(restored.version, "v1.18.0.4-custom")
        self.assertEqual(restored.auth, "acl")
        self.assertEqual(restored.source_radio, "radio2")
        self.assertEqual(restored.transfer_radio, (910.25, 62.5, 7, 5))

    def test_blank_settings_can_restore_automatic_port_builder_and_version(self):
        options = wizard.arguments([])
        options.port, options.motatool, options.version = "COM12", "/tools/motatool", "v1.18.0.4-custom"
        with self.answers(["4", "", "5", "", "7", "", "15"]):
            wizard.settings_menu(options)
        self.assertIsNone(options.port)
        self.assertIsNone(options.motatool)
        self.assertIsNone(options.version)

    def test_known_key_menu_can_reuse_enter_discover_and_back(self):
        options = wizard.arguments([])
        options.interactive_menu = True
        with self.answers(["1"]):
            self.assertEqual(wizard.choose_known_target(options, 'repeater', {'target': KEY}), KEY)
        with self.answers(["1", KEY.upper()]):
            self.assertEqual(wizard.choose_known_target(options, 'repeater', {}), KEY)
        with self.answers(["2"]):
            self.assertIsNone(wizard.choose_known_target(options, 'repeater', {}))
        with self.answers(["3"]), self.assertRaises(wizard.MenuCancelled):
            wizard.choose_known_target(options, 'repeater', {})

    def test_runtime_bootstrap_preserves_connection_choices(self):
        options = wizard.arguments(['--auth', 'acl', '--source-radio', 'radio2',
                                    '--transfer-radio', '910.25,62.5,7,5', '--target', KEY,
                                    '--target-type', 'repeater', '--mode', 'repeater', '--port', 'COM12',
                                    '--link', 'lora-relay', '--relay', '33'*32, '--relay', '44'*32, '--ota-hops', '2', '--transfer-preamble', '160'])
        restored = wizard.arguments(wizard.menu_runtime_arguments(options, 'check'))
        for field in ('auth', 'source_radio', 'transfer_radio', 'target', 'target_type', 'mode', 'port',
                      'link', 'ota_hops', 'transfer_preamble', 'relay'):
            self.assertEqual(getattr(restored, field), getattr(options, field))
        self.assertEqual(restored.menu_action, 'check')

    def test_invalid_transfer_settings_are_not_saved_or_loaded(self):
        for value in ('nan,62.5,7,5', '910.25,inf,7,5', '910.25,63,7,5', '910.25,62.5,7,99'):
            with self.subTest(value=value):
                options = wizard.arguments([])
                with self.answers(['11', value, '15']):
                    wizard.settings_menu(options)
                self.assertIsNone(options.transfer_radio)
                wizard.menu_settings_path().write_text(json.dumps({'transfer_radio': value}))
                wizard.load_menu_settings(options)
                self.assertIsNone(options.transfer_radio)

    def test_relay_link_menu_selects_compatible_defaults_and_remembers_hops(self):
        options = wizard.arguments(['--mode', 'companion', '--auth', 'password', '--source-radio', 'radio2'])
        with self.answers(['12', '2', '14', '33'*32+','+'44'*32, '15']):
            wizard.settings_menu(options)
        self.assertEqual((options.link, options.mode, options.auth, options.source_radio),
                         ('lora-relay', 'repeater', 'acl', 'radio'))
        restored = wizard.arguments([])
        wizard.load_menu_settings(restored)
        self.assertEqual(restored.link, 'lora-relay')
        self.assertEqual(restored.relay, ['33'*32, '44'*32])
        self.assertNotIn('ESP-NOW', self.output.getvalue())
        self.assertEqual(restored.ota_hops, 2)

    def test_relay_key_menu_rejects_invalid_route_without_replacing_previous_keys(self):
        for value in ('short', '33'*32+','+'33'*32, ','.join(f'{i:064x}' for i in range(9))):
            with self.subTest(value=value):
                options = wizard.arguments(['--relay', '44'*32])
                with self.answers(['14', value, '15']):
                    wizard.settings_menu(options)
                self.assertEqual(options.relay, ['44'*32])

    def test_lora_relay_destination_menu_requires_full_key_without_discovery(self):
        options = wizard.arguments(['--link', 'lora-relay'])
        options.interactive_menu = True
        with self.answers(['1', KEY]):
            self.assertEqual(wizard.choose_known_target(options, 'repeater', {}), KEY)
        self.assertNotIn('Discover', self.output.getvalue())
        with self.answers(['2']), self.assertRaises(wizard.MenuCancelled):
            wizard.choose_known_target(options, 'repeater', {})

    def test_old_lab_link_is_not_loaded_as_a_normal_menu_preference(self):
        wizard.menu_settings_path().write_text(json.dumps({'link':'espnow-relay', 'relay_keys':['bad']}))
        options = wizard.arguments([])
        wizard.load_menu_settings(options)
        self.assertEqual(options.link, 'direct')
        self.assertEqual(options.relay, [])

    def test_menu_prepares_real_full_bundle_from_github_and_cached_request(self):
        request = self.cache / "requests" / (KEY + ".json")
        bundles.save_request(request, destination())
        firmware = self.cache / "published.bin"
        firmware.write_bytes(image().image)
        asset = SimpleNamespace(release_tag="v1.18.0.2", name="ikoka-repeater-v1.18.0.2.zip")
        catalog = SimpleNamespace(choose_asset=mock.Mock(return_value=asset))
        with self.answers(["3", "1", "1", "", "6"]), \
                mock.patch.object(wizard, "GitHubCatalog", return_value=catalog), \
                mock.patch.object(wizard, "download_asset", return_value=firmware) as download, \
                mock.patch.object(wizard, "small_host", return_value=True), \
                mock.patch.object(wizard, "find_builder", return_value=None), \
                mock.patch.object(wizard, "ensure_dependencies", side_effect=AssertionError("USB setup")), \
                mock.patch.object(wizard, "choose_port", side_effect=AssertionError("USB access")), \
                mock.patch.object(wizard, "install_builder", side_effect=AssertionError("compiler setup")):
            self.assertEqual(wizard.main([]), 0)
        catalog.choose_asset.assert_called_once_with(target_id=destination().target_id)
        download.assert_called_once_with(asset, self.cache / "downloads")
        outputs = list((self.cache / "bundles").glob("*.ota.zip"))
        self.assertEqual(len(outputs), 1)
        prepared_image, package = bundles.load_bundle(outputs[0], destination())
        self.assertEqual(prepared_image.image, image().image)
        self.assertTrue(package.info.is_full)
        self.assertEqual(package.body_hash, image().body_hash)
        self.assertIn("Local firmware file or prepared bundle", self.output.getvalue())

    def test_recovery_menu_lists_only_unfinished_records_and_supplies_full_key(self):
        folder = self.cache / "sessions"
        folder.mkdir()
        (folder / (KEY + ".json")).write_text(json.dumps(
            {"format": 1, "target_key": KEY, "phase": "needs cleanup"}))
        (folder / ("22" * 32 + ".json")).write_text(json.dumps(
            {"format": 1, "target_key": "22" * 32, "phase": "finished"}))
        (folder / "invalid.json").write_text("not json")
        with self.answers(["4", "1", "6"]) as input_call, \
                mock.patch.object(wizard, "ensure_dependencies"), \
                mock.patch.object(wizard, "run", new_callable=mock.AsyncMock, return_value=0) as run:
            self.assertEqual(wizard.main([]), 0)
        self.assertEqual(input_call.call_count, 3)
        self.assertTrue(run.await_args.args[0].recover)
        self.assertEqual(run.await_args.args[0].target, KEY)
        self.assertIn(KEY[:12].upper(), self.output.getvalue())
        self.assertNotIn(KEY.upper(), self.output.getvalue())
        self.assertNotIn("finished", self.output.getvalue())

    def test_release_browser_can_load_older_pages_and_marks_previews(self):
        preview_asset, stable_asset = object(), object()
        preview = ReleaseChoice("v1.18.0.3-alpha1", preview_asset, True, "2026-10-26")
        stable = ReleaseChoice("v1.18.0.2", stable_asset, False, "2026-10-25")
        catalog = SimpleNamespace(has_more_releases=True, release_choices=mock.Mock(
            side_effect=[[preview], [stable, preview]]))
        with self.answers(["2", "1"]):
            chosen = asyncio.run(wizard.choose_release(catalog, destination()))
        self.assertIs(chosen, stable_asset)
        self.assertEqual([call.kwargs["max_pages"] for call in catalog.release_choices.call_args_list], [3, 6])
        self.assertTrue(all(call.kwargs["include_prerelease"] for call in catalog.release_choices.call_args_list))
        self.assertIn("v1.18.0.3-alpha1 (prerelease)", self.output.getvalue())

    def test_firmware_back_does_not_download_or_open_a_file(self):
        with self.answers(["5"]), mock.patch.object(wizard, "small_host", return_value=False), \
                mock.patch.object(wizard, "download_asset", side_effect=AssertionError("download")), \
                mock.patch.object(wizard, "file_prompt", side_effect=AssertionError("file")), \
                self.assertRaises(wizard.MenuCancelled):
            asyncio.run(wizard.choose_firmware(wizard.arguments([]), destination(), object(), None, {}))

    def test_remembered_file_has_an_explicit_back_choice(self):
        previous = self.cache / "previous.bin"
        previous.write_bytes(image().image)
        with self.answers(["3"]) as input_call, self.assertRaises(wizard.MenuCancelled):
            wizard.file_prompt("Firmware file", str(previous))
        self.assertEqual(input_call.call_count, 1)
        self.assertIn("Choose another file", self.output.getvalue())
        self.assertIn("Back", self.output.getvalue())

    def test_missing_remembered_file_can_cancel_instead_of_retrying_stale_default(self):
        missing = self.cache / "removed.bin"
        with self.answers([""]) as input_call, self.assertRaises(wizard.MenuCancelled):
            wizard.file_prompt("Firmware file", str(missing))
        self.assertEqual(input_call.call_count, 1)
        self.assertNotIn("Use " + str(missing), self.output.getvalue())

    def test_previous_boards_local_file_does_not_change_new_board_default(self):
        previous = self.cache / "other-board.bin"
        previous.write_bytes(b"other board")
        published = self.cache / "matching.bin"
        asset = SimpleNamespace(release_tag="v1.18.0.2", name="matching-board.zip")
        remembered = {"target": "22" * 32, "firmware_source": "local", "firmware_file": str(previous)}
        with self.answers([""]), mock.patch.object(wizard, "small_host", return_value=True), \
                mock.patch.object(wizard, "download_asset", return_value=published) as download, \
                mock.patch.object(wizard, "file_prompt", side_effect=AssertionError("other board's file")):
            selected, source = asyncio.run(wizard.choose_firmware(
                wizard.arguments([]), destination(), object(), asset, remembered))
        self.assertEqual((selected, source), (published, "latest"))
        download.assert_called_once_with(asset, self.cache / "downloads")

    def test_local_firmware_selection_does_not_query_github(self):
        local = self.cache / "local.bin"
        local.write_bytes(image().image)
        catalog = SimpleNamespace(choose_asset=mock.Mock(side_effect=AssertionError("GitHub query")),
                                  release_choices=mock.Mock(side_effect=AssertionError("GitHub query")))
        with self.answers(["3", str(local)]), mock.patch.object(wizard, "small_host", return_value=True), \
                mock.patch.object(wizard, "latest_asset", new_callable=mock.AsyncMock,
                                  side_effect=AssertionError("GitHub query")), \
                mock.patch.object(wizard, "download_asset", side_effect=AssertionError("GitHub download")):
            selected, source = asyncio.run(wizard.choose_firmware(
                wizard.arguments([]), destination(), catalog, None, {}))
        self.assertEqual((selected, source), (local, "local"))
        catalog.choose_asset.assert_not_called()
        catalog.release_choices.assert_not_called()

    def test_release_back_returns_to_firmware_menu_then_accepts_local_file(self):
        local = self.cache / "local.bin"
        local.write_bytes(image().image)
        preview = ReleaseChoice("v1.18.0.3-alpha1", object(), True, "2026-10-26")
        catalog = SimpleNamespace(has_more_releases=False,
                                  release_choices=mock.Mock(return_value=[preview]))
        with self.answers(["2", "2", "3", str(local)]), \
                mock.patch.object(wizard, "small_host", return_value=True), \
                mock.patch.object(wizard, "latest_asset", new_callable=mock.AsyncMock,
                                  side_effect=AssertionError("latest release query")), \
                mock.patch.object(wizard, "download_asset", side_effect=AssertionError("preview download")):
            selected, source = asyncio.run(wizard.choose_firmware(
                wizard.arguments([]), destination(), catalog, None, {}))
        self.assertEqual((selected, source), (local, "local"))
        catalog.release_choices.assert_called_once_with(
            target_id=destination().target_id, include_prerelease=True, max_pages=3)
        self.assertIn("v1.18.0.3-alpha1 (prerelease)", self.output.getvalue())

    def test_signed_preparation_installs_verifier_then_retries_the_same_file(self):
        options = wizard.arguments([])
        chosen = self.cache / "signed-full.mota"
        new_image, package = image(), signed_package()
        chosen.write_bytes(package.blob)
        catalog = object()
        with mock.patch.object(wizard, "choose_request", return_value=destination()), \
                mock.patch.object(wizard, "GitHubCatalog", return_value=catalog), \
                mock.patch.object(wizard, "latest_asset", new_callable=mock.AsyncMock, return_value=None), \
                mock.patch.object(wizard, "choose_firmware", new_callable=mock.AsyncMock,
                                  return_value=(chosen, "local")) as choose, \
                mock.patch.object(wizard, "prepare_selected_firmware", new_callable=mock.AsyncMock,
                                  side_effect=[ImportError("signature verifier missing"),
                                               (new_image, package)]) as prepare, \
                mock.patch.object(wizard, "ensure_signature_support") as verifier, \
                mock.patch.object(wizard, "ensure_dependencies", side_effect=AssertionError("USB setup")), \
                mock.patch.object(wizard, "choose_port", side_effect=AssertionError("USB access")), \
                mock.patch.object(wizard, "ask", side_effect=lambda prompt, default=None: default):
            self.assertEqual(asyncio.run(wizard.prepare_interactively(options)), 0)
        choose.assert_awaited_once()
        verifier.assert_called_once_with()
        self.assertEqual(prepare.await_args_list, [mock.call(options, destination(), chosen, catalog)] * 2)
        outputs = list((self.cache / "bundles").glob("*.ota.zip"))
        self.assertEqual(len(outputs), 1)
        _, prepared = bundles.load_bundle(outputs[0], destination())
        self.assertEqual(prepared.blob, package.blob)
        self.assertEqual(prepared.signer, package.signer)

    def test_failed_signature_setup_does_not_reselect_or_write_a_bundle(self):
        options = wizard.arguments([])
        chosen = self.cache / "signed-full.mota"
        with mock.patch.object(wizard, "choose_request", return_value=destination()), \
                mock.patch.object(wizard, "GitHubCatalog", return_value=object()), \
                mock.patch.object(wizard, "latest_asset", new_callable=mock.AsyncMock, return_value=None), \
                mock.patch.object(wizard, "choose_firmware", new_callable=mock.AsyncMock,
                                  return_value=(chosen, "local")) as choose, \
                mock.patch.object(wizard, "prepare_selected_firmware", new_callable=mock.AsyncMock,
                                  side_effect=ImportError("signature verifier missing")) as prepare, \
                mock.patch.object(wizard, "ensure_signature_support",
                                  side_effect=ota.OtaError("Verifier download failed")) as verifier, \
                mock.patch.object(wizard, "ensure_dependencies", side_effect=AssertionError("USB setup")), \
                mock.patch.object(wizard, "save_bundle", side_effect=AssertionError("partial bundle")), \
                self.assertRaisesRegex(ota.OtaError, "Verifier download failed"):
            asyncio.run(wizard.prepare_interactively(options))
        choose.assert_awaited_once()
        prepare.assert_awaited_once()
        verifier.assert_called_once_with()
        self.assertFalse((self.cache / "bundles").exists())

    def test_local_base_fallback_rejects_wrong_image_then_accepts_exact_running_image(self):
        wrong, correct = self.cache / "wrong.bin", self.cache / "running.bin"
        wrong.write_bytes(image().image)
        expected = image(0x01120001, body=b"original application" * 16)
        correct.write_bytes(expected.image)
        with self.answers(["2", "2"]), \
                mock.patch.object(wizard, "file_prompt", side_effect=[wrong, correct]), \
                mock.patch.object(wizard, "GitHubCatalog", side_effect=AssertionError("network")):
            base = asyncio.run(wizard.choose_missing_base(wizard.arguments([]), destination(), object()))
        self.assertIsNotNone(base)
        self.assertEqual(ota.parse_endf(base.read_bytes()).body_hash, destination().base_hash)
        self.assertIn("not the exact running firmware", self.output.getvalue())

    def test_older_github_base_search_checks_past_four_same_version_candidates(self):
        candidates, paths = [object() for _ in range(5)], []
        for index in range(4):
            path = self.cache / f"other-build-{index}.bin"
            path.write_bytes(image(0x01120001, body=bytes([index])*64).image)
            paths.append(path)
        exact = self.cache / "running.bin"
        exact.write_bytes(image(0x01120001, body=b"original application"*16).image)
        paths.append(exact)
        catalog = SimpleNamespace(has_more_releases=True, release_choices=mock.Mock(return_value=[]),
            base_candidates=mock.Mock(side_effect=lambda **kwargs: candidates[:kwargs["limit"]]))
        with self.answers(["1"]), mock.patch.object(packages, "download_asset",
                side_effect=lambda asset, cache: paths[candidates.index(asset)]) as download:
            base = asyncio.run(wizard.choose_missing_base(wizard.arguments([]), destination(), catalog))
        catalog.release_choices.assert_called_once_with(
            target_id=destination().target_id, include_prerelease=True, max_pages=6)
        catalog.base_candidates.assert_called_once_with(
            target_id=destination().target_id, fw_version=destination().version, limit=16)
        self.assertEqual(download.call_count, 5)
        self.assertEqual(ota.parse_endf(base.read_bytes()).body_hash, destination().base_hash)

    def test_existing_automation_flags_do_not_enter_main_menu(self):
        with mock.patch.object(wizard, "menu_main", side_effect=AssertionError("interactive menu")), \
                mock.patch.object(wizard, "ensure_dependencies") as setup, \
                mock.patch.object(wizard, "run", new_callable=mock.AsyncMock, return_value=0) as run:
            self.assertEqual(wizard.main(["--check", "--port", "COM7"]), 0)
        setup.assert_called_once()
        self.assertTrue(run.await_args.args[0].check)
        self.assertEqual(run.await_args.args[0].port, "COM7")


if __name__ == "__main__":
    unittest.main()
