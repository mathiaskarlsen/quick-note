#!/usr/bin/python3
"""Install Inbox Quick Capture in the current user's Plasma 6 session."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

HOME = Path.home()
NAME = 'zettel-inbox-capture'
ID = NAME + '.desktop'
RULE = '640e524f-a5e5-4275-90f3-163f673a426c'
DATA = Path(os.environ.get('XDG_DATA_HOME') or HOME/'.local/share')
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME') or HOME/'.config')/NAME/'settings.json'
BIN = HOME/'.local/bin'/NAME
DESKTOP = DATA/'kglobalaccel'/ID
LINK = DATA/'applications'/ID
BUS = ['busctl', '--user', 'call', 'org.kde.kglobalaccel', '/kglobalaccel', 'org.kde.KGlobalAccel']
ACTION = ['4', ID, '_launch', 'Inbox Quick Capture', 'Launch']

def call(*args):
    return subprocess.check_output(args, text=True).strip()

def rule(enable):
    base = ['--file', 'kwinrulesrc']
    order = call('kreadconfig6', *base, '--group', 'General', '--key', 'Order').split(',')
    ids = [i for i in order if i and i != RULE]
    keys = dict(Description='Hide Inbox Quick Capture from taskbar', wmclassmatch='0',
                title='Zettelkasten Inbox Quick Capture', titlematch='1', skiptaskbar='true', skiptaskbarrule='2')
    for key, value in keys.items():
        call('kwriteconfig6', *base, '--group', RULE, '--key', key, *([value] if enable else ['--delete']))
    if enable: ids.append(RULE)
    call('kwriteconfig6', *base, '--group', 'General', '--key', 'Order', ','.join(ids))
    call('busctl', '--user', 'call', 'org.kde.KWin', '/KWin', 'org.kde.KWin', 'reconfigure')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uninstall', action='store_true', help='remove the app and KDE integration; keep settings, notes and drafts')
    args = parser.parse_args()
    if os.geteuid() == 0: parser.error('Run as your desktop user, not root.')
    for tool in ('busctl', 'kreadconfig6', 'kwriteconfig6', 'kbuildsycoca6'):
        if not shutil.which(tool): parser.error('Missing dependency: ' + tool)
    call('busctl', '--user', 'call', 'org.kde.KWin', '/KWin', 'org.kde.KWin', 'activeOutputName')
    if (LINK.exists() or LINK.is_symlink()) and not (LINK.is_symlink() and LINK.resolve()==DESKTOP.resolve()):
        parser.error(f'Refusing to replace unrelated launcher: {LINK}')
    if args.uninstall:
        call(*BUS, 'unregister', 'ss', ID, '_launch')
        rule(False)
        for path in (LINK, DESKTOP, BIN): path.unlink(missing_ok=True)
        call('kbuildsycoca6'); return
    from PySide6.QtGui import QKeySequence
    from PySide6 import QtWidgets, QtDBus  # also required by the app
    if not os.getenv('DISPLAY'): parser.error('X11 or XWayland is required.')
    raw = input('Existing vault directory: ').strip()
    if not raw: parser.error('A vault path is required.')
    vault = Path(raw).expanduser().resolve()
    inbox = (vault/(input('Inbox directory relative to vault [00 Inbox]: ').strip() or '00 Inbox')).resolve()
    if not vault.is_dir() or inbox == vault or not inbox.is_relative_to(vault): parser.error('Invalid vault or Inbox directory.')
    sync = input('Enable /sync? It stages and commits ALL vault changes, including deletions, then pushes. [y/N] ').lower() in ('y','yes')
    if sync:
        if not shutil.which('notify-send') or not shutil.which('git'): parser.error('Sync requires Git and notify-send.')
        if Path(call('git','-C',vault,'rev-parse','--show-toplevel')).resolve()!=vault: parser.error('Choose the Git repository root as the vault.')
        call('git','-C',vault,'rev-parse','--abbrev-ref','@{u}')
    while True:
        key = input('Global shortcut [Meta+Space]: ').strip() or 'Meta+Space'
        seq = QKeySequence.fromString(key)
        if seq.count()!=1 or not seq.toString(): print('Enter one key combination.'); continue
        code = str(seq[0].toCombined()); owner = call(*BUS,'getGlobalShortcutsByKey','i',code)
        if owner.endswith(') 0') or ('"'+ID+'"') in owner: break
        print('Already assigned; choose another shortcut.')
    for path in (BIN, CONFIG, DESKTOP, LINK): path.parent.mkdir(parents=True,exist_ok=True)
    inbox.mkdir(parents=True,exist_ok=True)
    CONFIG.write_text(json.dumps(dict(vault=str(vault),inbox=str(inbox),sync=sync),indent=2)+'\n')
    shutil.copy2(Path(__file__).with_name('capture.py'), BIN)
    DESKTOP.write_text(f'[Desktop Entry]\nType=Application\nName=Inbox Quick Capture\nExec="{BIN}"\nIcon=document-new\nStartupNotify=false\nX-KDE-StartupNotify=false\nX-KDE-Shortcuts={seq.toString()}\n')
    if not LINK.is_symlink(): LINK.symlink_to(DESKTOP)
    call('kbuildsycoca6'); rule(True)
    call(*BUS,'doRegister','as',*ACTION)
    if call(*BUS,'setShortcut','asaiu',*ACTION,'1',code,'4') != 'ai 1 '+code: parser.error('KDE did not register the requested shortcut.')
    print('Installed. Press '+seq.toString()+' to capture a note.')

if __name__ == '__main__':
    main()
