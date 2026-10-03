// PIN login overlay, session validation, inactivity logout and the top-bar user area.
// Pages register data loaders with Auth.onReady(fn): it runs once a session is
// valid and again after every re-login.

(function (global) {
    'use strict';

    var U = global.UI;
    var INACTIVITY_TIMEOUT_MS = 5 * 60 * 1000;
    var MAX_PIN = 20;
    var inactivityTimer = null;
    var readyCallbacks = [];
    var ready = false;
    var overlay = null;
    var pinPad = null;

    // ========== Login overlay ==========

    function buildOverlay() {
        overlay = document.createElement('div');
        overlay.className = 'login-overlay hidden';
        overlay.innerHTML =
            '<form class="login-card" novalidate>' +
            '<div class="login-head"><span class="brand-mark">' + U.icon('lock') + '</span>' +
            '<div><h2>CAM Tracking Kiosk</h2><p>Enter your PIN to sign in</p></div></div>' +
            '<div class="pin-dots" id="pinDots" aria-live="polite"></div>' +
            '<div class="login-error" id="loginError" role="alert"></div>' +
            U.keypadHtml() +
            '<button type="submit" class="btn btn-primary btn-xl btn-block" id="loginBtn" style="margin-top:0.85rem">Sign in</button>' +
            '</form>';
        document.body.appendChild(overlay);

        var dots = overlay.querySelector('#pinDots');
        var form = overlay.querySelector('form');
        pinPad = U.bindKeypad(overlay, {
            maxDigits: MAX_PIN,
            onChange: function (digits) {
                dots.innerHTML = digits.length
                    ? new Array(digits.length + 1).join('<i></i>')
                    : '<span class="placeholder">PIN</span>';
            },
            onEnter: function () { form.requestSubmit(); },
            isActive: function () { return !overlay.classList.contains('hidden'); }
        });
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            handleLogin();
        });
    }

    async function handleLogin() {
        var btn = overlay.querySelector('#loginBtn');
        var errorEl = overlay.querySelector('#loginError');
        var dots = overlay.querySelector('#pinDots');
        var pin = pinPad.get();
        if (!pin) {
            errorEl.textContent = 'Enter your PIN';
            return;
        }
        U.setLoading(btn, true);
        errorEl.textContent = '';
        try {
            var result = await global.API.login(pin);
            hideOverlay();
            onAuthenticated();
            U.toast('Welcome, ' + result.user.display_name, { type: 'success', duration: 2500 });
        } catch (err) {
            errorEl.textContent = err.message;
            dots.classList.remove('is-error');
            void dots.offsetWidth;
            dots.classList.add('is-error');
            pinPad.reset();
        } finally {
            U.setLoading(btn, false);
        }
    }

    function showOverlay() {
        stopInactivityTimer();
        ready = false;
        renderUserArea();
        global.Layout.refreshAdminLinks();
        overlay.classList.remove('hidden');
        pinPad.reset();
        overlay.querySelector('#loginError').textContent = '';
    }

    function hideOverlay() {
        overlay.classList.add('hidden');
    }

    // ========== Inactivity auto-logout ==========

    function resetInactivityTimer() {
        if (!global.Session.isLoggedIn()) return;
        clearTimeout(inactivityTimer);
        inactivityTimer = setTimeout(function () {
            global.API.logout().then(function () {
                // Back to the entry screen so the next person doesn't land in someone else's view
                global.location.href = '/static/index.html';
            });
        }, INACTIVITY_TIMEOUT_MS);
    }

    function stopInactivityTimer() {
        clearTimeout(inactivityTimer);
    }

    ['mousedown', 'mousemove', 'keydown', 'touchstart', 'scroll'].forEach(function (evt) {
        document.addEventListener(evt, resetInactivityTimer, { passive: true });
    });

    // ========== User area ==========

    function renderUserArea() {
        var area = document.getElementById('userArea');
        if (!area) return;
        var user = global.Session.user();
        if (!user || !global.Session.isLoggedIn()) {
            area.innerHTML = '';
            return;
        }
        area.innerHTML =
            '<div class="user-chip"><strong>' + U.esc(user.display_name) + '</strong><span>' + U.esc(user.role) + '</span></div>' +
            '<button type="button" class="topbar-btn" id="logoutBtn" title="Sign out">' + U.icon('logout') + '<span class="label">Sign out</span></button>';
        area.querySelector('#logoutBtn').addEventListener('click', async function () {
            await global.API.logout();
            global.location.href = '/static/index.html';
        });
    }

    // ========== Session lifecycle ==========

    function onAuthenticated() {
        ready = true;
        renderUserArea();
        global.Layout.refreshAdminLinks();
        resetInactivityTimer();
        applyPageGuard();
        if (!ready) return;
        readyCallbacks.forEach(function (fn) {
            try { fn(global.Session.user()); } catch (e) { console.error(e); }
        });
        global.dispatchEvent(new CustomEvent('userLoggedIn', { detail: global.Session.user() }));
    }

    /** Pages marked data-requires="admin" are replaced with a notice for non-admins. */
    var guarded = false;

    function applyPageGuard() {
        if (document.body.dataset.requires !== 'admin') return;
        if (global.Session.isAdmin()) {
            // The page content was replaced for a previous non-admin user
            if (guarded) global.location.reload();
            return;
        }
        guarded = true;
        ready = false;
        var main = document.querySelector('main');
        if (main) {
            main.innerHTML = '<div class="card">' + U.emptyState('lock', 'Admin access required',
                'Sign in with an admin PIN to manage jobs, tools and users.') +
                '<div class="row" style="justify-content:center"><a class="btn btn-primary btn-lg" href="/static/index.html">Back to entry</a></div></div>';
        }
    }

    async function checkSession() {
        buildOverlay();
        if (!global.Session.isLoggedIn()) {
            showOverlay();
            return;
        }
        try {
            var me = await global.API.me();
            var stored = global.Session.user() || {};
            global.Session.save(null, Object.assign({ id: me.user_id }, stored, me));
            onAuthenticated();
        } catch (e) {
            global.Session.clear();
            showOverlay();
        }
    }

    global.addEventListener('sessionExpired', function () {
        if (overlay) showOverlay();
        U.toast('Your session has expired. Please sign in again.', { type: 'warning' });
    });

    global.Auth = {
        /** Run fn now if signed in, and after every sign-in. */
        onReady: function (fn) {
            readyCallbacks.push(fn);
            if (ready) fn(global.Session.user());
        },
        user: function () { return global.Session.user(); },
        isAdmin: function () { return global.Session.isAdmin(); }
    };

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', checkSession);
    else checkSession();
})(window);
