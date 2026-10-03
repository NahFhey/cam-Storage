// Shared UI helpers: escaping, formatting, icons, toasts, dialogs, keypad, tables.
// Load order on every page: ui.js, api.js, layout.js, auth.js, then the page script.

(function (global) {
    'use strict';

    // ========== Escaping ==========

    var ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

    /** Escape any value for safe interpolation into HTML text or attributes. */
    function esc(value) {
        if (value === null || value === undefined) return '';
        return String(value).replace(/[&<>"']/g, function (c) { return ESCAPES[c]; });
    }

    // ========== Icons (24x24 stroke) ==========

    var ICON_PATHS = {
        scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M7 12h10"/>',
        flame: '<path d="M12 22c4 0 7-2.7 7-7 0-3.5-2.5-6.5-4-8 0 2-1 3.5-2.5 4C12.5 7.5 11 4 8 2c.5 3-1.5 5.5-3 7.5A7.6 7.6 0 0 0 5 15c0 4.3 3 7 7 7z"/>',
        search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
        chart: '<path d="M3 3v18h18"/><path d="M7 15v2M11 11v6M15 7v10M19 12v5"/>',
        settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
        undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
        x: '<path d="M18 6 6 18M6 6l12 12"/>',
        check: '<path d="M20 6 9 17l-5-5"/>',
        arrowLeft: '<path d="M19 12H5M12 19l-7-7 7-7"/>',
        arrowRight: '<path d="M5 12h14M12 5l7 7-7 7"/>',
        chevronRight: '<path d="m9 18 6-6-6-6"/>',
        refresh: '<path d="M21 12a9 9 0 0 1-15.5 6.2L3 16M3 12a9 9 0 0 1 15.5-6.2L21 8"/><path d="M21 3v5h-5M3 21v-5h5"/>',
        logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
        user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
        users: '<circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0 1 14 0M16 3.1a4 4 0 0 1 0 7.8M22 21a7 7 0 0 0-4-6.3"/>',
        sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
        moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
        download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
        upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
        plus: '<path d="M12 5v14M5 12h14"/>',
        trash: '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
        grip: '<circle cx="9" cy="6" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="18" r="1"/>',
        alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
        info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
        checkCircle: '<circle cx="12" cy="12" r="10"/><path d="m8 12 3 3 5-6"/>',
        xCircle: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>',
        layers: '<path d="m12 2 10 5-10 5L2 7l10-5z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>',
        tool: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9l-3.8 3.8z"/>',
        clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
        play: '<circle cx="12" cy="12" r="10"/><path d="m10 8 6 4-6 4V8z"/>',
        sharpen: '<path d="m14 4 6 6M4 20l9.5-9.5M11 7l6 6M16 2l6 6-9 9-6-6 9-9z"/>',
        archive: '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4"/>',
        refill: '<path d="M12 2.7 5.6 9.1a9 9 0 1 0 12.8 0L12 2.7z"/>',
        list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
        briefcase: '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
        database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5M3 12c0 1.7 4 3 9 3s9-1.3 9-3"/>',
        trophy: '<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0V4zM17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3"/>',
        backspace: '<path d="M21 4H8l-7 8 7 8h13a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2z"/><path d="m18 9-6 6M12 9l6 6"/>',
        lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
        swap: '<path d="M16 3l4 4-4 4M20 7H4M8 21l-4-4 4-4M4 17h16"/>',
        eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'
    };

    function icon(name, extraClass) {
        return '<svg class="icon' + (extraClass ? ' ' + extraClass : '') + '" viewBox="0 0 24 24" aria-hidden="true">' +
            (ICON_PATHS[name] || '') + '</svg>';
    }

    // ========== Domain formatting ==========

    var STATIONS = ['active', 'sharpen', 'cabinet', 'refill'];
    var STATION_META = {
        active: { label: 'Active', icon: 'play', hint: 'In use on the floor' },
        sharpen: { label: 'Sharpen', icon: 'sharpen', hint: 'Needs sharpening' },
        cabinet: { label: 'Cabinet', icon: 'archive', hint: 'Ready for use' },
        refill: { label: 'Refill', icon: 'refill', hint: 'Needs grinding' },
        new: { label: 'New', icon: 'plus', hint: '' }
    };
    var PRIORITIES = ['low', 'medium', 'high', 'urgent', 'top'];

    function stationLabel(station) {
        return (STATION_META[station] && STATION_META[station].label) || station || '—';
    }

    function stationBadge(station, large) {
        if (station === 'new') return '<span class="badge">New</span>';
        return '<span class="badge st-' + esc(station) + (large ? ' badge-lg' : '') + '"><span class="dot"></span>' +
            esc(stationLabel(station)) + '</span>';
    }

    function capitalize(s) {
        s = s || '';
        return s.charAt(0).toUpperCase() + s.slice(1);
    }

    function priorityBadge(level) {
        level = PRIORITIES.indexOf(level) >= 0 ? level : 'low';
        return '<span class="badge pr-' + level + '">' + esc(capitalize(level)) + '</span>';
    }

    /** "S1793 · Set 1 · Cam 2" */
    function toolId(sNumber, setNo, camNo) {
        var parts = ['S' + esc(sNumber)];
        if (setNo !== undefined && setNo !== null) parts.push('Set ' + esc(setNo));
        if (camNo !== undefined && camNo !== null) parts.push('Cam ' + esc(camNo));
        return '<span class="tool-id">' + parts.join('<span class="sep">·</span>') + '</span>';
    }

    function toolIdText(sNumber, setNo, camNo) {
        return 'S' + sNumber + (setNo != null ? ' Set ' + setNo : '') + (camNo != null ? ' Cam ' + camNo : '');
    }

    function inches(value, digits) {
        if (value === null || value === undefined || isNaN(value)) return '—';
        return Number(value).toFixed(digits === undefined ? 3 : digits) + '"';
    }

    function number(value, digits) {
        if (value === null || value === undefined || isNaN(value)) return '—';
        return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits || 0, minimumFractionDigits: digits || 0 });
    }

    function lifeSeverity(pct) {
        return pct >= 90 ? 'is-danger' : pct >= 70 ? 'is-warn' : '';
    }

    function meter(pct) {
        pct = Number(pct) || 0;
        return '<div class="meter" title="' + pct.toFixed(1) + '% of material life used">' +
            '<div class="meter-track"><div class="meter-fill ' + lifeSeverity(pct) + '" style="width:' +
            Math.min(Math.max(pct, 0), 100) + '%"></div></div><span class="meter-value">' + pct.toFixed(0) + '%</span></div>';
    }

    /** Station distribution bar; counts = {active, sharpen, cabinet, refill}. */
    function stackbar(counts, large) {
        var total = STATIONS.reduce(function (sum, s) { return sum + (counts[s] || 0); }, 0);
        if (!total) return '<div class="stackbar' + (large ? ' stackbar-lg' : '') + '"></div>';
        var title = STATIONS.map(function (s) { return stationLabel(s) + ' ' + (counts[s] || 0); }).join(' · ');
        return '<div class="stackbar' + (large ? ' stackbar-lg' : '') + '" role="img" aria-label="' + esc(title) + '" title="' + esc(title) + '">' +
            STATIONS.filter(function (s) { return counts[s]; }).map(function (s) {
                return '<span class="seg-' + s + '" style="flex:' + counts[s] + '"></span>';
            }).join('') + '</div>';
    }

    function stationLegend() {
        return '<div class="legend">' + STATIONS.map(function (s) {
            return '<span><i style="background:var(--st-' + s + ')"></i>' + stationLabel(s) + '</span>';
        }).join('') + '</div>';
    }

    // ========== Dates ==========

    /** Parse API timestamps. SQLite CURRENT_TIMESTAMP values are UTC without a zone marker. */
    function parseDate(value) {
        if (!value) return null;
        if (value instanceof Date) return value;
        var s = String(value);
        if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
            var p = s.split('-');
            return new Date(+p[0], +p[1] - 1, +p[2]);
        }
        if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(s)) {
            return new Date(s.replace(' ', 'T') + 'Z');
        }
        var d = new Date(s);
        return isNaN(d) ? null : d;
    }

    function formatDateTime(value) {
        var d = parseDate(value);
        return d ? d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';
    }

    function formatDate(value) {
        var d = parseDate(value);
        return d ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '—';
    }

    function formatDay(value) {
        var d = parseDate(value);
        return d ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '—';
    }

    function timeAgo(value) {
        var d = parseDate(value);
        if (!d) return '—';
        var secs = Math.round((Date.now() - d.getTime()) / 1000);
        if (secs < 45) return 'just now';
        var mins = Math.round(secs / 60);
        if (mins < 60) return mins + 'm ago';
        var hours = Math.round(mins / 60);
        if (hours < 24) return hours + 'h ago';
        var days = Math.round(hours / 24);
        if (days < 30) return days + 'd ago';
        return formatDate(d);
    }

    function hoursLabel(hours) {
        if (hours === null || hours === undefined) return '—';
        if (hours < 1) return Math.round(hours * 60) + 'm';
        if (hours < 48) return hours.toFixed(1) + 'h';
        return (hours / 24).toFixed(1) + 'd';
    }

    // ========== Toasts ==========

    var TOAST_ICONS = { success: 'checkCircle', error: 'xCircle', warning: 'alert', info: 'info' };

    function toastRegion() {
        var region = document.getElementById('toastRegion');
        if (!region) {
            region = document.createElement('div');
            region.id = 'toastRegion';
            region.className = 'toast-region';
            region.setAttribute('role', 'status');
            region.setAttribute('aria-live', 'polite');
            document.body.appendChild(region);
        }
        return region;
    }

    /**
     * Show a toast. options: {type, detail, action: {label, onClick}, duration}
     * Returns a function that dismisses it.
     */
    function toast(message, options) {
        options = options || {};
        var type = options.type || 'info';
        var el = document.createElement('div');
        el.className = 'toast toast-' + type;
        el.innerHTML = '<span class="toast-icon">' + icon(TOAST_ICONS[type]) + '</span>' +
            '<div class="toast-body">' + esc(message) + (options.detail ? '<small>' + esc(options.detail) + '</small>' : '') + '</div>' +
            (options.action ? '<button type="button" class="toast-action">' + esc(options.action.label) + '</button>' : '') +
            '<button type="button" class="toast-close" aria-label="Dismiss">' + icon('x') + '</button>';

        var timer;
        function dismiss() {
            clearTimeout(timer);
            if (!el.parentNode) return;
            el.classList.add('is-leaving');
            setTimeout(function () { el.remove(); }, 180);
        }
        el.querySelector('.toast-close').addEventListener('click', dismiss);
        if (options.action) {
            el.querySelector('.toast-action').addEventListener('click', function () {
                dismiss();
                options.action.onClick();
            });
        }
        var region = toastRegion();
        region.appendChild(el);
        while (region.children.length > 3) region.firstChild.remove();
        timer = setTimeout(dismiss, options.duration || (type === 'error' ? 7000 : options.action ? 8000 : 4000));
        return dismiss;
    }

    // ========== Dialogs ==========

    /**
     * Open a modal dialog. options: {title, text, body (HTML), confirmLabel, cancelLabel,
     * danger, wide, onOpen(dialog), onConfirm(dialog) -> value | Promise (throw to keep open)}
     * Resolves with onConfirm's value, or null when cancelled.
     */
    function openDialog(options) {
        return new Promise(function (resolve) {
            var dialog = document.createElement('dialog');
            dialog.className = 'dialog' + (options.wide ? ' dialog-wide' : '');
            dialog.innerHTML =
                '<form method="dialog" novalidate>' +
                '<div class="dialog-body">' +
                (options.title ? '<h2 class="dialog-title">' + esc(options.title) + '</h2>' : '') +
                (options.text ? '<p class="dialog-text">' + esc(options.text) + '</p>' : '') +
                (options.body || '') +
                '<div class="dialog-error" role="alert"></div>' +
                '</div>' +
                '<div class="dialog-actions">' +
                (options.cancelLabel === false ? '' : '<button type="button" class="btn btn-lg" data-action="cancel">' + esc(options.cancelLabel || 'Cancel') + '</button>') +
                '<button type="submit" class="btn btn-lg ' + (options.danger ? 'btn-danger' : 'btn-primary') + '" data-action="confirm">' +
                esc(options.confirmLabel || 'OK') + '</button>' +
                '</div></form>';
            document.body.appendChild(dialog);

            var errorEl = dialog.querySelector('.dialog-error');
            var confirmBtn = dialog.querySelector('[data-action="confirm"]');
            var settled = false;

            function close(value) {
                if (settled) return;
                settled = true;
                dialog.close();
                dialog.remove();
                resolve(value);
            }

            dialog.querySelector('form').addEventListener('submit', function (e) {
                e.preventDefault();
                errorEl.textContent = '';
                var result;
                try {
                    result = options.onConfirm ? options.onConfirm(dialog) : true;
                } catch (err) {
                    errorEl.textContent = err.message;
                    return;
                }
                Promise.resolve(result).then(function (value) {
                    close(value === undefined ? true : value);
                }, function (err) {
                    confirmBtn.classList.remove('is-loading');
                    errorEl.textContent = err.message;
                });
                if (result && typeof result.then === 'function') confirmBtn.classList.add('is-loading');
            });
            var cancelBtn = dialog.querySelector('[data-action="cancel"]');
            if (cancelBtn) cancelBtn.addEventListener('click', function () { close(null); });
            dialog.addEventListener('cancel', function (e) { e.preventDefault(); close(null); });

            dialog.showModal();
            if (options.onOpen) options.onOpen(dialog);
            var focusTarget = dialog.querySelector('[autofocus]') || confirmBtn;
            focusTarget.focus();
        });
    }

    function confirmDialog(title, text, options) {
        options = options || {};
        return openDialog({
            title: title, text: text,
            confirmLabel: options.confirmLabel || 'Confirm',
            danger: options.danger
        }).then(function (v) { return v === true; });
    }

    /** Ask for a line of text. Resolves with the trimmed string, or null when cancelled. */
    function promptDialog(title, options) {
        options = options || {};
        var inputType = options.type || 'text';
        return openDialog({
            title: title,
            text: options.text,
            confirmLabel: options.confirmLabel || 'Save',
            body: '<div class="field"><label class="field-label" for="dialogInput">' + esc(options.label || '') + '</label>' +
                (options.multiline
                    ? '<textarea id="dialogInput" class="textarea" autofocus maxlength="' + (options.maxLength || 500) + '" placeholder="' + esc(options.placeholder || '') + '"></textarea>'
                    : '<input id="dialogInput" class="input" type="' + inputType + '" autofocus autocomplete="off" maxlength="' + (options.maxLength || 500) + '" placeholder="' + esc(options.placeholder || '') + '"' + (options.inputMode ? ' inputmode="' + options.inputMode + '"' : '') + '>') +
                '</div>',
            onOpen: function (dialog) {
                if (options.value) dialog.querySelector('#dialogInput').value = options.value;
            },
            onConfirm: function (dialog) {
                var value = dialog.querySelector('#dialogInput').value.trim();
                if (options.required !== false && !value) throw new Error(options.requiredMessage || 'This field is required');
                if (options.validate) options.validate(value);
                return value;
            }
        });
    }

    // ========== Keypad ==========

    function keypadHtml() {
        var keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'clear', '0', 'back'];
        return '<div class="keypad">' + keys.map(function (k) {
            if (k === 'clear') return '<button type="button" class="key-muted" data-key="clear">Clear</button>';
            if (k === 'back') return '<button type="button" class="key-muted" data-key="back" aria-label="Backspace">' + icon('backspace') + '</button>';
            return '<button type="button" data-key="' + k + '">' + k + '</button>';
        }).join('') + '</div>';
    }

    /**
     * Wire a keypad (and the physical keyboard while `isActive()`) to a digit buffer.
     * onChange(digits) renders; onEnter() submits. maxDigits limits length.
     */
    function bindKeypad(root, opts) {
        var digits = '';
        function set(next) {
            if (opts.accept && !opts.accept(next)) return false;
            digits = next;
            opts.onChange(digits);
            return true;
        }
        function press(key) {
            if (key === 'clear') return set('');
            if (key === 'back') return set(digits.slice(0, -1));
            if (/^[0-9]$/.test(key) && digits.length < (opts.maxDigits || 20)) return set(digits + key);
            return false;
        }
        root.querySelectorAll('[data-key]').forEach(function (btn) {
            btn.addEventListener('click', function () { press(btn.dataset.key); });
        });
        function onKey(e) {
            if (opts.isActive && !opts.isActive()) return;
            if (e.ctrlKey || e.metaKey || e.altKey) return;
            if (/^[0-9]$/.test(e.key)) { e.preventDefault(); press(e.key); }
            else if (e.key === 'Backspace') { e.preventDefault(); press('back'); }
            else if (e.key === 'Delete') { e.preventDefault(); press('clear'); }
            else if (e.key === 'Enter' && opts.onEnter) { e.preventDefault(); opts.onEnter(); }
        }
        document.addEventListener('keydown', onKey);
        opts.onChange(digits);
        return {
            get: function () { return digits; },
            reset: function () { set(''); },
            destroy: function () { document.removeEventListener('keydown', onKey); }
        };
    }

    /**
     * Touch-friendly entry of material removed, typed as thousandths (12 -> 0.012").
     * Resolves with a number of inches (0-1), or null when cancelled.
     */
    function materialDialog(toolLabel) {
        var pad;
        return openDialog({
            title: 'Material removed',
            text: toolLabel + ' — enter thousandths of an inch (e.g. 12 = 0.012").',
            confirmLabel: 'Confirm move',
            body: '<div class="keypad-display" id="materialDisplay">0.000<span class="unit">in</span></div>' + keypadHtml(),
            onOpen: function (dialog) {
                var display = dialog.querySelector('#materialDisplay');
                pad = bindKeypad(dialog, {
                    maxDigits: 4,
                    accept: function (d) { return parseInt(d || '0', 10) <= 1000; },
                    onChange: function (d) {
                        display.innerHTML = (parseInt(d || '0', 10) / 1000).toFixed(3) + '<span class="unit">in</span>';
                    },
                    onEnter: function () { dialog.querySelector('form').requestSubmit(); },
                    isActive: function () { return dialog.open; }
                });
            },
            onConfirm: function () {
                return parseInt(pad.get() || '0', 10) / 1000;
            }
        }).then(function (value) {
            if (pad) pad.destroy();
            return value === null ? null : value;
        });
    }

    // ========== Buttons & misc ==========

    function setLoading(button, loading) {
        if (!button) return;
        button.classList.toggle('is-loading', !!loading);
        button.disabled = !!loading;
    }

    function debounce(fn, wait) {
        var timeout;
        return function () {
            var args = arguments, ctx = this;
            clearTimeout(timeout);
            timeout = setTimeout(function () { fn.apply(ctx, args); }, wait);
        };
    }

    function emptyState(iconName, title, text) {
        return '<div class="empty">' + icon(iconName) + '<strong>' + esc(title) + '</strong>' +
            (text ? '<span>' + esc(text) + '</span>' : '') + '</div>';
    }

    function inlineAlert(message, type) {
        type = type || 'info';
        var name = { error: 'xCircle', warning: 'alert', success: 'checkCircle', info: 'info' }[type];
        return '<div class="inline-alert inline-alert-' + type + '">' + icon(name) + '<div>' + esc(message) + '</div></div>';
    }

    // ========== Sortable tables ==========

    /**
     * Render a sortable table into `container`.
     * columns: [{key, label, render(row) -> cell inner HTML, className, sortValue(row)}]
     * options: {rows, sortKey, ascending, rowAttrs(row) -> attribute string, empty}
     * Returns {update(rows)}.
     */
    function sortableTable(container, columns, options) {
        var state = { key: options.sortKey, asc: !!options.ascending, rows: options.rows || [] };

        function value(row, col) {
            var v = col.sortValue ? col.sortValue(row) : row[col.key];
            if (v === null || v === undefined) return -Infinity;
            return typeof v === 'string' ? v.toLowerCase() : v;
        }

        function render() {
            if (!state.rows.length) {
                container.innerHTML = options.empty || emptyState('list', 'Nothing to show yet');
                return;
            }
            var col = columns.filter(function (c) { return c.key === state.key; })[0];
            var rows = state.rows.slice();
            if (col) {
                var dir = state.asc ? 1 : -1;
                rows.sort(function (a, b) {
                    var av = value(a, col), bv = value(b, col);
                    return av < bv ? -dir : av > bv ? dir : 0;
                });
            }
            container.innerHTML = '<div class="table-wrap"><table class="table"><thead><tr>' +
                columns.map(function (c) {
                    var sortAttr = c.key && c.sortable !== false ? ' data-sort="' + c.key + '" tabindex="0"' : '';
                    var aria = c.key === state.key ? ' aria-sort="' + (state.asc ? 'ascending' : 'descending') + '"' : '';
                    return '<th class="' + (c.className || '') + '"' + sortAttr + aria + '>' + esc(c.label) + '</th>';
                }).join('') + '</tr></thead><tbody>' +
                rows.map(function (row) {
                    return '<tr' + (options.rowAttrs ? ' ' + options.rowAttrs(row) : '') + '>' + columns.map(function (c) {
                        return '<td class="' + (c.className || '') + '">' + c.render(row) + '</td>';
                    }).join('') + '</tr>';
                }).join('') + '</tbody></table></div>';

            container.querySelectorAll('th[data-sort]').forEach(function (th) {
                function sortBy() {
                    if (state.key === th.dataset.sort) state.asc = !state.asc;
                    else { state.key = th.dataset.sort; state.asc = false; }
                    render();
                }
                th.addEventListener('click', sortBy);
                th.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); sortBy(); } });
            });
            if (options.onRender) options.onRender(container);
        }

        render();
        return { update: function (rows) { state.rows = rows || []; render(); } };
    }

    // ========== Chart tooltip ==========

    var tipEl = null;
    function bindChartTips(root) {
        root.querySelectorAll('[data-tip]').forEach(function (el) {
            function show() {
                if (!tipEl) {
                    tipEl = document.createElement('div');
                    tipEl.className = 'chart-tip';
                    document.body.appendChild(tipEl);
                }
                tipEl.innerHTML = el.dataset.tip;
                var r = el.getBoundingClientRect();
                tipEl.style.left = (r.left + r.width / 2) + 'px';
                tipEl.style.top = r.top + 'px';
                tipEl.hidden = false;
            }
            function hide() { if (tipEl) tipEl.hidden = true; }
            el.addEventListener('mouseenter', show);
            el.addEventListener('focus', show);
            el.addEventListener('mouseleave', hide);
            el.addEventListener('blur', hide);
        });
    }

    global.UI = {
        esc: esc, icon: icon,
        STATIONS: STATIONS, STATION_META: STATION_META, PRIORITIES: PRIORITIES,
        stationLabel: stationLabel, stationBadge: stationBadge, priorityBadge: priorityBadge, capitalize: capitalize,
        toolId: toolId, toolIdText: toolIdText, inches: inches, number: number,
        meter: meter, lifeSeverity: lifeSeverity, stackbar: stackbar, stationLegend: stationLegend,
        parseDate: parseDate, formatDateTime: formatDateTime, formatDate: formatDate, formatDay: formatDay,
        timeAgo: timeAgo, hoursLabel: hoursLabel,
        toast: toast, openDialog: openDialog, confirmDialog: confirmDialog, promptDialog: promptDialog,
        keypadHtml: keypadHtml, bindKeypad: bindKeypad, materialDialog: materialDialog,
        setLoading: setLoading, debounce: debounce, emptyState: emptyState, inlineAlert: inlineAlert,
        sortableTable: sortableTable, bindChartTips: bindChartTips
    };
})(window);
