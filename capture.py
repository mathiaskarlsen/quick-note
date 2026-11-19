#!/usr/bin/python3
from pathlib import Path
from datetime import datetime, timezone
import subprocess
import json
import fcntl
import itertools
import os
import re
import sys
from tempfile import NamedTemporaryFile
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QVBoxLayout, QPlainTextEdit, QLabel
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QCursor

CONFIG = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config') / 'quick-note/settings.json'
STATE = Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state') / 'quick-note'
DRAFT = STATE/'draft.txt'
TEMP_PREFIX = '.quick-note-'
TEMP_SUFFIX = '.tmp'


def write_temp(text, folder):
    file = NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder,
                              prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX, delete=False)
    path = Path(file.name)
    try:
        with file:
            file.write(text)
    except (OSError, UnicodeError):
        path.unlink(missing_ok=True)
        raise
    return path


def save(text, folder):
    title = re.sub(r'[\\/:*?"<>|\[\]#^\x00-\x1f]', '', ' '.join(text.split())).strip('. ') or 'Note'
    title = title.encode()[:210].decode(errors='ignore').rstrip()
    stem = datetime.now().strftime('%Y%m%d%H%M ') + title
    temp = write_temp(text, folder)
    try:
        for n in itertools.count(1):
            path = folder / (stem + (f' ({n})' if n > 1 else '') + '.md')
            try:
                # A hard link publishes the completed file without replacing a collision.
                os.link(temp, path)
            except FileExistsError:
                continue
            return path
    finally:
        try:
            temp.unlink()
        except OSError as err:
            # Cleanup failure must not make a published note look unsaved.
            print(f'Cannot remove temporary note {temp}: {err}', file=sys.stderr)


def keep_draft(text, draft):
    if not text:
        draft.unlink(missing_ok=True)
        return
    temp = write_temp(text, draft.parent)
    try:
        temp.replace(draft)
    except OSError:
        temp.unlink(missing_ok=True)
        raise


def capture_screen(app):
    from PySide6.QtDBus import QDBusConnection, QDBusMessage
    request = QDBusMessage.createMethodCall('org.kde.KWin', '/KWin', 'org.kde.KWin', 'activeOutputName')
    reply = QDBusConnection.sessionBus().call(request, timeout=500)
    if reply.type() == QDBusMessage.ReplyMessage and reply.arguments():
        for screen in app.screens():
            if screen.name() == reply.arguments()[0]:
                return screen
    return app.screenAt(QCursor.pos()) or app.primaryScreen()

def notify_sync(message):
    subprocess.run(['notify-send', 'Quick Note sync', message], check=False)


def git(vault, *args, allowed=(0,)):
    try:
        result = subprocess.run(['git', *args], cwd=vault,
            env=dict(os.environ, GIT_TERMINAL_PROMPT='0'), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, errors='replace', timeout=120)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise RuntimeError(f'Git {args[0]} failed: {err}') from err
    if result.returncode not in allowed:
        detail = (result.stderr.strip() or result.stdout.strip())[-600:]
        raise RuntimeError(f'Git {args[0]} failed: {detail or result.returncode}')
    return result.returncode


def sync_repo(settings, state):
    if not settings.get('sync'):
        return 1
    vault = Path(settings['vault'])
    with (state/'sync.lock').open('a') as sync_lock:
        try:
            fcntl.flock(sync_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            notify_sync('Git sync is already running.')
            return 0
        try:
            git(vault, 'pull', '--ff-only')
            # Captures can be writing a note while the worker stages the vault.
            temporary = f':(exclude,glob)**/{TEMP_PREFIX}*{TEMP_SUFFIX}'
            git(vault, 'add', '-A', '--', '.', temporary)
            if git(vault, 'diff', '--cached', '--quiet', allowed=(0, 1)) == 1:
                timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
                host = os.uname().nodename.split('.')[0]
                git(vault, 'commit', '-m', f'vault sync {timestamp} from {host}')
            git(vault, 'push')
        except RuntimeError as err:
            notify_sync(str(err))
            return 1
        notify_sync('Pull and push completed.')
        return 0


class Capture(QDialog):
    def __init__(self, settings, draft):
        super().__init__(None, Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.inbox = Path(settings['inbox'])
        self.sync_enabled = settings.get('sync')
        self.draft = draft
        self.closing = False
        self.submitted = False
        self.setup_ui()

    def show_hint(self, message):
        self.hint.setText(message)
        self.hint.show()

    def reject(self):
        if self.closing:
            return
        if self.submitted:
            self.finish_submission()
            return
        try:
            keep_draft(self.editor.toPlainText(), self.draft)
        except (OSError, UnicodeError) as err:
            self.show_hint('Draft not kept: ' + str(err))
            return
        self.closing = True
        super().reject()

    def submit(self):
        text = self.editor.toPlainText()
        if self.closing or not text.strip():
            return
        if not self.submitted:
            try:
                if text == '/sync':
                    if not self.sync_enabled:
                        self.show_hint('Git sync is disabled.')
                        return
                    subprocess.Popen([sys.executable, __file__, '--sync'], start_new_session=True)
                else:
                    save(text, self.inbox)
            except (OSError, UnicodeError) as err:
                self.show_hint('Not saved: ' + str(err))
                return
            self.submitted = True
        self.finish_submission()

    def finish_submission(self):
        try:
            self.draft.unlink(missing_ok=True)
        except OSError as err:
            self.editor.setReadOnly(True)
            self.show_hint('Submitted, but could not remove draft. Press Enter to retry: ' + str(err))
            return
        self.closing = True
        self.accept()

    def event(self, event):
        if event.type() == QEvent.WindowDeactivate and self.isVisible() and not self.closing:
            self.reject()
        return super().event(event)

    def setup_ui(self):
        self.setWindowTitle('Quick Note')
        self.setAttribute(Qt.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName('capturePanel')
        outer.addWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(11, 11, 11, 11)
        self.editor = Editor(self.submit)
        self.editor.setPlaceholderText('Write a note…')
        layout.addWidget(self.editor)
        self.hint = QLabel()
        layout.addWidget(self.hint)
        self.hint.hide()
        self.setStyleSheet('QFrame#capturePanel {background:#24272b;border-radius:8px;}'
                           'QPlainTextEdit {background:#17191c;color:#f1f3f5;border:1px solid #4397ba;'
                           'border-radius:5px;padding:7px;font-size:16px;}'
                           'QLabel {color:#bbc3ca;}')
        try:
            self.editor.setPlainText(self.draft.read_text(encoding='utf-8'))
        except FileNotFoundError:
            pass
        self.editor.moveCursor(self.editor.textCursor().MoveOperation.End)


class Editor(QPlainTextEdit):
    def __init__(self, submit):
        super().__init__()
        self.submit = submit

    def keyPressEvent(self, event):
        modifiers = event.modifiers() & ~Qt.KeypadModifier
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not modifiers:
            self.submit()
            return
        super().keyPressEvent(event)


if __name__ == '__main__':
    os.environ['QT_QPA_PLATFORM'] = 'xcb'  # absolute placement via XWayland
    settings = json.loads(CONFIG.read_text())
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if sys.argv[1:] == ['--sync']:
        sys.exit(sync_repo(settings, STATE))
    lock = (STATE/'lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(0)
    app = QApplication(sys.argv)
    dialog = Capture(settings, DRAFT)
    screen = capture_screen(app)
    dialog.setScreen(screen)
    area = screen.availableGeometry()
    dialog.resize(min(640, area.width()-24), 108); dialog.move(area.center().x()-dialog.width()//2, area.top()+12)
    dialog.show()
    dialog.editor.setFocus()
    dialog.raise_()
    dialog.activateWindow()
    app.exec()
