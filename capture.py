#!/usr/bin/python3
from pathlib import Path
from datetime import datetime, timezone
import subprocess
import json
import fcntl
import itertools, re, os, sys
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QVBoxLayout, QPlainTextEdit, QLabel
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QCursor

CONFIG = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config') / 'zettel-inbox-capture/settings.json'
settings = json.loads(CONFIG.read_text())
VAULT = Path(settings['vault'])
INBOX = Path(settings['inbox'])
def save(text, folder=INBOX):
    title = re.sub(r'[\\/:*?"<>|\[\]#^\x00-\x1f]', '', ' '.join(text.split())).strip('. ') or 'Note'
    title = title.encode()[:210].decode(errors='ignore').rstrip()
    stem = datetime.now().strftime('%Y%m%d%H%M ') + title
    for n in itertools.count(1):
        path = folder / (stem + (f' ({n})' if n > 1 else '') + '.md')
        try:
            f = path.open('x', encoding='utf-8')
        except FileExistsError:
            continue
        with f:
            f.write(text)
        return path

def capture_screen(app):
    from PySide6.QtDBus import QDBusConnection, QDBusMessage
    request = QDBusMessage.createMethodCall('org.kde.KWin', '/KWin', 'org.kde.KWin', 'activeOutputName')
    reply = QDBusConnection.sessionBus().call(request, timeout=500)
    if reply.type() == QDBusMessage.ReplyMessage and reply.arguments():
        for screen in app.screens():
            if screen.name() == reply.arguments()[0]:
                return screen
    return app.screenAt(QCursor.pos()) or app.primaryScreen()

STATE = Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state') / 'zettel-inbox-capture'
DRAFT = STATE/'draft.txt'

def sync_repo():
    if not settings.get("sync"): return 1
    with (STATE/'sync.lock').open('a') as sync_lock:
        try: fcntl.flock(sync_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return 0
        message = 'Pull and push completed.'
        ok = True
        for args in [('pull', '--ff-only'), ('add', '-A', '--', '.'), ('diff', '--cached', '--quiet'), ('commit', '-m', 'vault sync ' + datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z') + ' from ' + os.uname().nodename.split('.')[0]), ('push',)]:
            if args[0] == 'commit' and not changed: continue
            try:
                result = subprocess.run(['git', *args], cwd=VAULT,
                    env=dict(os.environ, GIT_TERMINAL_PROMPT='0'), stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, errors='replace', timeout=120)
                if args[0] == 'diff' and result.returncode in (0, 1):
                    changed = bool(result.returncode); continue
                if result.returncode:
                    message = f'Git {args[0]} failed: ' + (result.stderr or result.stdout).strip()[-600:]
                    ok = False; break
            except (OSError, subprocess.TimeoutExpired) as err:
                message = f'Git {args[0]} failed: {err}'; ok = False; break
        subprocess.run(['notify-send', 'Zettelkasten sync', message], check=False)
        return 0 if ok else 1

class Capture(QDialog):
    closing = False
    def reject(self):
        if self.closing: return
        try:
            text = editor.toPlainText()
            if text:
                tmp = STATE/'draft.tmp'
                tmp.write_text(text, encoding='utf-8'); tmp.replace(DRAFT)
            else: DRAFT.unlink(missing_ok=True)
        except OSError as err:
            hint.setText('Draft not kept: ' + str(err)); hint.show(); return
        self.closing = True
        super().reject()

    def event(self, event):
        if event.type() == QEvent.WindowDeactivate and self.isVisible() and not self.closing:
            self.reject()
        return super().event(event)

class Editor(QPlainTextEdit):
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers():
            text = self.toPlainText()
            if text.strip():
                try:
                    if text == '/sync':
                        if not settings.get('sync'):
                            hint.setText('Git sync is disabled.'); hint.show(); return
                        subprocess.Popen([sys.executable, __file__, '--sync'], start_new_session=True)
                    else: save(text)
                except OSError as err: hint.setText('Not saved: ' + str(err)); hint.show()
                else:
                    dialog.closing = True
                    DRAFT.unlink(missing_ok=True)
                    dialog.accept()
            return
        super().keyPressEvent(event)

if __name__ == '__main__':
    os.environ['QT_QPA_PLATFORM'] = 'xcb'  # absolute placement via XWayland
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if sys.argv[1:] == ['--sync']: sys.exit(sync_repo())
    lock = (STATE/'lock').open('a')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: sys.exit(0)
    app = QApplication(sys.argv)
    dialog = Capture(None, Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    dialog.setWindowTitle('Zettelkasten Inbox Quick Capture')
    dialog.setAttribute(Qt.WA_TranslucentBackground)
    outer = QVBoxLayout(dialog); outer.setContentsMargins(0, 0, 0, 0)
    panel = QFrame(); panel.setObjectName("capturePanel"); outer.addWidget(panel)
    layout = QVBoxLayout(panel); layout.setContentsMargins(11, 11, 11, 11)
    editor = Editor(); editor.setPlaceholderText('Write a note…'); layout.addWidget(editor)
    if DRAFT.exists():
        editor.setPlainText(DRAFT.read_text(encoding='utf-8'))
        editor.moveCursor(editor.textCursor().MoveOperation.End)
    hint = QLabel(); layout.addWidget(hint); hint.hide()
    dialog.setStyleSheet('QFrame#capturePanel {background:#24272b;border-radius:8px;} QPlainTextEdit {background:#17191c;color:#f1f3f5;border:1px solid #4397ba;border-radius:5px;padding:7px;font-size:16px;} QLabel {color:#bbc3ca;}')
    screen = capture_screen(app)
    dialog.setScreen(screen)
    area = screen.availableGeometry()
    dialog.resize(min(640, area.width()-24), 108); dialog.move(area.center().x()-dialog.width()//2, area.top()+12)
    dialog.show(); editor.setFocus(); dialog.raise_(); dialog.activateWindow(); app.exec()
