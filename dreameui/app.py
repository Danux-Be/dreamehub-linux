"""Fenêtre de bureau : Qt WebEngine (+ icône de barre système) si disponible, sinon WebKitGTK."""
from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

from .api import AppAPI
from .server import LocalServer

APP_NAME = "Dreame UI"
APP_ID = "io.github.dreameui"
ICON = Path(__file__).parent / "resources" / "assets" / "hub-icon.svg"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "dreameui"
# Port fixe pour garder la même origine (thème, préférences en localStorage) d'un lancement à l'autre.
PREFERRED_PORT = 47823


def run_qt(url: str, start_hidden: bool) -> bool:
    try:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QAction, QColor, QIcon
        from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtWidgets import QApplication, QMainWindow, QMenu, QSystemTrayIcon
    except ImportError:
        return False

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName(APP_ID)
    app.setQuitOnLastWindowClosed(False)
    icon = QIcon(str(ICON))
    app.setWindowIcon(icon)

    profile = QWebEngineProfile("dreameui", app)
    profile.setPersistentStoragePath(str(DATA_DIR / "qtwebengine"))

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self, target, kind, main_frame):
            # L'interface ne navigue jamais ailleurs ; les liens externes s'ouvrent dans le navigateur.
            if target.toString().startswith(url):
                return True
            webbrowser.open(target.toString())
            return False

    class Window(QMainWindow):
        def closeEvent(self, event):
            if QSystemTrayIcon.isSystemTrayAvailable():
                event.ignore()
                self.hide()
            else:
                app.quit()

    window = Window()
    window.setWindowTitle(APP_NAME)
    window.resize(980, 720)
    window.setMinimumSize(700, 520)
    view = QWebEngineView()
    view.setPage(Page(profile, view))
    view.page().setBackgroundColor(QColor("#0b0f14"))
    view.setUrl(QUrl(url))
    window.setCentralWidget(view)

    def toggle():
        if window.isVisible() and window.isActiveWindow():
            window.hide()
        else:
            window.show()
            window.raise_()
            window.activateWindow()

    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(icon, app)
        tray.setToolTip(APP_NAME)
        menu = QMenu()
        show_action = QAction("Afficher / masquer", menu)
        show_action.triggered.connect(toggle)
        quit_action = QAction("Quitter", menu)
        quit_action.triggered.connect(app.quit)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda reason: toggle() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        tray.show()
        window._tray = tray  # garder une référence
    else:
        start_hidden = False
    if not start_hidden:
        window.show()
    app.exec()
    return True


def run_gtk(url: str) -> bool:
    # Le rendu DMA-BUF de WebKitGTK provoque une erreur de protocole Wayland (71) avec les pilotes NVIDIA.
    os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import Gdk, GLib, Gtk, WebKit2
    except (ImportError, ValueError):
        return False

    GLib.set_prgname(APP_ID)
    GLib.set_application_name(APP_NAME)
    manager = WebKit2.WebsiteDataManager(base_data_directory=str(DATA_DIR / "webkit"),
                                         base_cache_directory=str(DATA_DIR / "webkit-cache"))
    context = WebKit2.WebContext.new_with_website_data_manager(manager)
    view = WebKit2.WebView.new_with_context(context)
    settings = view.get_settings()
    settings.set_enable_developer_extras("--debug" in sys.argv)
    settings.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.ALWAYS)
    view.set_background_color(Gdk.RGBA(red=0.043, green=0.059, blue=0.078, alpha=1))

    def decide(_view, decision, kind):
        if kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            target = decision.get_navigation_action().get_request().get_uri()
            if not target.startswith(url):
                decision.ignore()
                webbrowser.open(target)
                return True
        return False

    view.connect("decide-policy", decide)
    view.load_uri(url)

    window = Gtk.Window(title=APP_NAME)
    window.set_default_size(980, 720)
    window.set_size_request(700, 520)
    if ICON.exists():
        window.set_icon_from_file(str(ICON))
    window.add(view)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    Gtk.main()
    return True


def main():
    parser = argparse.ArgumentParser(prog="dreameui", description="Contrôle de robot Dreame pour Linux")
    parser.add_argument("--read-only", action="store_true", help="n'envoie aucune commande au robot")
    parser.add_argument("--browser", action="store_true", help="ouvre l'interface dans le navigateur par défaut")
    parser.add_argument("--server-only", action="store_true", help="démarre uniquement le serveur local")
    parser.add_argument("--hidden", action="store_true", help="démarre réduit dans la barre système (Qt)")
    parser.add_argument("--toolkit", choices=("auto", "qt", "gtk"), default="auto")
    parser.add_argument("--debug", action="store_true", help="active l'inspecteur web")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    api = AppAPI(read_only=args.read_only)
    try:
        server = LocalServer(api, PREFERRED_PORT)
    except OSError:
        server = LocalServer(api)
    server.start()
    threading.Thread(target=api.bootstrap, daemon=True, name="dreameui-bootstrap").start()

    try:
        if args.server_only or args.browser:
            print(f"Dreame UI : {server.url}", flush=True)
            if args.browser:
                webbrowser.open(server.url)
            threading.Event().wait()
        shown = False
        if args.toolkit in ("auto", "qt"):
            shown = run_qt(server.url, args.hidden)
        if not shown and args.toolkit in ("auto", "gtk"):
            shown = run_gtk(server.url)
        if not shown:
            print("Ni Qt WebEngine ni WebKitGTK ne sont disponibles ; ouverture dans le navigateur.", file=sys.stderr)
            webbrowser.open(server.url)
            threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        api.shutdown()
        server.stop()


if __name__ == "__main__":
    main()
