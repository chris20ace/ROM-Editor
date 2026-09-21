"""Local ROM build jobs: frozen inputs, isolated compilation, verified downloads."""
from __future__ import annotations

from datetime import datetime, timezone
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
import unicodedata
import zipfile


ACTIVE = {'snapshotting', 'queued', 'building'}
MAX_LOG = 128 * 1024
MAX_ROM = 32 * 1024 * 1024
JOB_ID = re.compile(r'^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{8}$')
GENERATED = {
    'include/constants/map_groups.h', 'include/constants/layouts.h', 'include/constants/map_event_ids.h',
    'data/maps/connections.inc', 'data/maps/groups.inc', 'data/maps/events.inc', 'data/maps/headers.inc',
    'data/layouts/layouts.inc', 'data/layouts/layouts_table.inc', 'src/data/wild_encounters.h',
    'include/constants/wild_encounter.h', 'src/data/region_map/region_map_entries.h',
    'include/constants/region_map_sections.h', 'src/data/heal_locations.h', 'include/constants/heal_locations.h',
}
GENERATED_SUFFIXES = {'.o', '.d', '.a', '.exe', '.elf', '.map', '.sym', '.i', '.dump', '.ddump',
                      '.1bpp', '.4bpp', '.8bpp', '.gbapal', '.lz', '.rl', '.latfont', '.hwjpnfont', '.fwjpnfont'}
TOOL_NAMES = {'bin2c', 'gbafix', 'gbagfx', 'jsonproc', 'mapjson', 'mid2agb', 'preproc',
              'ramscrgen', 'rsfont', 'scaninc', 'wav2agb'}
# The GBA logo/boot header bytes used by this source's tools/gbafix/gbafix.c.
GBA_LOGO = bytes.fromhex(
    '24FFAE51699AA2213D84820A84E409AD11248B98C0817F21A352BE199309CE20'
    '10464A4AF82731EC58C7E83382E3CEBF85F4DF94CE4B09C194568AC01372A7FC'
    '9F844D73A3CA9A615897A327FC039876231DC7610304AE56BF38840040A70EFD'
    'FF52FE036F9530F197FBC08560D68025A963BE03014E38E2F9A234FFBB3E0344'
    '780090CB88113A9465C07C6387F03CAFD625E48B380AAC7221D4F807')


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp-' + secrets.token_hex(4))
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def _filename(name):
    ascii_name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    clean = re.sub(r'[^A-Za-z0-9_-]+', '-', ascii_name.removesuffix('.gba')).strip('-_')[:64]
    return (clean or 'My-Emerald') + '.gba'


def validate_rom(path):
    """Validate a finished build's boot header; this is not emulator play-testing."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('The compiler did not produce a ROM file.')
    size = path.stat().st_size
    if not 256 <= size <= MAX_ROM or size % 4:
        raise ValueError('The generated ROM has an invalid GBA size (256 bytes to 32 MiB, aligned to 4 bytes).')
    with path.open('rb') as stream:
        header = stream.read(0xC0)
    if header[4:0xA0] != GBA_LOGO or header[0xB2] != 0x96 or header[0xB3] != 0:
        raise ValueError('The generated file does not contain a valid GBA boot header.')
    if ((-sum(header[0xA0:0xBD]) - 0x19) & 0xFF) != header[0xBD]:
        raise ValueError('The generated ROM header checksum is invalid.')
    branch = int.from_bytes(header[:4], 'little')
    if branch >> 24 != 0xEA:
        raise ValueError('The generated ROM has an invalid ARM entry instruction.')
    offset = branch & 0xFFFFFF
    if offset & 0x800000:
        offset -= 0x1000000
    entry = 8 + offset * 4
    if not 0xC0 <= entry < size:
        raise ValueError('The generated ROM entry point lies outside its code.')
    return {'size': size, 'sha256': _hash_file(path),
            'title': header[0xA0:0xAC].rstrip(b'\x00 ').decode('ascii', errors='replace'),
            'game_code': header[0xAC:0xB0].decode('ascii', errors='replace'),
            'revision': header[0xBC], 'entry_offset': entry}


class _Cancelled(Exception):
    pass


class RomBuilds:
    """Keep one instance per Project. start() also acquires Project.lock itself."""
    def __init__(self, project):
        self.project = project
        self.state = project.state / 'rom-builds'
        self.config_path = project.state / 'rom-toolchain.json'
        self.project_key = hashlib.sha256(str(project.root).encode()).hexdigest()[:16]
        self._lock = threading.RLock()
        self._jobs = {}
        self._active = None
        self._process = None
        self._cancel = threading.Event()
        self._thread = None
        self._last_log_save = 0
        for path in sorted(self.state.glob('*.json')):
            if not JOB_ID.fullmatch(path.stem):
                continue
            try:
                job = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if job.get('id') != path.stem:
                continue
            if job.get('status') in ACTIVE:
                job.update(status='failed', stage='Interrupted', finished_at=_now(),
                           error='The editor stopped before this build completed. Start a new build.',
                           message='Build interrupted; no download was published.')
                _atomic_json(path, job)
            self._jobs[job['id']] = job

    def _config(self):
        if not self.config_path.is_file():
            raise ValueError('ROM compiler setup is not finished. Configure the local toolchain first.')
        try:
            config = json.loads(self.config_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise ValueError('The ROM toolchain configuration could not be read.') from error
        for name in ('bash', 'make', 'agbcc', 'build_root'):
            value = config.get(name)
            if not isinstance(value, str) or not value or not Path(value).is_absolute():
                raise ValueError(f'The ROM toolchain needs an absolute {name} path.')
            config[name] = str(Path(value).resolve())
        if any(ch.isspace() for ch in config['build_root']):
            raise ValueError('The ROM build folder must have a path without spaces for the original Makefile.')
        build_root = Path(config['build_root'])
        if build_root.is_relative_to(self.project.source) or self.project.source.is_relative_to(build_root):
            raise ValueError('ROM builds must use a separate folder outside the source project.')
        if not isinstance(config.get('bin_dirs', []), list) or any(not isinstance(p, str) or not Path(p).is_absolute() for p in config.get('bin_dirs', [])):
            raise ValueError('The ROM toolchain bin_dirs must contain absolute directory paths.')
        missing = [name for name in ('bash', 'make') if not Path(config[name]).is_file()]
        compiler = Path(config['agbcc'])
        suffix = '.exe' if os.name == 'nt' else ''
        for binary in ('agbcc', 'old_agbcc', 'agbcc_arm'):
            if not (compiler / 'bin' / (binary + suffix)).is_file():
                missing.append('agbcc/' + binary)
        for library in ('libc.a', 'libgcc.a'):
            if not (compiler / 'lib' / library).is_file():
                missing.append('agbcc/' + library)
        if not (compiler / 'include').is_dir():
            missing.append('agbcc headers')
        if missing:
            raise ValueError('ROM compiler setup is incomplete: ' + ', '.join(missing) + '.')
        return config

    def toolchain(self):
        try:
            config = self._config()
            return {'ready': True, 'message': 'Local compiler ready. Builds use a saved source snapshot.',
                    'label': config.get('label', 'Emerald agbcc toolchain')}
        except (ValueError, OSError) as error:
            return {'ready': False, 'message': str(error), 'label': 'Emerald agbcc toolchain'}

    def _public(self, job):
        visible = {key: value for key, value in job.items() if not key.startswith('_')}
        visible['download_url'] = '/api/build/download?id=' + job['id'] if job['status'] == 'succeeded' else None
        return visible

    def status(self):
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j['id'], reverse=True)
            return {'toolchain': self.toolchain(),
                    'active_job': self._public(self._jobs[self._active]) if self._active else None,
                    'history': [self._public(job) for job in jobs[:50]]}

    def get(self, job_id):
        with self._lock:
            return self._public(self._job(job_id))

    def _job(self, job_id):
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id) or job_id not in self._jobs:
            raise ValueError('Choose a ROM build from this project’s build history.')
        return self._jobs[job_id]

    def _save(self, job):
        _atomic_json(self.state / (job['id'] + '.json'), job)

    def _update(self, job, **values):
        with self._lock:
            job.update(values)
            self._save(job)

    def _log(self, job, text):
        with self._lock:
            joined = job['log'] + text.replace('\x00', '')
            job['log_truncated'] = job.get('log_truncated', False) or len(joined) > MAX_LOG
            job['log'] = joined[-MAX_LOG:]
            if time.monotonic() - self._last_log_save >= 0.5:
                try:
                    self._save(job)
                except OSError as error:
                    # A full/unavailable disk must not crash the output reader
                    # and leave the compiler blocked on its stdout pipe.
                    job['_persistence_error'] = str(error)
                self._last_log_save = time.monotonic()

    def _included(self, relative, path):
        parts = Path(relative).parts
        if any(part in ('.git', '.workbench', '__pycache__') for part in parts) or parts[0] == 'build':
            return False
        if relative.startswith(('tools/agbcc/', 'tools/compresSmol/')) or relative in GENERATED:
            return False
        if path.suffix.lower() in GENERATED_SUFFIXES:
            return False
        if len(parts) == 1 and path.suffix.lower() in ('.gba', '.sav', '.srm', '.log'):
            return False
        if len(parts) == 4 and parts[:2] == ('data', 'maps') and path.name in ('header.inc', 'events.inc', 'connections.inc'):
            return False
        if relative.startswith('tools/') and path.name in TOOL_NAMES:
            return False
        if relative.startswith('sound/') and path.suffix == '.bin' and path.with_suffix('.wav').is_file():
            return False
        if relative.startswith('sound/songs/midi/') and path.suffix == '.s' and path.with_suffix('.mid').is_file():
            return False
        return True

    def _inventory(self):
        source = self.project.source
        inventory = {}
        for directory, dirs, files in os.walk(source, followlinks=False):
            dirs[:] = [name for name in dirs if name not in ('.git', '.workbench', '__pycache__')
                       and not (Path(directory) == source and name == 'build')]
            for name in dirs:
                path = Path(directory) / name
                if path.is_symlink() or not path.resolve().is_relative_to(source):
                    raise ValueError('Source snapshots cannot follow linked folders: ' + str(path.relative_to(source)))
            for name in files:
                path = Path(directory) / name
                relative = path.relative_to(source).as_posix()
                if not self._included(relative, path):
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(source):
                    raise ValueError('Source snapshots cannot include linked files: ' + relative)
                stat = path.stat()
                inventory[relative] = (stat.st_size, stat.st_mtime_ns)
        return inventory

    def _snapshot(self, job, config):
        home = Path(job['_home'])
        work = home / 'source'
        work.mkdir(parents=True, exist_ok=False)
        initial = self._inventory()
        digest, files = hashlib.sha256(), []
        with zipfile.ZipFile(home / 'snapshot.zip', 'w', compression=zipfile.ZIP_STORED) as archive:
            for relative in sorted(initial):
                if self._cancel.is_set():
                    raise _Cancelled()
                path = self.project.source / relative
                blob = path.read_bytes()
                content_hash = hashlib.sha256(blob).hexdigest()
                digest.update(relative.encode() + b'\0' + content_hash.encode() + b'\0')
                destination = work / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(blob)
                archive.writestr(relative, blob)
                files.append({'path': relative, 'size': len(blob), 'sha256': content_hash})
        if self._inventory() != initial:
            raise ValueError('Source files changed while creating the snapshot. Save your changes, then build again.')
        # Compiler installation is copied too; neither live source nor compiler
        # assets share hardlinks with the writable build directory.
        shutil.copytree(config['agbcc'], work / 'tools' / 'agbcc', symlinks=False)
        _atomic_json(home / 'snapshot.json', {'source_sha256': digest.hexdigest(), 'created_at': job['created_at'], 'files': files})
        return digest.hexdigest(), len(files), sum(item['size'] for item in files)

    def start(self, body=None):
        body = {} if body is None else body
        if not isinstance(body, dict):
            raise ValueError('ROM build options must be an object.')
        name = body.get('name', 'My Emerald')
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError('Give the ROM a name of 1 to 80 characters without control characters.')
        with self.project.lock:
            config = self._config()
            with self._lock:
                if self._active:
                    raise ValueError('A ROM build is already running. Wait for it or cancel it first.')
                ident = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + secrets.token_hex(4)
                home = Path(config['build_root']) / self.project_key / ident
                job = {'id': ident, 'name': name.strip(), 'status': 'snapshotting', 'stage': 'Saving source snapshot',
                       'created_at': _now(), 'started_at': None, 'finished_at': None, 'source_sha256': None,
                       'rom_sha256': None, 'size': None, 'log': '', 'error': None, 'message': 'Copying saved game files.',
                       'filename': _filename(name.strip()), 'attempt_count': 1, 'attempts': [],
                       '_home': str(home), '_build_root': config['build_root']}
                self._active = ident
                self._cancel.clear()
                self._jobs[ident] = job
                try:
                    self._save(job)
                except OSError as error:
                    # There is no worker yet to observe cancellation or release
                    # this slot. Roll it back here so a later retry can start.
                    self._active = None
                    self._jobs.pop(ident, None)
                    raise ValueError('Could not create ROM build history. Check available disk space and folder access, then try again: ' + str(error)) from error
            try:
                source_hash, count, size = self._snapshot(job, config)
                if self._cancel.is_set():
                    raise _Cancelled()
                self._update(job, source_sha256=source_hash, source_files=count, source_bytes=size,
                             status='queued', stage='Starting compiler', message='Saved source snapshot is ready.')
                self._thread = threading.Thread(target=self._run, args=(job, config), daemon=True, name='rom-build-' + ident)
                self._thread.start()
            except _Cancelled:
                self._finish(job, 'cancelled', 'Build cancelled before compilation.')
            except Exception as error:
                self._finish(job, 'failed', 'Could not create the source snapshot.', str(error))
            return self.get(ident)

    def _job_home(self, job):
        expected = Path(job['_build_root']).resolve() / self.project_key / job['id']
        home = Path(job['_home']).resolve()
        if home != expected or home.is_relative_to(self.project.source):
            raise ValueError('This ROM build has an invalid artifact location.')
        return home

    def _validate_retry_snapshot(self, job):
        home = self._job_home(job)
        work, manifest_path, archive_path = home / 'source', home / 'snapshot.json', home / 'snapshot.zip'
        if work.resolve() != work or not work.is_dir():
            raise ValueError('The saved build folder is missing or linked elsewhere. Generate a new ROM.')
        for path in (manifest_path, archive_path):
            if path.is_symlink() or not path.is_file():
                raise ValueError('This build has no complete saved source snapshot. Generate a new ROM.')
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise ValueError('The saved source manifest could not be read. Generate a new ROM.') from error
        if not job.get('source_sha256') or manifest.get('source_sha256') != job['source_sha256']:
            raise ValueError('The saved source manifest no longer matches this build. Generate a new ROM.')
        rows = manifest.get('files')
        if not isinstance(rows, list) or not rows:
            raise ValueError('The saved source manifest is incomplete. Generate a new ROM.')
        files = {}
        for row in rows:
            relative = row.get('path') if isinstance(row, dict) else None
            if (not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative
                    or relative.startswith('/') or '..' in relative.split('/')
                    or Path(relative).as_posix() != relative or relative in files
                    or not isinstance(row.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', row['sha256'])
                    or type(row.get('size')) is not int or row['size'] < 0):
                raise ValueError('The saved source manifest contains invalid files. Generate a new ROM.')
            files[relative] = row
        digest = hashlib.sha256()
        try:
            with zipfile.ZipFile(archive_path) as archive:
                if len(archive.namelist()) != len(files) or set(archive.namelist()) != set(files):
                    raise ValueError('The saved source archive no longer matches its manifest. Generate a new ROM.')
                for relative in sorted(files):
                    row = files[relative]
                    blob = archive.read(relative)  # Includes ZIP CRC validation.
                    content_hash = hashlib.sha256(blob).hexdigest()
                    if len(blob) != row['size'] or content_hash != row['sha256']:
                        raise ValueError('The saved source archive changed: ' + relative + '. Generate a new ROM.')
                    digest.update(relative.encode() + b'\0' + content_hash.encode() + b'\0')
                    current = work / relative
                    if (current.is_symlink() or current.resolve() != current or not current.is_file()
                            or current.stat().st_size != row['size'] or _hash_file(current) != content_hash):
                        raise ValueError('The cached build input changed: ' + relative + '. Generate a new ROM from your saved project instead.')
        except (OSError, zipfile.BadZipFile, KeyError) as error:
            raise ValueError('The saved source snapshot is incomplete or damaged. Generate a new ROM.') from error
        if digest.hexdigest() != job['source_sha256']:
            raise ValueError('The saved source hash no longer matches this build. Generate a new ROM.')
        compiler = work / 'tools/agbcc'
        suffix = '.exe' if os.name == 'nt' else ''
        for relative in ('bin/agbcc' + suffix, 'bin/old_agbcc' + suffix, 'bin/agbcc_arm' + suffix,
                         'lib/libc.a', 'lib/libgcc.a', 'include'):
            path = compiler / relative
            if not path.exists() or path.is_symlink() or path.resolve() != path:
                raise ValueError('The compiler copied with this saved build is incomplete. Generate a new ROM.')

    def retry(self, job_id):
        """Retry the original saved inputs, retaining safely reusable build outputs."""
        config = self._config()
        with self._lock:
            if self._active:
                raise ValueError('A ROM build is already running. Wait for it or cancel it first.')
            job = self._job(job_id)
            if job['status'] not in ('failed', 'cancelled'):
                raise ValueError('Only a failed or cancelled build can retry its saved copy. Generate a new ROM for current edits.')
            self._validate_retry_snapshot(job)
            previous = copy.deepcopy(job)
            attempt = job.get('attempt_count', 1)
            prior = {key: job.get(key) for key in ('status', 'stage', 'started_at', 'finished_at', 'error', 'message')}
            prior['attempt'] = attempt
            log = job['log'] + f'\n\n--- Retry attempt {attempt + 1}: original saved source snapshot; current local toolchain ---\n'
            job.update(attempt_count=attempt + 1, attempts=job.get('attempts', []) + [prior],
                       status='queued', stage='Retrying saved copy', started_at=None, finished_at=None,
                       rom_sha256=None, size=None, error=None, log=log[-MAX_LOG:],
                       log_truncated=job.get('log_truncated', False) or len(log) > MAX_LOG,
                       message='Reusing the original saved snapshot and cached build outputs. Later project edits are not included.')
            job.pop('rom_header', None)
            self._active = job['id']
            self._cancel.clear()
            try:
                self._save(job)
            except OSError as error:
                job.clear()
                job.update(previous)
                self._active = None
                raise ValueError('Could not record the retry. Check available disk space and folder access: ' + str(error)) from error
            try:
                self._thread = threading.Thread(target=self._run, args=(job, config), daemon=True, name='rom-build-' + job['id'])
                self._thread.start()
            except Exception as error:
                self._finish(job, 'failed', 'Could not start the saved-copy retry.', str(error))
            return self._public(job)

    def _command(self, config):
        # User fields never enter shell code or make arguments. The portable
        # MSYS installation supplies the exact compiler paths in this PATH.
        script = 'set -euo pipefail\nexport PATH=/ucrt64/bin:/usr/bin:/bin\nexec make -j4\n'
        return [config['bash'], '--noprofile', '--norc', '-c', script]

    def _launch_process(self, command, options):
        if os.name != 'nt':
            return subprocess.Popen(command, **options)
        from windows_build_job import CREATE_SUSPENDED, WindowsBuildJob
        job, process = WindowsBuildJob(), None
        try:
            options = {**options, 'creationflags': options.get('creationflags', 0) | CREATE_SUSPENDED}
            process = subprocess.Popen(command, **options)
            process._rom_build_job = job
            job.assign_and_resume(process.pid)
            return process
        except Exception:
            job.close()
            if process is not None:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                if process.stdout:
                    process.stdout.close()
            raise

    def _run(self, job, config):
        reader, process = None, None
        try:
            if self._cancel.is_set():
                raise _Cancelled()
            self._update(job, status='building', stage='Compiling saved snapshot', started_at=_now(),
                         message='Building game code and assets. You can continue editing your project.')
            environment = os.environ.copy()
            for variable in ('MAKEFLAGS', 'MFLAGS', 'GNUMAKEFLAGS', 'BASH_ENV', 'ENV', 'DEVKITARM', 'CC', 'CXX'):
                environment.pop(variable, None)
            environment.update(MSYSTEM='MSYS', CHERE_INVOKING='1', OS='Windows_NT' if os.name == 'nt' else environment.get('OS', ''))
            environment['PATH'] = os.pathsep.join(config.get('bin_dirs', []) + [environment.get('PATH', '')])
            options = {'cwd': str(Path(job['_home']) / 'source'), 'env': environment,
                       'stdin': subprocess.DEVNULL, 'stdout': subprocess.PIPE, 'stderr': subprocess.STDOUT}
            if os.name == 'nt':
                options['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                options['start_new_session'] = True
            with self._lock:
                if self._cancel.is_set():
                    raise _Cancelled()
                process = self._launch_process(self._command(config), options)
                self._process = process
            def read_output():
                # read() can wait for a full buffer and hide short compiler
                # messages until much later. read1() emits currently available
                # pipe bytes so startup/errors appear while the build is active.
                for chunk in iter(lambda: process.stdout.read1(4096), b''):
                    self._log(job, chunk.decode('utf-8', errors='replace'))
            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            deadline = time.monotonic() + 30 * 60
            while process.poll() is None:
                if self._cancel.wait(0.1):
                    self._terminate(process)
                    raise _Cancelled()
                if time.monotonic() > deadline:
                    self._terminate(process)
                    raise ValueError('The build exceeded 30 minutes. Review the log and local compiler setup before retrying.')
            reader.join(10)
            if self._cancel.is_set():
                raise _Cancelled()
            if process.returncode:
                raise ValueError(f'Compiler exited with code {process.returncode}. Review the last errors in the build log, correct the source or compiler setup, and build again.')
            self._update(job, stage='Checking generated ROM', message='Checking the ROM boot header and file hash.')
            generated = Path(job['_home']) / 'source' / 'pokeemerald.gba'
            information = validate_rom(generated)
            artifact = Path(job['_home']) / 'game.gba'
            shutil.copyfile(generated, artifact)
            if _hash_file(artifact) != information['sha256']:
                raise ValueError('The generated ROM changed while preparing the download. Build again.')
            self._update(job, size=information['size'], rom_sha256=information['sha256'], rom_header=information)
            self._finish(job, 'succeeded', 'ROM generated. The boot header is valid; emulator play-testing is still needed.', release=False)
        except _Cancelled:
            self._finish(job, 'cancelled', 'Build cancelled. Your saved game files were not changed.', release=False)
        except Exception as error:
            self._log(job, '\nBuild failed: ' + str(error) + '\n')
            self._finish(job, 'failed', 'ROM generation failed. The build log and snapshot were preserved.', str(error), release=False)
        finally:
            try:
                if process is not None:
                    if process.poll() is None:
                        self._terminate(process)
                    job_object = getattr(process, '_rom_build_job', None)
                    if job_object:
                        job_object.close()
                    if reader:
                        reader.join(5)
                    if process.stdout:
                        process.stdout.close()
            finally:
                with self._lock:
                    if self._process is process:
                        self._process = None
                    if self._active == job['id']:
                        self._active = None
                    self._save_terminal(job)

    def _save_terminal(self, job):
        try:
            self._save(job)
        except OSError as error:
            # Keep failure visible to the current editor even if its history
            # cannot be persisted. An interrupted on-disk job recovers as failed
            # on the next server start. Do not publish an unrecorded success.
            detail = 'Could not save ROM build history. Check available disk space and folder access: ' + str(error)
            job.update(status='failed', stage='Build failed', error=detail,
                       message='The build result could not be recorded. Generate a new ROM after fixing storage access.',
                       finished_at=_now())
            job['log'] = (job['log'] + '\n' + detail + '\n')[-MAX_LOG:]

    def _finish(self, job, status, message, error=None, release=True):
        with self._lock:
            job.update(status=status, stage={'succeeded': 'Ready to download', 'failed': 'Build failed', 'cancelled': 'Cancelled'}[status],
                       message=message, error=error, finished_at=_now())
            try:
                self._save_terminal(job)
            finally:
                if release and self._active == job['id']:
                    self._active = None

    def _terminate(self, process):
        job_object = getattr(process, '_rom_build_job', None)
        if job_object:
            # MSYS can reparent descendants, so walking the current Windows PID
            # tree misses them. The job tracks descendants for their lifetimes.
            job_object.terminate()
        if process.poll() is not None:
            return
        if os.name == 'nt' and not job_object:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15, check=False)
        elif os.name != 'nt':
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def cancel(self, job_id):
        with self._lock:
            job = self._job(job_id)
            if job['status'] not in ACTIVE:
                return self._public(job)
            self._cancel.set()
            self._update(job, stage='Cancelling', message='Stopping the compiler. Saved game files are unchanged.')
            return self._public(job)

    def download(self, job_id):
        with self._lock:
            job = self._job(job_id)
            if job['status'] != 'succeeded':
                raise ValueError('Only a successfully completed ROM build can be downloaded.')
            home = self._job_home(job)
            path = home / 'game.gba'
            if path.resolve().parent != home or path.is_symlink():
                raise ValueError('This ROM build has an invalid artifact path.')
            information = validate_rom(path)
            if information['sha256'] != job['rom_sha256'] or information['size'] != job['size']:
                raise ValueError('The saved ROM file changed after its build. Generate a new ROM.')
            return {'path': path, 'filename': job['filename'], 'size': job['size'], 'sha256': job['rom_sha256']}
