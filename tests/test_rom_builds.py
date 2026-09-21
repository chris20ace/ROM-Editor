"""Isolated ROM build jobs tested with a tiny local fixture compiler process."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import zipfile

from rom_builds import ACTIVE, GBA_LOGO, MAX_LOG, RomBuilds, validate_rom


def fixture_rom():
    data = bytearray(1024)
    data[:4] = bytes.fromhex('2E0000EA')
    data[4:0xA0] = GBA_LOGO
    data[0xA0:0xAC] = b'FIXTURE ROM\x00'
    data[0xAC:0xB0] = b'TEST'
    data[0xB0:0xB2] = b'01'
    data[0xB2] = 0x96
    data[0xBD] = (-sum(data[0xA0:0xBD]) - 0x19) & 255
    return bytes(data)


def make_build_fixture(root, script=None):
    root = Path(root)
    source, state, tools = root / 'source' / 'pokeemerald', root / '.workbench', root / 'toolchain'
    source.mkdir(parents=True)
    state.mkdir(parents=True)
    tools.mkdir(parents=True)
    files = {
        'Makefile': b'all:\n\t@echo fixture\n', 'rom.sha1': b'fixture\n',
        'src/data/trainers.h': b'original trainer data\n',
        'data/layouts/Test/map.bin': b'\x01\x02\x03\x04',
        'data/maps/Test/map.json': b'{"name":"Test"}',
        'data/maps/Test/scripts.inc': b'NPC::\n end\n',
        'graphics/pokemon/treecko/front.png': b'unchanged protected species art',
        'data/mb_fixture.gba': fixture_rom(),
    }
    for name, blob in files.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    compiler = tools / 'agbcc'
    suffix = '.exe' if os.name == 'nt' else ''
    for name in ('bin/agbcc' + suffix, 'bin/old_agbcc' + suffix, 'bin/agbcc_arm' + suffix,
                 'include/stdlib.h', 'lib/libc.a', 'lib/libgcc.a'):
        target = compiler / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'compiler fixture')
    runner = tools / 'runner.py'
    runner.write_text(script or '''from pathlib import Path
import shutil
print('Fixture compiler running', flush=True)
Path('src/data/trainers.h').write_text('build-only generated change')
shutil.copyfile('data/mb_fixture.gba', 'pokeemerald.gba')
print('Fixture compiler finished', flush=True)
''', encoding='utf-8')
    for executable in ('bash', 'make'):
        (tools / executable).write_text('fixture', encoding='utf-8')
    config = {'bash': str(tools / 'bash'), 'make': str(tools / 'make'), 'agbcc': str(compiler),
              'build_root': str(root / 'buildstore'), 'bin_dirs': [], 'fixture_runner': str(runner)}
    (state / 'rom-toolchain.json').write_text(json.dumps(config), encoding='utf-8')
    return SimpleNamespace(root=root.resolve(), source=source.resolve(), state=state, lock=threading.RLock()), config


class FixtureBuilds(RomBuilds):
    def _command(self, config):
        return [sys.executable, '-u', config['fixture_runner']]


class RomBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project, self.config = make_build_fixture(self.root)
        self.service = FixtureBuilds(self.project)
        self.addCleanup(self.stop)

    def stop(self):
        if self.service._active:
            self.service.cancel(self.service._active)
        if self.service._thread:
            self.service._thread.join(15)

    def source_files(self):
        return {p.relative_to(self.project.source).as_posix(): p.read_bytes()
                for p in self.project.source.rglob('*') if p.is_file()}

    def wait(self, job, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.service.get(job['id'])
            if current['status'] not in ACTIVE and self.service._active != job['id']:
                return current
            time.sleep(0.02)
        self.fail('Fixture build did not finish in time: ' + str(self.service.get(job['id'])))

    def runner(self, code):
        Path(self.config['fixture_runner']).write_text(code, encoding='utf-8')

    def test_build_uses_frozen_snapshot_preserves_live_source_and_validates_download(self):
        before = self.source_files()
        result = self.wait(self.service.start({'name': 'My Emerald Adventure'}))
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(before, self.source_files())
        self.assertEqual(result['rom_sha256'], hashlib.sha256(fixture_rom()).hexdigest())
        self.assertEqual(result['rom_header']['game_code'], 'TEST')
        self.assertIn('Fixture compiler finished', result['log'])
        self.assertTrue(result['source_sha256'])
        self.assertTrue(result['started_at'])
        self.assertTrue(result['finished_at'])
        download = self.service.download(result['id'])
        self.assertEqual(download['filename'], 'My-Emerald-Adventure.gba')
        self.assertEqual(download['path'].read_bytes(), fixture_rom())
        self.assertNotIn('_home', result)
        home = download['path'].parent
        with zipfile.ZipFile(home / 'snapshot.zip') as archive:
            self.assertEqual(archive.read('src/data/trainers.h'), before['src/data/trainers.h'])
            self.assertEqual(archive.read('data/layouts/Test/map.bin'), before['data/layouts/Test/map.bin'])
            self.assertEqual(archive.read('data/mb_fixture.gba'), fixture_rom())
        manifest = json.loads((home / 'snapshot.json').read_text())
        self.assertEqual(manifest['source_sha256'], result['source_sha256'])
        self.assertEqual(manifest['files'][0]['sha256'], hashlib.sha256(before[manifest['files'][0]['path']]).hexdigest())

    def test_snapshot_excludes_generated_files_but_keeps_binary_game_inputs(self):
        generated = ['pokeemerald.gba', 'build/emerald/output.o', '.git/config', 'src/foo.o',
                     'graphics/pokemon/treecko/front.4bpp', 'include/constants/map_groups.h',
                     'data/maps/Test/header.inc', 'data/maps/Test/connections.inc', 'data/maps/Test/events.inc',
                     'tools/gbagfx/gbagfx.exe', 'tools/gbagfx/gbagfx', 'tools/agbcc/old.txt']
        for name in generated:
            target = self.project.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'stale generated data')
        custom_map = self.project.source / 'data/maps/build/map.json'
        custom_map.parent.mkdir(parents=True)
        custom_map.write_text('{"name":"build"}', encoding='utf-8')
        result = self.wait(self.service.start())
        self.assertEqual(result['status'], 'succeeded', result)
        home = self.service.download(result['id'])['path'].parent
        with zipfile.ZipFile(home / 'snapshot.zip') as archive:
            self.assertTrue(set(generated).isdisjoint(archive.namelist()))
            self.assertIn('data/mb_fixture.gba', archive.namelist())
            self.assertIn('data/layouts/Test/map.bin', archive.namelist())
            self.assertIn('data/maps/Test/scripts.inc', archive.namelist())
            self.assertIn('data/maps/build/map.json', archive.namelist())
        self.assertTrue((home / 'source/tools/agbcc/lib/libgcc.a').is_file())
        self.assertFalse((home / 'source/tools/agbcc/old.txt').exists())

    def test_live_edits_after_snapshot_do_not_change_build_inputs_or_hash(self):
        self.runner("from pathlib import Path\nimport time,shutil\ntime.sleep(0.3)\nprint(Path('src/data/trainers.h').read_text(),flush=True)\nshutil.copyfile('data/mb_fixture.gba','pokeemerald.gba')\n")
        first = self.service.start()
        (self.project.source / 'src/data/trainers.h').write_text('new live edit', encoding='utf-8')
        finished = self.wait(first)
        self.assertIn('original trainer data', finished['log'])
        self.assertNotIn('new live edit', finished['log'])
        second = self.wait(self.service.start())
        self.assertNotEqual(finished['source_sha256'], second['source_sha256'])
        self.assertIn('new live edit', second['log'])

    def test_only_one_active_build_and_cancel_leaves_no_download(self):
        before = self.source_files()
        self.runner("import time\nprint('waiting',flush=True)\ntime.sleep(30)\n")
        job = self.service.start()
        with self.assertRaisesRegex(ValueError, 'already running'):
            self.service.start()
        self.service.cancel(job['id'])
        result = self.wait(job)
        self.assertEqual(result['status'], 'cancelled', result)
        self.assertIsNone(result['download_url'])
        with self.assertRaisesRegex(ValueError, 'successfully'):
            self.service.download(job['id'])
        self.assertEqual(before, self.source_files())
        self.assertIsNone(self.service.status()['active_job'])

    def test_short_flushed_log_line_is_visible_while_compiler_is_still_running(self):
        self.runner("import time\nprint('Compiler startup visible now',flush=True)\ntime.sleep(30)\n")
        job = self.service.start()
        deadline = time.monotonic() + 5
        current = self.service.get(job['id'])
        while 'Compiler startup visible now' not in current['log'] and time.monotonic() < deadline:
            time.sleep(0.02)
            current = self.service.get(job['id'])
        self.assertIn('Compiler startup visible now', current['log'])
        self.assertEqual(current['status'], 'building')
        self.assertIsNotNone(self.service._process)
        self.assertIsNone(self.service._process.poll())
        self.service.cancel(job['id'])
        self.assertEqual(self.wait(job)['status'], 'cancelled')

    @unittest.skipUnless(os.name == 'nt', 'Windows Job Object regression')
    def test_cancel_stops_orphaned_compiler_grandchildren(self):
        child = "from pathlib import Path;import time;time.sleep(2);Path('unexpected-child-output').write_text('survived')"
        middle = f"import subprocess,sys;subprocess.Popen([sys.executable,'-c',{child!r}])"
        self.runner(f"import subprocess,sys,time\nsubprocess.run([sys.executable,'-c',{middle!r}],check=True)\nprint('Grandchild orphaned',flush=True)\ntime.sleep(30)\n")
        job = self.service.start()
        deadline = time.monotonic() + 5
        while 'Grandchild orphaned' not in self.service.get(job['id'])['log'] and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertIn('Grandchild orphaned', self.service.get(job['id'])['log'])
        home = Path(self.service._jobs[job['id']]['_home'])
        self.assertIsNotNone(getattr(self.service._process, '_rom_build_job', None))
        self.service.cancel(job['id'])
        self.assertEqual(self.wait(job)['status'], 'cancelled')
        time.sleep(2.2)
        self.assertFalse((home / 'source/unexpected-child-output').exists())

    def test_failure_keeps_bounded_log_and_does_not_offer_download(self):
        self.runner("import sys\nprint('x'*200000)\nprint('fatal: fixture compile error')\nsys.exit(2)\n")
        result = self.wait(self.service.start())
        self.assertEqual(result['status'], 'failed', result)
        self.assertLessEqual(len(result['log']), MAX_LOG)
        self.assertTrue(result['log_truncated'])
        self.assertIn('fixture compile error', result['log'])
        self.assertIn('code 2', result['error'])
        self.assertIsNone(result['download_url'])
        with self.assertRaises(ValueError):
            self.service.download(result['id'])

    def test_successful_compiler_without_valid_rom_fails_validation(self):
        self.runner("from pathlib import Path\nPath('pokeemerald.gba').write_bytes(b'bad output')\n")
        result = self.wait(self.service.start())
        self.assertEqual(result['status'], 'failed')
        self.assertIn('size', result['error'])
        self.assertIsNone(result['download_url'])

    def test_download_rechecks_artifact_integrity(self):
        result = self.wait(self.service.start())
        path = self.service.download(result['id'])['path']
        blob = bytearray(path.read_bytes())
        blob[-1] ^= 1
        path.write_bytes(blob)
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.service.download(result['id'])

    def test_history_survives_restart_and_interrupted_jobs_are_marked_failed(self):
        result = self.wait(self.service.start())
        restored = FixtureBuilds(self.project)
        self.assertEqual(restored.get(result['id'])['rom_sha256'], result['rom_sha256'])
        self.assertEqual(restored.download(result['id'])['path'].read_bytes(), fixture_rom())
        path = self.service.state / (result['id'] + '.json')
        job = json.loads(path.read_text())
        job.update(status='building', download_url=None)
        path.write_text(json.dumps(job), encoding='utf-8')
        recovered = FixtureBuilds(self.project).get(result['id'])
        self.assertEqual(recovered['status'], 'failed')
        self.assertIn('stopped', recovered['error'])
        self.assertIsNone(recovered['download_url'])

    def test_invalid_config_names_and_job_ids_rejected(self):
        self.assertTrue(self.service.toolchain()['ready'])
        for name in ('', '\r\nContent-Disposition: bad', 'x'*81, 9):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.service.start({'name': name})
        for ident in ('../outside', 'not-a-job', None):
            with self.subTest(id=ident), self.assertRaises(ValueError):
                self.service.get(ident)
        Path(self.config['make']).unlink()
        self.assertFalse(self.service.toolchain()['ready'])
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.service.start()

    def test_source_change_during_snapshot_fails_without_compiling(self):
        original = self.service._inventory
        calls = []
        def inventory():
            result = original()
            calls.append(True)
            if len(calls) == 2:
                result['unexpected.txt'] = (1, 1)
            return result
        self.service._inventory = inventory
        result = self.service.start()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('changed while', result['error'])
        self.assertIsNone(result['started_at'])

    def test_cleanup_keeps_single_build_ownership_until_process_is_closed(self):
        entered, release = threading.Event(), threading.Event()
        original = self.service._finish
        def finish(job, status, message, error=None, release=True):
            original(job, status, message, error, release=release)
            if not release:
                entered.set()
                gate.wait(5)
        gate = release
        self.service._finish = finish
        job = self.service.start()
        self.assertTrue(entered.wait(5))
        try:
            with self.assertRaisesRegex(ValueError, 'already running'):
                self.service.start()
        finally:
            gate.set()
        self.assertEqual(self.wait(job)['status'], 'succeeded')
        self.service._finish = original
        self.assertEqual(self.wait(self.service.start())['status'], 'succeeded')

    def test_initial_history_write_failure_releases_slot_and_allows_retry(self):
        original = self.service._save
        def fail(job):
            raise OSError('fixture history disk unavailable')
        self.service._save = fail
        with self.assertRaisesRegex(ValueError, 'Could not create ROM build history'):
            self.service.start()
        self.assertIsNone(self.service._active)
        self.assertIsNone(self.service._thread)
        self.assertEqual(self.service.status()['history'], [])
        self.service._save = original
        self.assertEqual(self.wait(self.service.start())['status'], 'succeeded')

    def test_terminal_history_write_failure_marks_failed_and_releases_worker_for_retry(self):
        original = self.service._save
        def fail_terminal(job):
            if job['status'] not in ACTIVE:
                raise OSError('fixture terminal history unavailable')
            return original(job)
        self.service._save = fail_terminal
        result = self.wait(self.service.start())
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('Could not save ROM build history', result['error'])
        self.assertIsNone(result['download_url'])
        self.assertIsNone(self.service._active)
        self.assertIsNone(self.service._process)
        with self.assertRaises(ValueError):
            self.service.download(result['id'])
        self.service._save = original
        self.assertEqual(self.wait(self.service.start())['status'], 'succeeded')

    def test_history_failure_after_launch_does_not_crash_log_reader_or_leave_active_job(self):
        original = self.service._save
        def fail_after_launch(job):
            if job.get('started_at'):
                raise OSError('fixture build history unavailable')
            return original(job)
        self.service._save = fail_after_launch
        result = self.wait(self.service.start())
        self.assertEqual(result['status'], 'failed', result)
        self.assertIsNone(self.service._active)
        self.assertIsNone(self.service._process)
        self.service._save = original
        self.assertEqual(self.wait(self.service.start())['status'], 'succeeded')

    def failed_job(self):
        self.runner("import sys\nprint('Fixture linker missing',flush=True)\nsys.exit(2)\n")
        job = self.wait(self.service.start({'name': 'Retry example'}))
        self.assertEqual(job['status'], 'failed', job)
        return job

    def repair_fixture_compiler(self):
        self.runner("from pathlib import Path\nimport shutil\nprint(Path('src/data/trainers.h').read_text(),flush=True)\nprint('Fixture linker repaired',flush=True)\nshutil.copyfile('data/mb_fixture.gba','pokeemerald.gba')\n")

    def test_retry_reuses_original_snapshot_and_cached_outputs_after_toolchain_repair(self):
        job = self.failed_job()
        home = Path(self.service._jobs[job['id']]['_home'])
        cached = home / 'source/build/cached-output.o'
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b'cached build output')
        (self.project.source / 'src/data/trainers.h').write_text('later live edit', encoding='utf-8')
        compiler_before = (home / 'source/tools/agbcc/lib/libc.a').read_bytes()
        (Path(self.config['agbcc']) / 'lib/libc.a').write_bytes(b'changed installed toolchain')
        self.repair_fixture_compiler()
        result = self.wait(self.service.retry(job['id']))
        self.assertEqual(result['id'], job['id'])
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['source_sha256'], job['source_sha256'])
        self.assertEqual(result['attempt_count'], 2)
        self.assertEqual(result['attempts'][0]['status'], 'failed')
        self.assertIn('code 2', result['attempts'][0]['error'])
        self.assertIn('Retry attempt 2', result['log'])
        self.assertIn('original trainer data', result['log'])
        self.assertNotIn('later live edit', result['log'])
        self.assertEqual(cached.read_bytes(), b'cached build output')
        self.assertEqual((home / 'source/tools/agbcc/lib/libc.a').read_bytes(), compiler_before)
        self.assertEqual(self.service.download(result['id'])['path'].read_bytes(), fixture_rom())

    def test_retry_rejects_changed_cached_source_without_starting_an_attempt(self):
        job = self.failed_job()
        home = Path(self.service._jobs[job['id']]['_home'])
        (home / 'source/src/data/trainers.h').write_text('modified cached input', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'cached build input changed'):
            self.service.retry(job['id'])
        self.assertEqual(self.service.get(job['id'])['status'], 'failed')
        self.assertEqual(self.service.get(job['id'])['attempt_count'], 1)
        self.assertIsNone(self.service._active)

    def test_retry_rejects_changed_archive_or_manifest(self):
        job = self.failed_job()
        home = Path(self.service._jobs[job['id']]['_home'])
        archive_path = home / 'snapshot.zip'
        original_archive = archive_path.read_bytes()
        with zipfile.ZipFile(archive_path, 'r') as archive:
            contents = {name: archive.read(name) for name in archive.namelist()}
        contents['src/data/trainers.h'] = b'changed archive input'
        with zipfile.ZipFile(archive_path, 'w') as archive:
            for name, blob in contents.items():
                archive.writestr(name, blob)
        with self.assertRaisesRegex(ValueError, 'source archive changed'):
            self.service.retry(job['id'])
        archive_path.write_bytes(original_archive)
        manifest_path = home / 'snapshot.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['source_sha256'] = '0' * 64
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'manifest no longer matches'):
            self.service.retry(job['id'])

    def test_retry_rejects_missing_snapshot_and_successful_or_active_jobs(self):
        job = self.failed_job()
        home = Path(self.service._jobs[job['id']]['_home'])
        (home / 'snapshot.json').unlink()
        with self.assertRaisesRegex(ValueError, 'complete saved source snapshot'):
            self.service.retry(job['id'])
        self.repair_fixture_compiler()
        success = self.wait(self.service.start())
        with self.assertRaisesRegex(ValueError, 'Only a failed or cancelled'):
            self.service.retry(success['id'])
        self.runner("import time\ntime.sleep(30)\n")
        active = self.service.start()
        with self.assertRaisesRegex(ValueError, 'already running'):
            self.service.retry(job['id'])
        self.service.cancel(active['id'])
        self.wait(active)

    def test_retry_of_cancelled_job_and_retry_history_write_failure_allow_next_try(self):
        self.runner("import time\ntime.sleep(30)\n")
        job = self.service.start()
        self.service.cancel(job['id'])
        self.assertEqual(self.wait(job)['status'], 'cancelled')
        original = self.service._save
        def fail(job):
            raise OSError('fixture retry history unavailable')
        self.service._save = fail
        with self.assertRaisesRegex(ValueError, 'Could not record the retry'):
            self.service.retry(job['id'])
        self.assertIsNone(self.service._active)
        self.assertEqual(self.service.get(job['id'])['status'], 'cancelled')
        self.assertEqual(self.service.get(job['id'])['attempt_count'], 1)
        self.service._save = original
        self.repair_fixture_compiler()
        self.assertEqual(self.wait(self.service.retry(job['id']))['status'], 'succeeded')


class RomHeaderTests(unittest.TestCase):
    def test_header_logo_checksum_entry_and_size_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.gba'
            good = fixture_rom()
            path.write_bytes(good)
            self.assertEqual(validate_rom(path)['game_code'], 'TEST')
            for index in (4, 0xB2, 0xBD, 3):
                bad = bytearray(good)
                bad[index] ^= 1
                path.write_bytes(bad)
                with self.subTest(index=index), self.assertRaises(ValueError):
                    validate_rom(path)


if __name__ == '__main__':
    unittest.main()
