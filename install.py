#!/usr/bin/python3
"""Install Quick Note for the current user (KDE Plasma 6; no sudo).

What installation changes:
  - Creates the chosen Inbox directory if missing. Saves the vault path, Inbox
    path and Git sync preference in quick-note/settings.json under
    the XDG config directory. Installation itself never commits or pushes notes.
  - Copies capture.py to ~/.local/bin/quick-note and records its hash
    beside settings.json so later installs can recognize the executable.
  - Writes a launcher under the XDG data directory's kglobalaccel/ and links
    it into applications/. The launcher disables the startup animation.
  - Refreshes KDE's application cache, registers the selected global shortcut,
    and applies a KWin rule that keeps the capture window out of the taskbar.

For other desktops, replace the KDE launcher, shortcut and window integration;
see capture_screen() in capture.py for active-monitor placement. No autostart
service or system packages are installed. --uninstall removes the executable,
receipt, launchers, shortcut and window rule, but keeps settings, notes and drafts.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

HOME = Path.home()
NAME = 'quick-note'
ID = NAME + '.desktop'
RULE = '640e524f-a5e5-4275-90f3-163f673a426c'
DATA = Path(os.environ.get('XDG_DATA_HOME') or HOME/'.local/share')
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME') or HOME/'.config')/NAME/'settings.json'
BIN = HOME/'.local/bin'/NAME
DESKTOP = DATA/'kglobalaccel'/ID
LINK = DATA/'applications'/ID
BUS = ['busctl', '--user', 'call', 'org.kde.kglobalaccel', '/kglobalaccel', 'org.kde.KGlobalAccel']
ACTION = ['4', ID, '_launch', 'Quick Note', 'Launch']

def call(*args):
    return subprocess.check_output(args, text=True).strip()

KEYS = dict(Description='Hide Quick Note from taskbar', wmclassmatch='0',
            title='Quick Note', titlematch='1', skiptaskbar='true', skiptaskbarrule='2')
KWIN = CONFIG.parent.parent/'kwinrulesrc'
RECEIPT = CONFIG.with_name('installed.sha256')
# The capture.py shipped by the original installer (also used by the local legacy setup).
LEGACY = '3fb14beda8bfe149a3dbc612d445147368cbb48202b7963a695358e4a5799cdc'
MISSING = '__missing_' + RULE


class SetupError(Exception):
    pass


def reject(path):
    raise SetupError(f'Refusing to modify an unrecognized installation or unsafe path: {path}')


def mode(path):
    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return None


def check_parents(path):
    for parent in reversed(path.absolute().parents):
        existing = mode(parent)
        if existing is not None and not stat.S_ISDIR(existing):
            reject(parent)


def snapshot(path, link=False):
    check_parents(path)
    existing = mode(path)
    if existing is None:
        return None
    if link and stat.S_ISLNK(existing):
        return os.readlink(path)
    if not link and stat.S_ISREG(existing):
        return path.read_bytes(), stat.S_IMODE(existing)
    reject(path)


def write(path, value):
    # Replace files rather than truncating them, including when the old file is a hard link.
    check_parents(path)
    if value is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.symlink_to(value)
        return
    content, permissions = value
    file = tempfile.NamedTemporaryFile(dir=path.parent, delete=False)
    temporary = Path(file.name)
    try:
        with file:
            file.write(content)
            os.fchmod(file.fileno(), permissions)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def desktop(key, executable=None):
    executable = executable if executable is not None else f'"{BIN}"'
    return (f'[Desktop Entry]\nType=Application\nName=Quick Note\nExec={executable}\n'
            'Icon=document-new\nStartupNotify=false\nX-KDE-StartupNotify=false\n'
            f'X-KDE-Shortcuts={key}\n').encode()


def installation():
    paths = {path: snapshot(path, path == LINK) for path in (BIN, DESKTOP, LINK, RECEIPT)}
    if not any(value is not None for value in paths.values()):
        return paths, None, False
    binary, launcher, link, receipt = (paths[p] for p in (BIN, DESKTOP, LINK, RECEIPT))
    if binary is None or launcher is None or link is None:
        reject('incomplete app files')
    if os.path.normpath(LINK.parent/link) != str(DESKTOP):
        reject(LINK)
    digest = hashlib.sha256(binary[0]).hexdigest()
    if (receipt is None and digest != LEGACY) or (receipt and receipt[0] != (digest+'\n').encode()):
        reject(BIN)
    match = re.search(rb'(?m)^X-KDE-Shortcuts=([^\r\n]+)\n$', launcher[0])
    if not match:
        reject(DESKTOP)
    key = match[1].decode()
    if launcher[0] != desktop(key) and (receipt or launcher[0] != desktop(key, str(BIN))):
        reject(DESKTOP)
    return paths, key, receipt is None


def read_rule(installed):
    raw = snapshot(KWIN)
    wanted = f'[{RULE}]'.encode()
    entries = []
    in_group = False
    seen = False
    for line in raw[0].splitlines() if raw else ():
        line = line.strip()
        if line.startswith(b'['):
            in_group = line.startswith(wanted)
            if in_group and (seen or line != wanted):
                reject(f'KWin rule {RULE}')
            seen |= in_group
        elif in_group and line and not line.startswith(b'#'):
            entries.append(line)
    order = call('kreadconfig6', '--file', KWIN, '--group', 'General', '--key', 'Order', '--default', MISSING)
    order = None if order == MISSING else order
    count = (order or '').split(',').count(RULE)
    if seen or count:
        expected = sorted(f'{key}={value}'.encode() for key, value in KEYS.items())
        if not installed or count != 1 or sorted(entries) != expected:
            reject(f'KWin rule {RULE}')
    return seen, order


def set_rule(state):
    enabled, order = state
    check_parents(KWIN)
    if mode(KWIN) is not None and not stat.S_ISREG(mode(KWIN)):
        reject(KWIN)
    for group, entries in ((RULE, {key: value if enabled else None for key, value in KEYS.items()}),
                           ('General', {'Order': order})):
        for key, value in entries.items():
            call('kwriteconfig6', '--file', KWIN, '--group', group, '--key', key,
                 '--delete' if value is None else value)
    call('busctl', '--user', 'call', 'org.kde.KWin', '/KWin', 'org.kde.KWin', 'reconfigure')


def read_shortcut(installed):
    actions = shlex.split(call(*BUS, 'allActionsForComponent', 'as', *ACTION))
    if actions == ['aas', '0']:
        return None
    if not installed or actions != ['aas', '1', *ACTION]:
        reject(f'KDE shortcut {ID}')
    return tuple(call(*BUS, method, 'as', *ACTION).split()[1:]
                 for method in ('shortcutKeys', 'defaultShortcutKeys'))


def restore_shortcut(previous):
    if previous is None:
        call(*BUS, 'unregister', 'ss', ID, '_launch')
        return
    call(*BUS, 'doRegister', 'as', *ACTION)
    for keys, flags in zip(previous, ('4', '8')):
        result = call(*BUS, 'setShortcutKeys', 'asa(ai)u', *ACTION, *keys, flags)
        if result.split() != ['a(ai)', *keys]:
            raise SetupError('KDE did not restore the previous shortcut.')


def apply(before, after, old_rule, new_rule, old_shortcut, code=None):
    changed = []
    rule_attempted = False
    cache_attempted = False
    try:
        for path, value in after.items():
            if value != before[path]:
                # An existing link is only ever removed, not replaced.
                write(path, value)
                changed.append(path)
        cache_attempted = True
        call('kbuildsycoca6')
        if old_rule != new_rule:
            rule_attempted = True
            set_rule(new_rule)
        if code is None:
            if old_shortcut is not None:
                call(*BUS, 'unregister', 'ss', ID, '_launch')
        else:
            call(*BUS, 'doRegister', 'as', *ACTION)
            if call(*BUS, 'setShortcut', 'asaiu', *ACTION, '1', code, '4') != 'ai 1 '+code:
                raise SetupError('KDE did not register the requested shortcut.')
    except (OSError, subprocess.SubprocessError, SetupError, KeyboardInterrupt):
        # Restore files before rebuilding KDE's cache and restoring the live shortcut.
        undo = [(write, path, before[path]) for path in reversed(changed)]
        if rule_attempted:
            undo.append((set_rule, old_rule))
        if cache_attempted:
            undo.extend([(call, 'kbuildsycoca6'), (restore_shortcut, old_shortcut)])
        for restore, *args in undo:
            try:
                restore(*args)
            except (OSError, subprocess.SubprocessError, SetupError) as error:
                print(f'Rollback step failed ({restore.__name__}): {error}', file=sys.stderr)
        raise


def settings(value, legacy):
    if value is None:
        if legacy:
            vault = HOME/'Documents/Zettelkasten'
            return dict(vault=str(vault), inbox=str(vault/'00 Inbox'), sync=True)
        return {}
    try:
        data = json.loads(value[0])
        if (isinstance(data, dict) and data.keys() == {'vault', 'inbox', 'sync'}
                and all(isinstance(data[key], str) and Path(data[key]).is_absolute() for key in ('vault', 'inbox'))
                and isinstance(data['sync'], bool)
                and Path(data['inbox']) != Path(data['vault'])
                and Path(data['inbox']).is_relative_to(data['vault'])):
            return data
    except (ValueError, UnicodeError):
        pass
    reject(CONFIG)


def prompt(label, default):
    return input(f'{label} [{default}]: ' if default else f'{label}: ').strip() or default


def shortcut_prompt(previous_key, shortcut, key_name):
    if previous_key is None:
        return prompt('Global shortcut', 'Meta+Space')
    if shortcut:
        active = shortcut[0]
        if active[:2] == ['1', '4'] and active[3:] == ['0', '0', '0'] and int(active[2]) != 0:
            name = key_name(int(active[2]))
            if name:
                return prompt('Global shortcut', name)
    return prompt('KDE shortcut is disabled or uses custom bindings; enter a replacement to continue', '')


def setup_inputs(parser, defaults, previous_key, shortcut):
    from PySide6.QtCore import QKeyCombination
    from PySide6.QtGui import QKeySequence
    from PySide6 import QtWidgets, QtDBus  # also required by the app
    if not os.getenv('DISPLAY'):
        parser.error('X11 or XWayland is required.')
    raw = prompt('Existing vault directory', defaults.get('vault', ''))
    if not raw:
        parser.error('A vault path is required.')
    vault = Path(raw).expanduser().resolve()
    default_inbox = (str(Path(defaults['inbox']).relative_to(defaults['vault'])) if defaults else '00 Inbox')
    inbox = (vault/prompt('Inbox directory relative to vault', default_inbox)).resolve()
    if not vault.is_dir() or inbox == vault or not inbox.is_relative_to(vault):
        parser.error('Invalid vault or Inbox directory.')
    sync_default = defaults.get('sync', False)
    answer = input('Enable /sync? It stages and commits ALL vault changes, including deletions, then pushes. '
                   + ('[Y/n] ' if sync_default else '[y/N] ')).strip().lower()
    sync = answer in ('y', 'yes') if answer else sync_default
    if sync:
        if not shutil.which('notify-send') or not shutil.which('git'):
            parser.error('Sync requires Git and notify-send.')
        if Path(call('git', '-C', vault, 'rev-parse', '--show-toplevel')).resolve() != vault:
            parser.error('Choose the Git repository root as the vault.')
        call('git', '-C', vault, 'rev-parse', '--abbrev-ref', '@{u}')
    def key_name(code):
        return QKeySequence(QKeyCombination.fromCombined(code)).toString()

    while True:
        key = shortcut_prompt(previous_key, shortcut, key_name)
        if not key:
            continue
        seq = QKeySequence.fromString(key)
        if seq.count() != 1 or not seq.toString():
            print('Enter one key combination.')
            continue
        code = str(seq[0].toCombined())
        owner = call(*BUS, 'getGlobalShortcutsByKey', 'i', code)
        fields = shlex.split(owner)
        if fields[1:] == ['0'] or (previous_key and fields[1:8] == [
                '1', '_launch', 'Launch', ID, 'Quick Note', 'default', 'Default Context']):
            return dict(vault=str(vault), inbox=str(inbox), sync=sync), seq.toString(), code
        print('Already assigned; choose another shortcut.')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, epilog=(
        'Close KDE settings and avoid concurrent installs while this runs. Rollback is best effort if '
        'the desktop session stops responding; newly created directories may remain.'))
    parser.add_argument('--uninstall', action='store_true', help='remove the app and KDE integration; keep settings, notes and drafts')
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('Run as your desktop user, not root.')
    try:
        before, previous_key, legacy = installation()
        if args.uninstall and previous_key is None:
            return
        for tool in ('busctl', 'kreadconfig6', 'kwriteconfig6', 'kbuildsycoca6'):
            if not shutil.which(tool):
                parser.error('Missing dependency: ' + tool)
        call('busctl', '--user', 'call', 'org.kde.KWin', '/KWin', 'org.kde.KWin', 'activeOutputName')
        old_rule = read_rule(previous_key is not None)
        old_shortcut = read_shortcut(previous_key is not None)
        if args.uninstall:
            order = ','.join(i for i in old_rule[1].split(',') if i != RULE) if old_rule[0] else old_rule[1]
            apply(before, dict.fromkeys((LINK, DESKTOP, BIN, RECEIPT)), old_rule, (False, order), old_shortcut)
            return
        before[CONFIG] = snapshot(CONFIG)
        defaults = settings(before[CONFIG], legacy)
        data, key, code = setup_inputs(parser, defaults, previous_key, old_shortcut)
        source = Path(__file__).with_name('capture.py')
        content = source.read_bytes()
        config = before[CONFIG]
        if config is None or defaults != data:
            config = ((json.dumps(data, indent=2)+'\n').encode(), config[1] if config else 0o600)
        after = {BIN: (content, stat.S_IMODE(source.stat().st_mode)), CONFIG: config,
                 DESKTOP: (desktop(key), before[DESKTOP][1] if before[DESKTOP] else 0o644),
                 LINK: before[LINK] or str(DESKTOP), RECEIPT: ((hashlib.sha256(content).hexdigest()+'\n').encode(), 0o600)}
        Path(data['inbox']).mkdir(parents=True, exist_ok=True)
        new_rule = old_rule if old_rule[0] else (True, ','.join(filter(None, (old_rule[1], RULE))))
        apply(before, after, old_rule, new_rule, old_shortcut, code)
        print('Installed. Press '+key+' to capture a note.')
    except (OSError, subprocess.SubprocessError, ValueError, SetupError, KeyboardInterrupt, EOFError) as error:
        parser.exit(1, f'Setup failed: {error or type(error).__name__}\n')


if __name__ == '__main__':
    main()
