// GNOME 46 only. Modify background presentation, never authentication.
import St from 'gi://St';
import Gio from 'gi://Gio';
import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {UnlockDialog} from 'resource:///org/gnome/shell/ui/unlockDialog.js';

export default class LabLockscreen extends Extension {
    enable() {
        // GNOME may enable extensions again on a session-mode transition.
        if (this._injections)
            return;
        this._injections = new InjectionManager();
        this._settings = new Gio.Settings({schema_id: 'org.gnome.desktop.screensaver'});
        const settings = this._settings;
        this._injections.overrideMethod(UnlockDialog.prototype, '_createBackground',
            () => function (monitorIndex) {
                const monitor = Main.layoutManager.monitors[monitorIndex];
                const widget = new St.Widget({
                    style_class: 'screen-shield-background',
                    x: monitor.x,
                    y: monitor.y,
                    width: monitor.width,
                    height: monitor.height,
                });
                // Read only a local URI; network is exclusively the updater's job.
                const uri = settings.get_string('picture-uri');
                if (uri.startsWith('file://')) {
                    const escaped = uri.replaceAll('\\', '\\\\').replaceAll('"', '\\"');
                    widget.set_style(`background-color: #09121f; background-image: url("${escaped}"); background-size: contain;`);
                }
                this._backgroundGroup.add_child(widget);
            });
        // These actors have no blur effect. Skip the native blur updater.
        this._injections.overrideMethod(UnlockDialog.prototype,
            '_updateBackgroundEffects', () => function () {});
        this._changedId = this._settings.connect('changed::picture-uri', () => this._refresh());
        this._refresh();
    }

    _refresh() {
        const dialog = Main.screenShield?._dialog;
        if (dialog?._updateBackgrounds)
            dialog._updateBackgrounds();
    }

    disable() {
        if (this._changedId)
            this._settings.disconnect(this._changedId);
        this._changedId = null;
        this._settings = null;
        this._injections?.clear();
        this._injections = null;
        // Restore the ordinary GNOME background and its blur effects.
        this._refresh();
    }
}
