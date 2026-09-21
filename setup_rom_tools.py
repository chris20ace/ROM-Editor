"""Install a per-user Windows ROM toolchain from official MSYS2 and pret sources.

Run once with the same Python used by the editor. No administrator access,
system PATH changes, WSL installation, or changes to the game source are needed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parent
MSYS_RELEASE = '2026-06-11'
MSYS_FILE = 'msys2-base-x86_64-20260611.sfx.exe'
MSYS_URL = f'https://github.com/msys2/msys2-installer/releases/download/{MSYS_RELEASE}/{MSYS_FILE}'
AGBCC_COMMIT = 'da598c1d918402c42c0c0d7128ba14567f3175e9'
PNG_VERSION = '1.6.58'
PNG_SHA256 = '28eb403f51f0f7405249132cecfe82ea5c0ef97f1b32c5a65828814ae0d34775'
BINUTILS_VERSION = '2.46.1-1'
BINUTILS_URL = f'https://repo.msys2.org/mingw/ucrt64/mingw-w64-ucrt-x86_64-arm-none-eabi-binutils-{BINUTILS_VERSION}-any.pkg.tar.zst'


def run(args, **kwargs):
    print('Running: ' + ' '.join(map(str, args)), flush=True)
    with subprocess.Popen(list(map(str, args)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding='utf-8', errors='replace',
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), **kwargs) as process:
        for line in process.stdout:
            print(line, end='', flush=True)
        if process.wait():
            raise subprocess.CalledProcessError(process.returncode, args)


def download(url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'EmeraldWorkbench'}), timeout=120) as response:
        with path.open('wb') as output:
            shutil.copyfileobj(response, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('base', 'packages', 'png', 'compiler', 'config', 'all'), default='all')
    args = parser.parse_args()
    if os.name != 'nt':
        raise SystemExit('This installer configures the portable Windows toolchain.')
    base = Path(os.environ['LOCALAPPDATA']) / 'EmeraldWorkbench'
    tools = base / 'tools'
    tools.mkdir(parents=True, exist_ok=True)
    msys = tools / 'msys64'
    bash = msys / 'usr/bin/bash.exe'
    env = dict(os.environ, MSYSTEM='MSYS', CHERE_INVOKING='1', MSYS2_PATH_TYPE='strict')

    def shell(script, *params, cwd=tools):
        run([bash, '--noprofile', '--norc', '-c', 'export PATH=/ucrt64/bin:/usr/bin:/bin; ' + script, 'rom-setup', *params], cwd=cwd, env=env)

    if args.phase in ('base', 'all'):
        if not bash.is_file():
            archive = base / 'downloads' / MSYS_FILE
            checksum = archive.with_suffix('.exe.sha256')
            download(MSYS_URL + '.sha256', checksum)
            expected = checksum.read_text(encoding='utf-8').split()[0].lower()
            if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
                print('Downloading the official MSYS2 base archive…', flush=True)
                download(MSYS_URL, archive)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
                raise SystemExit('MSYS2 archive checksum did not match its published checksum.')
            run([archive, '-y', '-o' + str(tools)])
            run([bash, '--login', '-c', 'true'], cwd=tools, env=env)
        print('MSYS2 base is available.', flush=True)

    if args.phase in ('packages', 'all'):
        # MSYS2 core updates can exit their own shell; running both upgrade
        # passes is the documented restart boundary before installing packages.
        shell('pacman --noconfirm -Syu')
        shell('pacman --noconfirm -Syu')
        shell('pacman --noconfirm -S --needed make gcc zlib-devel pkgconf diffutils git')
        # Native Windows ld 2.47 incorrectly treats separate INCLUDE scripts as
        # duplicates (upstream fix ab1830f633107b180b46938003e6f49fb44cb17d).
        # pacman verifies the official archived package's signature as usual.
        shell('pacman --noconfirm -U "$1"', BINUTILS_URL)

    if args.phase in ('png', 'all'):
        # The game uses the MSYS host compiler, so libpng must be built for
        # MSYS too (the MinGW libpng package targets a different runtime).
        archive = base / 'downloads' / f'libpng-{PNG_VERSION}.tar.xz'
        if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != PNG_SHA256:
            download(f'https://download.sourceforge.net/libpng/libpng-{PNG_VERSION}.tar.xz', archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != PNG_SHA256:
            raise SystemExit('libpng archive checksum did not match the official published checksum.')
        png_source = tools / f'libpng-{PNG_VERSION}'
        if not png_source.is_dir():
            shell('tar --force-local -xf "$1"', archive.as_posix())
        shell('./configure --prefix=/usr && make -j2 check && make install', cwd=png_source)

    compiler_source = tools / 'agbcc-source'
    install_root = tools / 'agbcc-install'
    git = msys / 'usr/bin/git.exe'
    if args.phase in ('compiler', 'all'):
        if not compiler_source.exists():
            run([git, '-c', 'core.autocrlf=false', 'clone', '--no-checkout', 'https://github.com/pret/agbcc.git', compiler_source])
        if not (compiler_source / 'build.sh').exists():
            run([git, '-C', compiler_source, '-c', 'core.autocrlf=false', 'checkout', AGBCC_COMMIT])
        revision = subprocess.check_output([str(git), '-C', str(compiler_source), 'rev-parse', 'HEAD'], text=True).strip()
        if revision != AGBCC_COMMIT:
            raise SystemExit('The existing agbcc checkout differs from the pinned version; it was left unchanged.')
        install_root.mkdir(parents=True, exist_ok=True)
        shell('./build.sh', cwd=compiler_source)
        shell('./install.sh "$1"', install_root.as_posix(), cwd=compiler_source)

    if args.phase in ('config', 'all'):
        agbcc = install_root / 'tools/agbcc'
        required = [bash, msys / 'usr/bin/make.exe', msys / 'usr/bin/gcc.exe',
                    msys / 'ucrt64/bin/arm-none-eabi-as.exe', msys / 'usr/lib/libpng.a',
                    agbcc / 'bin/agbcc.exe', agbcc / 'bin/old_agbcc.exe', agbcc / 'bin/agbcc_arm.exe',
                    agbcc / 'lib/libc.a', agbcc / 'lib/libgcc.a']
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise SystemExit('Tool setup is incomplete: ' + ', '.join(missing))
        # Resolve any packaged-app filesystem redirection so the same config
        # also works when Start Workbench is launched outside that app.
        config = {'bash': str(bash.resolve()), 'make': str((msys / 'usr/bin/make.exe').resolve()),
                  'agbcc': str(agbcc.resolve()), 'bin_dirs': [str((msys / 'ucrt64/bin').resolve()), str((msys / 'usr/bin').resolve())],
                  'build_root': str((base / 'builds').resolve()), 'msys_release': MSYS_RELEASE,
                  'agbcc_commit': AGBCC_COMMIT, 'libpng_version': PNG_VERSION,
                  'binutils_version': BINUTILS_VERSION}
        target = ROOT / '.workbench/rom-toolchain.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
        print('ROM toolchain ready. Configuration: ' + str(target), flush=True)


if __name__ == '__main__':
    main()
